"""Normalized product variant contract."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator

from utils.normalization import normalize_pack_size, normalize_text, normalize_url, parse_price

from .base import CatalogModel
from .enums import FieldSource, PriceVisibility


class ProductVariant(CatalogModel):
    product_url: str
    sku: str | None = None
    item_number: str | None = None
    product_code: str | None = None
    option_values: dict[str, str] = Field(default_factory=dict)
    image_urls: list[str] = Field(default_factory=list)
    price: Decimal | None = None
    currency: str | None = None
    price_visibility: PriceVisibility = PriceVisibility.UNKNOWN
    unit_pack_size: str | None = None
    availability: str | None = None
    field_provenance: dict[str, FieldSource] = Field(default_factory=dict)

    @field_validator("product_url", mode="before")
    @classmethod
    def canonicalize_product_url(cls, value: Any) -> str:
        normalized = normalize_url(value)
        if normalized is None:
            raise ValueError("product URL is required")
        return normalized

    @field_validator("sku", "item_number", "product_code", "availability", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        return normalize_text(value)

    @field_validator("option_values", mode="before")
    @classmethod
    def normalize_options(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        result: dict[str, str] = {}
        for raw_key, raw_value in dict(value).items():
            key = normalize_text(raw_key)
            item = normalize_text(raw_value)
            if key is not None and item is not None:
                result[key] = item
        return result

    @field_validator("image_urls", mode="before")
    @classmethod
    def normalize_image_urls(cls, value: Any) -> list[str]:
        if value is None:
            return []
        result: list[str] = []
        seen: set[str] = set()
        for raw_url in value:
            url = normalize_url(raw_url)
            if url is not None and url not in seen:
                seen.add(url)
                result.append(url)
        return result

    @field_validator("price", mode="before")
    @classmethod
    def normalize_amount(cls, value: Any) -> Decimal | None:
        return parse_price(value)

    @field_validator("price")
    @classmethod
    def reject_negative_price(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("price cannot be negative")
        return value

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: Any) -> str | None:
        currency = normalize_text(value)
        if currency is None:
            return None
        currency = currency.upper()
        if not re_full_currency(currency):
            raise ValueError("currency must be a three-letter ISO-style code")
        return currency

    @field_validator("unit_pack_size", mode="before")
    @classmethod
    def normalize_pack(cls, value: Any) -> str | None:
        return normalize_pack_size(value)


def re_full_currency(value: str) -> bool:
    return len(value) == 3 and value.isalpha() and value.isascii()
