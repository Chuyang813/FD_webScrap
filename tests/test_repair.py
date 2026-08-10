"""The repair loop end to end: break a page, propose, validate, keep or discard.

Provider traffic is mocked, so these tests spend no API quota. The point they
prove is that a proposal is never trusted on the model's say-so: it has to
reproduce values that are already known to be correct.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from extraction import DeterministicProductExtractor
from extraction.field_values import known_value, repairable_field
from extraction.locators import LocatorSample
from llm.openai_compatible import LLMSettings, OpenAICompatibleAdapter
from llm.repair import SelectorRepairAdvisor, build_repair_context


FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.safcodental.com/product/silkcare-reg"

SETTINGS = LLMSettings(
    provider="test",
    model="test-model",
    api_key="test-key",
    base_url="https://example.invalid/v1",
)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _reply(proposal: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(proposal)}}]}


def _advisor(proposal: dict | None = None, *, status: int = 200) -> SelectorRepairAdvisor:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": "nope"})
        return httpx.Response(200, json=_reply(proposal or {}))

    async def no_sleep(_seconds: float) -> None:
        return None

    adapter = OpenAICompatibleAdapter(
        SETTINGS, transport=httpx.MockTransport(handler), max_retries=0, sleep=no_sleep
    )
    return SelectorRepairAdvisor(SETTINGS, adapter=adapter)


BRAND = "Cranberry"


def _relocate_brand(html: str) -> str:
    """Simulate the real failure: the site moves a field somewhere new.

    The value is still on the page, just not where the extractor reads it. That is
    what makes repair possible at all, and what the validator has to confirm.
    """

    moved = html.replace('"brand"', '"manufacturerBrand"')
    return moved.replace(
        "<body", f'<body data-manufacturer="{BRAND}"', 1
    ) if "<body" in moved else f'<div data-manufacturer="{BRAND}"></div>{moved}'


# --- the failure the repair loop exists to answer ---------------------------


def test_relocating_the_field_actually_degrades_extraction() -> None:
    """Guard the premise: without this, the repair tests prove nothing."""

    intact = DeterministicProductExtractor().extract(_fixture("glove_product.html"), URL)
    assert intact.product.brand == BRAND

    broken = DeterministicProductExtractor().extract(
        _relocate_brand(_fixture("glove_product.html")), URL
    )
    assert broken.product.brand is None
    assert "brand" in broken.missing_expected_fields
    assert repairable_field("brand") == "brand"


# --- proposing and proving --------------------------------------------------


@pytest.mark.asyncio
async def test_the_correct_candidate_is_validated_and_the_wrong_one_is_not() -> None:
    """The full loop, on the pairing that occurs in production.

    Samples are the *changed* pages paired with the value the database recorded
    before the change, so a candidate proves itself against known ground truth.
    """

    broken = _relocate_brand(_fixture("glove_product.html"))
    samples = [LocatorSample(url=URL, html=broken, expected=BRAND)]

    advisor = _advisor(
        {
            "suspected_change": "brand left the Product JSON-LD node",
            "confidence": 0.7,
            "candidates": [
                {
                    "kind": "css_attribute",
                    "expression": "[data-manufacturer]",
                    "attribute": "data-manufacturer",
                    "reason": "a manufacturer attribute now carries the value",
                },
                {
                    "kind": "css_text",
                    "expression": "h1",
                    "reason": "plausible, but this is the product name",
                },
            ],
        }
    )

    diagnosis = await advisor.diagnose(
        field_name="brand", failing_html=broken, samples=samples
    )

    assert diagnosis.status == "validated"
    assert diagnosis.request_sent is True
    assert diagnosis.selectors_modified is False

    good, bad = diagnosis.candidates
    assert good.validated is True
    assert (good.matched, good.tested) == (1, 1)
    assert bad.validated is False, "a plausible guess must still be rejected"
    assert [item.locator for item in diagnosis.accepted] == [
        "[data-manufacturer] @data-manufacturer"
    ]


@pytest.mark.asyncio
async def test_a_plausible_but_wrong_candidate_is_rejected() -> None:
    """The whole point: the model can be confident and still be wrong."""

    broken = _relocate_brand(_fixture("glove_product.html"))
    samples = [LocatorSample(url=URL, html=broken, expected=BRAND)]

    advisor = _advisor(
        {
            "suspected_change": "brand is now a data attribute",
            "confidence": 0.95,
            "candidates": [
                {
                    "kind": "css_attribute",
                    "expression": "body",
                    "attribute": "data-brand-that-does-not-exist",
                    "reason": "high confidence, entirely wrong",
                }
            ],
        }
    )

    diagnosis = await advisor.diagnose(
        field_name="brand", failing_html=broken, samples=samples
    )

    assert diagnosis.status == "rejected"
    assert diagnosis.accepted == []
    assert diagnosis.candidates[0].validated is False
    assert diagnosis.model_confidence == 0.95, "confidence does not buy adoption"


@pytest.mark.asyncio
async def test_a_malformed_candidate_is_reported_not_executed() -> None:
    advisor = _advisor(
        {
            "suspected_change": "unclear",
            "confidence": 0.4,
            "candidates": [
                {"kind": "js_variable", "expression": "fetch('http://x')", "reason": "hostile"}
            ],
        }
    )
    samples = [LocatorSample(url=URL, html="<html></html>", expected="x")]

    diagnosis = await advisor.diagnose(
        field_name="brand", failing_html="<html></html>", samples=samples
    )

    assert diagnosis.candidates[0].validated is False
    assert "malformed locator" in diagnosis.candidates[0].detail


# --- refusing to answer ------------------------------------------------------


@pytest.mark.asyncio
async def test_without_known_good_samples_the_advisor_declines() -> None:
    """An unvalidated proposal is not worth recording, so none is requested."""

    advisor = _advisor({"suspected_change": "x", "confidence": 1.0, "candidates": []})

    diagnosis = await advisor.diagnose(field_name="brand", failing_html="<html></html>", samples=[])

    assert diagnosis.status == "skipped"
    assert diagnosis.request_sent is False
    assert "no known-good samples" in (diagnosis.reason or "")


@pytest.mark.asyncio
async def test_missing_configuration_skips_without_contacting_a_provider() -> None:
    advisor = SelectorRepairAdvisor(LLMSettings())
    diagnosis = await advisor.diagnose(
        field_name="brand",
        failing_html="<html></html>",
        samples=[LocatorSample(url=URL, html="<html></html>", expected="x")],
    )

    assert diagnosis.status == "skipped"
    assert diagnosis.request_sent is False


@pytest.mark.asyncio
async def test_provider_failure_is_evidence_not_an_exception() -> None:
    advisor = _advisor(status=500)
    diagnosis = await advisor.diagnose(
        field_name="brand",
        failing_html="<html></html>",
        samples=[LocatorSample(url=URL, html="<html></html>", expected="x")],
    )

    assert diagnosis.status == "failed"
    assert diagnosis.request_sent is True


# --- the prompt context ------------------------------------------------------


def test_context_sends_structure_not_a_megabyte_of_markup() -> None:
    html = _fixture("glove_product.html")
    context = build_repair_context(html, "brand", max_characters=24_000)

    assert len(context) <= 24_000
    payload = json.loads(context)
    assert payload["missing_field"] == "brand"
    assert "response_schema" in payload
    assert any("masterData" in item for item in payload["window_assignments"])
