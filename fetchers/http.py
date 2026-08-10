"""Conservative asynchronous HTTP transport with bounded retries."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable

import httpx

from .base import FetchRequest, FetchResult, utc_now

Sleep = Callable[[float], Awaitable[None]]


@dataclass(slots=True)
class FetchStats:
    requests: int = 0
    retries: int = 0

    @property
    def request_count(self) -> int:
        return self.requests

    @property
    def retry_count(self) -> int:
        return self.retries


class HttpFetcher:
    """Fetch with ``httpx.AsyncClient``, rate limiting, and safe retries.

    ``max_retries`` counts additional attempts. Only timeouts, connection
    failures, HTTP 429, and HTTP 5xx responses are retried.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        user_agent: str = "FrontierDentalTakeHomeBot/0.1",
        connect_timeout_seconds: float = 10,
        read_timeout_seconds: float = 30,
        max_retries: int = 3,
        requests_per_second: float = 1,
        max_concurrency: int = 2,
        backoff_base_seconds: float = 0.5,
        jitter_ms: int = 300,
        sleep: Sleep = asyncio.sleep,
        random_source: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least one")

        timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept": "*/*"},
        )
        self.max_retries = max_retries
        self.backoff_base_seconds = max(0, backoff_base_seconds)
        self.jitter_seconds = max(0, jitter_ms) / 1000
        self._minimum_interval = 1 / requests_per_second
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._rate_lock = asyncio.Lock()
        self._last_request_started: float | None = None
        self._sleep = sleep
        self._random = random_source
        self._clock = clock
        self.stats = FetchStats()

    @classmethod
    def from_config(
        cls,
        config: Any,
        *,
        client: httpx.AsyncClient | None = None,
        **overrides: Any,
    ) -> "HttpFetcher":
        """Build from ``AppConfig`` without coupling this module to it."""

        values = {
            "client": client,
            "user_agent": config.fetch.user_agent,
            "connect_timeout_seconds": config.fetch.connect_timeout_seconds,
            "read_timeout_seconds": config.fetch.read_timeout_seconds,
            "max_retries": config.crawler.max_retries,
            "requests_per_second": config.rate_limit.requests_per_second,
            "max_concurrency": config.rate_limit.max_concurrency,
            "jitter_ms": config.rate_limit.jitter_ms,
        }
        values.update(overrides)
        return cls(**values)

    @property
    def request_count(self) -> int:
        return self.stats.requests

    @property
    def retry_count(self) -> int:
        return self.stats.retries

    async def fetch(self, request: FetchRequest) -> FetchResult:
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._request_once(request)
            except httpx.TransportError:
                if attempt >= self.max_retries:
                    raise
                await self._wait_before_retry(attempt + 1)
                continue

            if self._is_retryable_status(response.status_code) and attempt < self.max_retries:
                await self._wait_before_retry(
                    attempt + 1,
                    retry_after=response.headers.get("Retry-After"),
                )
                continue

            body = response.text
            return FetchResult(
                url=str(response.url),
                status_code=response.status_code,
                body=body,
                content_type=response.headers.get("content-type"),
                fetched_at=utc_now(),
                source="http",
                from_cache=False,
            )

        raise AssertionError("retry loop exited unexpectedly")

    async def _request_once(self, request: FetchRequest) -> httpx.Response:
        async with self._semaphore:
            await self._wait_for_rate_limit()
            self.stats.requests += 1
            kwargs: dict[str, Any] = {"headers": request.headers}
            if request.json_body is not None:
                kwargs["json"] = request.json_body
            return await self._client.request(request.method, request.url, **kwargs)

    async def _wait_for_rate_limit(self) -> None:
        async with self._rate_lock:
            now = self._clock()
            wait_for = 0.0
            if self._last_request_started is not None:
                wait_for = max(0.0, self._last_request_started + self._minimum_interval - now)
            if wait_for:
                await self._sleep(wait_for)
                now = max(self._clock(), now + wait_for)
            self._last_request_started = now

    async def _wait_before_retry(
        self,
        retry_number: int,
        *,
        retry_after: str | None = None,
    ) -> None:
        self.stats.retries += 1
        exponential = self.backoff_base_seconds * (2 ** (retry_number - 1))
        requested = self._retry_after_seconds(retry_after)
        delay = max(exponential, requested or 0) + self._random() * self.jitter_seconds
        await self._sleep(delay)

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return min(60.0, max(0.0, float(value)))
        except ValueError:
            try:
                target = parsedate_to_datetime(value)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=UTC)
                return min(60.0, max(0.0, (target - datetime.now(UTC)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                return None

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code == 429 or 500 <= status_code <= 599

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "HttpFetcher":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
