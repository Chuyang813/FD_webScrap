"""Normalized product contract."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field, field_validator

from utils.normalization import normalize_text, normalize_url

from .base import CatalogModel, utc_now
from .enums import FieldSource, PriceVisibility


class Product(CatalogModel):
    name: str
    brand: str | None = None
    category_path: list[str] = Field(default_factory=list)
    product_url: str
    description: str | None = None
    specifications: dict[str, str] = Field(default_factory=dict)
    image_urls: list[str] = Field(default_factory=list)
    alternative_product_urls: list[str] = Field(default_factory=list)
    price_visibility: PriceVisibility = PriceVisibility.UNKNOWN
    field_provenance: dict[str, FieldSource] = Field(default_factory=dict)
    scraped_at: datetime = Field(default_factory=utc_now)
    content_hash: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def normalize_required_text(cls, value: Any) -> str:
        normalized = normalize_text(value)
        if normalized is None:
            raise ValueError("product name cannot be blank")
        return normalized

    @field_validator("brand", "description", "content_hash", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        return normalize_text(value)

    @field_validator("product_url", mode="before")
    @classmethod
    def canonicalize_product_url(cls, value: Any) -> str:
        normalized = normalize_url(value)
        if normalized is None:
            raise ValueError("product URL is required")
        return normalized

    @field_validator("category_path", mode="before")
    @classmethod
    def normalize_categories(cls, value: Any) -> list[str]:
        if value is None:
            return []
        categories = [normalize_text(item) for item in value]
        return [item for item in categories if item is not None]

    @field_validator("specifications", mode="before")
    @classmethod
    def normalize_specifications(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        result: dict[str, str] = {}
        for raw_key, raw_value in dict(value).items():
            key = normalize_text(raw_key)
            item = normalize_text(raw_value)
            if key is not None and item is not None:
                result[key] = item
        return result

    @field_validator("image_urls", "alternative_product_urls", mode="before")
    @classmethod
    def normalize_url_lists(cls, value: Any) -> list[str]:
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

    @field_validator("scraped_at")
    @classmethod
    def ensure_timezone(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
