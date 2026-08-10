"""Recovery diagnostics that record repair ideas without changing selectors."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents.validator import ValidationReport
from extraction.comparator import ProductAgreement
from models import ExtractionResult
from utils.normalization import normalize_text


LOGGER = logging.getLogger(__name__)


class RepairSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suspected_change: str
    candidate_selectors: list[str] = Field(default_factory=list)
    reason: str
    confidence: float = Field(ge=0, le=1)
    source: Literal["rule", "llm", "operator"] = "rule"
    human_review_required: Literal[True] = True

    @field_validator("suspected_change", "reason", mode="before")
    @classmethod
    def required_text(cls, value: object) -> str:
        result = normalize_text(value)
        if not result:
            raise ValueError("repair suggestion text cannot be blank")
        return result

    @field_validator("candidate_selectors", mode="before")
    @classmethod
    def selectors_are_distinct(cls, value: object) -> list[str]:
        result: list[str] = []
        for raw in value or []:  # type: ignore[union-attr]
            selector = normalize_text(raw)
            if selector and selector not in result:
                result.append(selector)
        return result


class RecoveryTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["missing_field", "validation_failure", "agreement_drop"]
    detail: str


class RecoveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_url: str
    triggers: list[RecoveryTrigger] = Field(default_factory=list)
    suggestions: list[RepairSuggestion] = Field(default_factory=list)
    disposition: Literal["no_action", "recorded_for_review"]
    selectors_modified: Literal[False] = False
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RecoveryAgent:
    """Create auditable records; selector adoption is deliberately out of scope."""

    def __init__(self, *, agreement_threshold: float = 0.80) -> None:
        if not 0 <= agreement_threshold <= 1:
            raise ValueError("agreement_threshold must be between zero and one")
        self.agreement_threshold = agreement_threshold

    def assess(
        self,
        extraction: ExtractionResult,
        *,
        validation: ValidationReport | None = None,
        agreement: ProductAgreement | None = None,
        suggestions: list[RepairSuggestion] | None = None,
    ) -> RecoveryRecord:
        triggers = [
            RecoveryTrigger(kind="missing_field", detail=field)
            for field in extraction.missing_expected_fields
        ]
        if validation and not validation.valid:
            triggers.extend(
                RecoveryTrigger(kind="validation_failure", detail=error)
                for error in validation.errors
            )
        if agreement and agreement.overall_agreement < self.agreement_threshold:
            triggers.append(
                RecoveryTrigger(
                    kind="agreement_drop",
                    detail=(
                        f"cross-extractor agreement {agreement.overall_agreement:.3f} "
                        f"below {self.agreement_threshold:.3f}"
                    ),
                )
            )
        recorded_suggestions = list(suggestions or [])
        needs_review = bool(triggers or recorded_suggestions)
        record = RecoveryRecord(
            product_url=extraction.product.product_url,
            triggers=triggers,
            suggestions=recorded_suggestions,
            disposition="recorded_for_review" if needs_review else "no_action",
        )
        if needs_review:
            LOGGER.warning(
                "repair_suggested",
                extra={
                    "url": record.product_url,
                    "trigger_count": len(triggers),
                    "suggestion_count": len(recorded_suggestions),
                    "selectors_modified": False,
                },
            )
        return record

    def record_suggestion(
        self,
        extraction: ExtractionResult,
        suggestion: RepairSuggestion,
        *,
        validation: ValidationReport | None = None,
        agreement: ProductAgreement | None = None,
    ) -> RecoveryRecord:
        return self.assess(
            extraction,
            validation=validation,
            agreement=agreement,
            suggestions=[suggestion],
        )


SelectorRepairSuggestion = RepairSuggestion
