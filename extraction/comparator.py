"""Field-level cross-extractor agreement reporting for drift signals."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from difflib import SequenceMatcher
from statistics import fmean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from models import ExtractionResult, ProductVariant
from utils.identity import variant_identity
from utils.normalization import normalize_text


AgreementMethod = Literal["normalized_exact", "numeric_tolerance", "set", "structured", "text_similarity"]


class FieldAgreement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    method: AgreementMethod
    agreed: bool
    score: float = Field(ge=0, le=1)
    deterministic_value: Any = None
    shadow_value: Any = None


class ProductAgreement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terminology: Literal["cross-extractor agreement"] = "cross-extractor agreement"
    product_url: str
    overall_agreement: float = Field(ge=0, le=1)
    field_agreement: dict[str, float]
    comparisons: list[FieldAgreement]


class CrossExtractorAgreementReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terminology: Literal["cross-extractor agreement"] = "cross-extractor agreement"
    sample_size: int = Field(ge=0)
    overall_agreement: float = Field(ge=0, le=1)
    field_agreement: dict[str, float]
    products: list[ProductAgreement] = Field(default_factory=list)


def _text(value: Any) -> str | None:
    normalized = normalize_text(value)
    return normalized.casefold() if normalized else None


def _variant_key(variant: ProductVariant, index: int) -> str:
    return variant_identity(variant)


def _variant_map(result: ExtractionResult) -> dict[str, ProductVariant]:
    return {_variant_key(variant, index): variant for index, variant in enumerate(result.variants)}


def _jaccard(left: set[Any], right: set[Any]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def _structured_score(left: dict[Any, Any], right: dict[Any, Any]) -> float:
    keys = set(left) | set(right)
    if not keys:
        return 1.0
    return sum(_text(left.get(key)) == _text(right.get(key)) for key in keys) / len(keys)


class ExtractionComparator:
    def __init__(self, *, price_tolerance: Decimal = Decimal("0.01"), text_threshold: float = 0.90) -> None:
        self.price_tolerance = price_tolerance
        self.text_threshold = text_threshold

    def compare(self, deterministic: ExtractionResult, shadow: ExtractionResult) -> ProductAgreement:
        comparisons: list[FieldAgreement] = []

        def add(field: str, method: AgreementMethod, score: float, left: Any, right: Any) -> None:
            threshold = self.text_threshold if method == "text_similarity" else 1.0
            comparisons.append(
                FieldAgreement(
                    field=field,
                    method=method,
                    agreed=score >= threshold,
                    score=score,
                    deterministic_value=left,
                    shadow_value=right,
                )
            )

        for field in ("name", "brand", "product_url", "price_visibility"):
            left = getattr(deterministic.product, field)
            right = getattr(shadow.product, field)
            add(field, "normalized_exact", float(_text(left) == _text(right)), left, right)

        left_categories = [_text(value) for value in deterministic.product.category_path]
        right_categories = [_text(value) for value in shadow.product.category_path]
        add(
            "category_path",
            "normalized_exact",
            float(left_categories == right_categories),
            left_categories,
            right_categories,
        )

        left_description = _text(deterministic.product.description) or ""
        right_description = _text(shadow.product.description) or ""
        description_score = SequenceMatcher(None, left_description, right_description).ratio()
        add(
            "description",
            "text_similarity",
            description_score,
            deterministic.product.description,
            shadow.product.description,
        )

        add(
            "specifications",
            "structured",
            _structured_score(deterministic.product.specifications, shadow.product.specifications),
            deterministic.product.specifications,
            shadow.product.specifications,
        )
        for field in ("image_urls", "alternative_product_urls"):
            left = {_text(value) for value in getattr(deterministic.product, field)}
            right = {_text(value) for value in getattr(shadow.product, field)}
            add(field, "set", _jaccard(left, right), sorted(left), sorted(right))

        left_variants = _variant_map(deterministic)
        right_variants = _variant_map(shadow)
        for field in ("sku", "item_number", "product_code"):
            left = {_text(getattr(value, field)) for value in left_variants.values() if getattr(value, field)}
            right = {_text(getattr(value, field)) for value in right_variants.values() if getattr(value, field)}
            add(field, "set", _jaccard(left, right), sorted(left), sorted(right))

        variant_keys = set(left_variants) | set(right_variants)
        for field in ("currency", "unit_pack_size", "availability", "price_visibility"):
            left = {key: _text(getattr(left_variants[key], field)) for key in left_variants}
            right = {key: _text(getattr(right_variants[key], field)) for key in right_variants}
            score = (
                sum(
                    key in left and key in right and left[key] == right[key]
                    for key in variant_keys
                )
                / len(variant_keys)
                if variant_keys
                else 1.0
            )
            add(field, "normalized_exact", score, left, right)

        left_options = {key: value.option_values for key, value in left_variants.items()}
        right_options = {key: value.option_values for key, value in right_variants.items()}
        option_scores = [
            _structured_score(left_options.get(key, {}), right_options.get(key, {}))
            if key in left_options and key in right_options
            else 0.0
            for key in variant_keys
        ]
        add(
            "option_values",
            "structured",
            fmean(option_scores) if option_scores else 1.0,
            left_options,
            right_options,
        )

        price_scores: list[float] = []
        left_prices = {key: value.price for key, value in left_variants.items()}
        right_prices = {key: value.price for key, value in right_variants.items()}
        for key in variant_keys:
            left = left_prices.get(key)
            right = right_prices.get(key)
            if left is None or right is None:
                price_scores.append(float(left is None and right is None and key in left_prices and key in right_prices))
            else:
                price_scores.append(float(abs(left - right) <= self.price_tolerance))
        add(
            "price",
            "numeric_tolerance",
            fmean(price_scores) if price_scores else 1.0,
            left_prices,
            right_prices,
        )

        left_variant_images = {
            key: {_text(url) for url in value.image_urls}
            for key, value in left_variants.items()
        }
        right_variant_images = {
            key: {_text(url) for url in value.image_urls}
            for key, value in right_variants.items()
        }
        variant_image_scores = [
            _jaccard(left_variant_images[key], right_variant_images[key])
            if key in left_variant_images and key in right_variant_images
            else 0.0
            for key in variant_keys
        ]
        add(
            "variant_image_urls",
            "set",
            fmean(variant_image_scores) if variant_image_scores else 1.0,
            left_variant_images,
            right_variant_images,
        )

        fields = {item.field: item.score for item in comparisons}
        return ProductAgreement(
            product_url=deterministic.product.product_url,
            overall_agreement=fmean(fields.values()) if fields else 1.0,
            field_agreement=fields,
            comparisons=comparisons,
        )

    def summarize(
        self,
        pairs: Iterable[tuple[ExtractionResult, ExtractionResult]],
    ) -> CrossExtractorAgreementReport:
        products = [self.compare(left, right) for left, right in pairs]
        field_names = sorted({field for product in products for field in product.field_agreement})
        fields = {
            field: fmean(product.field_agreement[field] for product in products if field in product.field_agreement)
            for field in field_names
        }
        return CrossExtractorAgreementReport(
            sample_size=len(products),
            overall_agreement=fmean(product.overall_agreement for product in products) if products else 1.0,
            field_agreement=fields,
            products=products,
        )


def compare_results(deterministic: ExtractionResult, shadow: ExtractionResult) -> ProductAgreement:
    return ExtractionComparator().compare(deterministic, shadow)
