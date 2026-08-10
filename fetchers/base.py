"""Fetcher contracts shared by HTTP, cache, and agents."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class FetchRequest(BaseModel):
    """A deterministic HTTP request supported by the crawler."""

    model_config = ConfigDict(extra="forbid")

    url: str
    method: str = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: Any | None = None
    force_refresh: bool = False

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, value: Any) -> str:
        method = str(value).upper()
        if method not in {"GET", "POST"}:
            raise ValueError("only GET and POST requests are supported")
        return method

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must be absolute HTTP(S)")
        return value


class FetchResult(BaseModel):
    """Transport-independent response returned to crawler agents."""

    model_config = ConfigDict(extra="forbid")

    url: str
    status_code: int = Field(ge=0, le=599)
    body: str
    content_type: str | None = None
    fetched_at: datetime = Field(default_factory=utc_now)
    source: str
    from_cache: bool = False
    content_hash: str = ""

    @field_validator("fetched_at")
    @classmethod
    def make_fetched_at_aware(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    def model_post_init(self, __context: Any) -> None:
        if not self.content_hash:
            self.content_hash = hashlib.sha256(self.body.encode("utf-8")).hexdigest()


@runtime_checkable
class Fetcher(Protocol):
    async def fetch(self, request: FetchRequest) -> FetchResult:
        """Fetch one request and return its decoded body plus metadata."""


class FetchError(RuntimeError):
    """Base error for fetch-layer failures."""


class OfflineCacheMissError(FetchError):
    """Raised when offline mode cannot satisfy a request from disk."""

