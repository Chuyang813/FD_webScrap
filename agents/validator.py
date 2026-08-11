"""Independent normalization, validation, and variant deduplication."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from extraction.hashing import normalized_content_hash
from models import ExtractionResult, ProductVariant
from utils.identity import variant_identity
from utils.normalization import normalize_text


_MARKUP = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
_SKU = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,127}$")


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    result: ExtractionResult | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duplicate_variants_removed: int = 0

    def require_valid(self) -> ExtractionResult:
        if not self.valid or self.result is None:
            raise ValueError("; ".join(self.errors) or "extraction result is invalid")
        return self.result


def _plain_text(value: Any) -> Any:
    if not isinstance(value, str) or not _MARKUP.search(value):
        return value
    return normalize_text(BeautifulSoup(value, "html.parser").get_text(" ", strip=True))


def _sanitize_payload(candidate: ExtractionResult | Mapping[str, Any]) -> dict[str, Any]:
    payload = (
        candidate.model_dump(mode="python")
        if isinstance(candidate, ExtractionResult)
        else dict(candidate)
    )
    product = dict(payload.get("product") or {})
    product["description"] = _plain_text(product.get("description"))
    product["specifications"] = {
        _plain_text(key): _plain_text(value)
        for key, value in dict(product.get("specifications") or {}).items()
        if _plain_text(key) and _plain_text(value)
    }
    payload["product"] = product
    variants: list[dict[str, Any]] = []
    for raw in payload.get("variants") or []:
        item = raw.model_dump(mode="python") if isinstance(raw, ProductVariant) else dict(raw)
        item["availability"] = _plain_text(item.get("availability"))
        item["option_values"] = {
            _plain_text(key): _plain_text(value)
            for key, value in dict(item.get("option_values") or {}).items()
            if _plain_text(key) and _plain_text(value)
        }
        variants.append(item)
    payload["variants"] = variants
    return payload


def _variant_identity(variant: ProductVariant) -> str:
    return variant_identity(variant)


class ValidatorAgent:
    """Apply the same contract to deterministic and shadow extractions."""

    def validate(self, candidate: ExtractionResult | Mapping[str, Any]) -> ValidationReport:
        try:
            normalized = ExtractionResult.model_validate(_sanitize_payload(candidate))
        except (ValidationError, TypeError, ValueError) as exc:
            return ValidationReport(valid=False, errors=[str(exc)])

        errors: list[str] = []
        warnings = list(normalized.warnings)
        if _MARKUP.search(normalized.product.name):
            errors.append("product name contains markup")
        empty_grouped_snapshot = (
            normalized.variants_complete
            and normalized.method_summary.get("variants") == "embedded_state_empty_grouped"
        )
        if not normalized.variants and not empty_grouped_snapshot:
            errors.append("at least one product variant is required")

        unique: list[ProductVariant] = []
        seen: dict[str, ProductVariant] = {}
        duplicate_count = 0
        for index, variant in enumerate(normalized.variants):
            if variant.product_url != normalized.product.product_url:
                errors.append(f"variant[{index}] product_url does not match product")
            for field in ("sku", "item_number", "product_code"):
                value = getattr(variant, field)
                if value and _MARKUP.search(value):
                    errors.append(f"variant[{index}] {field} contains markup")
            if variant.sku and not _SKU.fullmatch(variant.sku):
                errors.append(f"variant[{index}] sku has an unreasonable format")
            identity = _variant_identity(variant)
            if identity in seen:
                duplicate_count += 1
                if seen[identity].model_dump() != variant.model_dump():
                    warnings.append(f"conflicting duplicate variant removed: {identity}")
                else:
                    warnings.append(f"duplicate variant removed: {identity}")
                continue
            seen[identity] = variant
            unique.append(variant)

        normalized.variants = unique
        normalized.warnings = list(dict.fromkeys(warnings))
        normalized.product.content_hash = normalized_content_hash(normalized.product, unique)
        return ValidationReport(
            valid=not errors,
            result=normalized,
            errors=errors,
            warnings=normalized.warnings,
            duplicate_variants_removed=duplicate_count,
        )

    def normalize_and_validate(
        self,
        candidate: ExtractionResult | Mapping[str, Any],
    ) -> ExtractionResult:
        return self.validate(candidate).require_valid()


ProductValidator = ValidatorAgent


def validate_extraction(candidate: ExtractionResult | Mapping[str, Any]) -> ValidationReport:
    return ValidatorAgent().validate(candidate)
