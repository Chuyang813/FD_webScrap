"""Public domain model API."""

from .crawl_state import CrawlError, CrawlState
from .enums import CrawlStatus, FieldSource, PageType, PriceVisibility
from .extraction import ExtractionResult, ShadowProductExtraction
from .product import Product
from .variant import ProductVariant

__all__ = [
    "CrawlError",
    "CrawlState",
    "CrawlStatus",
    "ExtractionResult",
    "FieldSource",
    "PageType",
    "PriceVisibility",
    "Product",
    "ProductVariant",
    "ShadowProductExtraction",
]
