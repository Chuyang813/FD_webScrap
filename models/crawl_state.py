"""Durable crawl state and error records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field, field_validator

from utils.normalization import normalize_text, normalize_url

from .base import CatalogModel, utc_now
from .enums import CrawlStatus, PageType


class CrawlState(CatalogModel):
    url: str
    page_type: PageType = PageType.UNKNOWN
    status: CrawlStatus = CrawlStatus.PENDING
    attempt_count: int = Field(default=0, ge=0)
    last_error: str | None = None
    last_fetched_at: datetime | None = None
    content_hash: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("url", mode="before")
    @classmethod
    def canonicalize_url(cls, value: Any) -> str:
        normalized = normalize_url(value)
        if normalized is None:
            raise ValueError("crawl URL is required")
        return normalized

    @field_validator("last_error", "content_hash", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        return normalize_text(value)

    @field_validator("last_fetched_at", "updated_at")
    @classmethod
    def ensure_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class CrawlError(CatalogModel):
    id: int | None = None
    url: str
    error_type: str
    error_message: str
    attempt_number: int = Field(ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("url", mode="before")
    @classmethod
    def canonicalize_url(cls, value: Any) -> str:
        normalized = normalize_url(value)
        if normalized is None:
            raise ValueError("error URL is required")
        return normalized

    @field_validator("error_type", "error_message", mode="before")
    @classmethod
    def normalize_required_text(cls, value: Any) -> str:
        normalized = normalize_text(value)
        if normalized is None:
            raise ValueError("error fields cannot be blank")
        return normalized

    @field_validator("created_at")
    @classmethod
    def ensure_timezone(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
