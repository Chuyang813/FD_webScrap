"""Demonstrate the selector repair loop against a real provider.

A site layout change is simulated by moving a field out of the structure the
extractor reads, while leaving the value elsewhere on the page. The advisor is
then asked where it went, and every candidate it proposes is checked against the
value the catalogue already knows before anything is recorded.

    python demo_repair.py

Costs one provider request. Nothing in the extraction code is modified.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from extraction import DeterministicProductExtractor
from extraction.locators import LocatorSample
from llm.openai_compatible import LLMSettings
from llm.repair import SelectorRepairAdvisor


FIXTURE = Path("tests/fixtures/glove_product.html")
URL = "https://www.safcodental.com/product/silkcare-reg"
FIELD = "brand"


def relocate_field(html: str, value: str) -> str:
    """Move the field somewhere new, as a real site change would."""

    moved = html.replace('"brand"', '"manufacturerBrand"')
    return moved.replace("<body", f'<body data-manufacturer="{value}"', 1)


def rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "-" * 66)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=str(FIXTURE))
    args = parser.parse_args()

    load_dotenv()
    settings = LLMSettings.from_env()
    if not settings.configured:
        print(
            "No provider configured. Set LLM_PROVIDER, LLM_MODEL, LLM_API_KEY and\n"
            "LLM_BASE_URL in .env, then rerun.",
            file=sys.stderr,
        )
        return 1

    html = Path(args.fixture).read_text(encoding="utf-8")
    extractor = DeterministicProductExtractor()

    rule("1. Before the change")
    intact = extractor.extract(html, URL)
    known = intact.product.brand
    print(f"   {FIELD} extracted deterministically: {known!r}")
    print(f"   source: {intact.product.field_provenance.get(FIELD)}")
    if not known:
        print("   fixture has no brand; cannot demonstrate", file=sys.stderr)
        return 1

    rule("2. The site changes")
    broken = relocate_field(html, known)
    degraded = extractor.extract(broken, URL)
    print(f"   {FIELD} is now: {degraded.product.brand!r}")
    print(f"   reported missing: {degraded.missing_expected_fields}")
    print("   the value is still on the page, just not where the extractor reads it")

    rule("3. Ask where it went")
    print(f"   provider: {settings.provider} · model: {settings.model}")
    advisor = SelectorRepairAdvisor(settings)
    samples = [LocatorSample(url=URL, html=broken, expected=known)]
    print(f"   validating against {len(samples)} page(s) whose correct value is known")

    diagnosis = await advisor.diagnose(
        field_name=FIELD, failing_html=broken, samples=samples
    )

    rule("4. What the model proposed, and what survived validation")
    print(f"   status: {diagnosis.status}")
    if diagnosis.reason:
        print(f"   reason: {diagnosis.reason}")
    if diagnosis.suspected_change:
        print(f"   diagnosis: {diagnosis.suspected_change}")
    if diagnosis.model_confidence is not None:
        print(f"   model confidence: {diagnosis.model_confidence}")
    print()
    for candidate in diagnosis.candidates:
        mark = "\033[32mVALIDATED\033[0m" if candidate.validated else "\033[31mrejected \033[0m"
        print(f"   {mark}  {candidate.locator}")
        print(f"              {candidate.detail}")
        if candidate.reason:
            print(f"              model said: {candidate.reason}")
    if not diagnosis.candidates:
        print("   no candidates returned")

    rule("5. What happened to the code")
    print(f"   selectors modified: {diagnosis.selectors_modified}")
    print("   validated candidates are recorded for human review; adoption stays a")
    print("   human decision. Model confidence does not buy adoption - only")
    print("   reproducing values that are already known to be correct does.")
    print()
    return 0 if diagnosis.status in {"validated", "rejected"} else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
