"""Ask a model where a missing field moved to, then prove or discard the answer.

The model's output is a hypothesis, never a change. Every candidate it proposes is
evaluated against pages whose correct value is already stored, and only candidates
that reproduce all of them are recorded for human review. Nothing here edits
extraction code.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from extraction.locators import (
    Locator,
    LocatorError,
    LocatorKind,
    LocatorSample,
    ValidationOutcome,
    validate_locator,
)

from .openai_compatible import LLMQuotaExhaustedError, LLMSettings, OpenAICompatibleAdapter


class CandidateLocator(BaseModel):
    """One proposal, in the shape the validator can execute."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["css_text", "css_attribute", "js_variable"]
    expression: str
    attribute: str | None = None
    path: str | None = None
    reason: str = ""

    def to_locator(self) -> Locator:
        return Locator(
            kind=LocatorKind(self.kind),
            expression=self.expression,
            attribute=self.attribute,
            path=self.path,
        )


class RepairProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suspected_change: str
    candidates: list[CandidateLocator] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)


class ValidatedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locator: str
    kind: str
    reason: str
    validated: bool
    tested: int
    matched: int
    detail: str


class RepairDiagnosis(BaseModel):
    """The full audit record of one repair attempt."""

    model_config = ConfigDict(extra="forbid")

    field_name: str
    status: Literal["validated", "rejected", "skipped", "failed"]
    reason: str | None = None
    suspected_change: str | None = None
    model_confidence: float | None = None
    request_sent: bool = False
    candidates: list[ValidatedCandidate] = Field(default_factory=list)
    selectors_modified: Literal[False] = False

    @property
    def accepted(self) -> list[ValidatedCandidate]:
        return [item for item in self.candidates if item.validated]


_SCHEMA_HINT = json.dumps(RepairProposal.model_json_schema(), separators=(",", ":"))

_SYSTEM = (
    "A web scraper can no longer find one field on a page whose layout changed. "
    "Given the page and the field, propose where the value now lives.\n\n"
    "Return locators the scraper can evaluate mechanically:\n"
    "  css_text       - text content of the elements a CSS selector matches\n"
    "  css_attribute  - a named attribute of those elements\n"
    "  js_variable    - a window.<name> assignment, optionally with a dotted path "
    "(use * to fan out across a collection)\n\n"
    "Propose at most four candidates, most likely first. Base them only on markup "
    "present in the supplied page. Do not guess values; propose locations."
)


def build_repair_context(html: str, field_name: str, *, max_characters: int = 24_000) -> str:
    """Send structure, not a megabyte of markup.

    Scripts, styles, and the deep body text are stripped; what remains is the tag
    skeleton with identifying attributes plus any window.* assignments, which is
    where a moved value actually shows up.
    """

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["style", "noscript", "svg", "path"]):
        tag.decompose()

    assignments: list[str] = []
    for script in soup("script"):
        text = script.string or ""
        for match in re.finditer(r"\bwindow\.([A-Za-z_$][\w$]*)\s*=", text):
            snippet = text[match.start() : match.start() + 320].replace("\n", " ")
            assignments.append(snippet)
        script.decompose()

    skeleton: list[str] = []
    for element in soup.find_all(True):
        attrs = {
            key: value
            for key, value in element.attrs.items()
            if key in {"id", "class", "itemprop", "data-sku", "data-price-amount"}
            or key.startswith("data-")
        }
        if not attrs:
            continue
        rendered = " ".join(
            f'{key}="{" ".join(value) if isinstance(value, list) else value}"'
            for key, value in list(attrs.items())[:4]
        )
        text = element.get_text(" ", strip=True)[:80]
        skeleton.append(f"<{element.name} {rendered}> {text}")

    context = {
        "missing_field": field_name,
        "window_assignments": assignments[:20],
        "elements": skeleton[:400],
        "response_schema": json.loads(_SCHEMA_HINT),
    }
    rendered = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    return rendered if len(rendered) <= max_characters else rendered[:max_characters]


class SelectorRepairAdvisor:
    """Propose candidate locators, then keep only those that prove themselves."""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        *,
        adapter: OpenAICompatibleAdapter | None = None,
        max_candidates: int = 4,
    ) -> None:
        self.settings = settings or LLMSettings.from_env()
        self.adapter = adapter or OpenAICompatibleAdapter(self.settings)
        self.max_candidates = max_candidates
        self.quota_exhausted = False

    async def _propose(self, context: str) -> RepairProposal:
        payload = self.adapter.request_payload(context=context)
        payload["messages"][0]["content"] = _SYSTEM
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "selector_repair_proposal",
                "strict": True,
                "schema": RepairProposal.model_json_schema(),
            },
        }
        raw = await self.adapter.post_for_content(payload)
        return RepairProposal.model_validate_json(raw)

    async def diagnose(
        self,
        *,
        field_name: str,
        failing_html: str,
        samples: list[LocatorSample],
    ) -> RepairDiagnosis:
        if not self.settings.configured:
            return RepairDiagnosis(
                field_name=field_name,
                status="skipped",
                reason="missing configuration: " + ", ".join(self.settings.missing_configuration),
            )
        if self.quota_exhausted:
            return RepairDiagnosis(
                field_name=field_name,
                status="skipped",
                reason="provider quota exhausted earlier in this run",
            )
        if not samples:
            # Without known-good pages a proposal cannot be checked, and an
            # unchecked proposal is not worth recording.
            return RepairDiagnosis(
                field_name=field_name,
                status="skipped",
                reason="no known-good samples available to validate against",
            )

        context = build_repair_context(failing_html, field_name)
        try:
            proposal = await self._propose(context)
        except LLMQuotaExhaustedError as exc:
            self.quota_exhausted = True
            return RepairDiagnosis(
                field_name=field_name, status="failed", reason=str(exc), request_sent=True
            )
        except (ValidationError, ValueError) as exc:
            return RepairDiagnosis(
                field_name=field_name,
                status="failed",
                reason=f"unusable proposal: {type(exc).__name__}: {exc}",
                request_sent=True,
            )
        except Exception as exc:
            return RepairDiagnosis(
                field_name=field_name,
                status="failed",
                reason=f"{type(exc).__name__}: {exc}",
                request_sent=True,
            )

        results: list[ValidatedCandidate] = []
        for candidate in proposal.candidates[: self.max_candidates]:
            try:
                locator = candidate.to_locator()
            except LocatorError as exc:
                results.append(
                    ValidatedCandidate(
                        locator=candidate.expression,
                        kind=candidate.kind,
                        reason=candidate.reason,
                        validated=False,
                        tested=0,
                        matched=0,
                        detail=f"malformed locator: {exc}",
                    )
                )
                continue
            outcome: ValidationOutcome = validate_locator(locator, samples)
            results.append(
                ValidatedCandidate(
                    locator=locator.describe(),
                    kind=candidate.kind,
                    reason=candidate.reason,
                    validated=outcome.validated,
                    tested=outcome.tested,
                    matched=outcome.matched,
                    detail=outcome.summary(),
                )
            )

        return RepairDiagnosis(
            field_name=field_name,
            status="validated" if any(item.validated for item in results) else "rejected",
            suspected_change=proposal.suspected_change,
            model_confidence=proposal.confidence,
            request_sent=True,
            candidates=results,
        )
