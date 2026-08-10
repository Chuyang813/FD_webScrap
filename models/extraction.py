"""Extractor output shared by deterministic and LLM paths."""

from pydantic import Field

from .base import CatalogModel
from .product import Product
from .variant import ProductVariant


class ExtractionResult(CatalogModel):
    product: Product
    variants: list[ProductVariant] = Field(default_factory=list)
    variants_complete: bool = True
    warnings: list[str] = Field(default_factory=list)
    missing_expected_fields: list[str] = Field(default_factory=list)
    method_summary: dict[str, str] = Field(default_factory=dict)


ShadowProductExtraction = ExtractionResult
