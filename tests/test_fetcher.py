from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from fetchers import (
    CachedFetcher,
    FetchRequest,
    FetchResult,
    HttpFetcher,
    OfflineCacheMissError,
)


@pytest.mark.asyncio
async def test_http_fetcher_retries_only_retryable_statuses() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        status = 503 if attempts == 1 else 200
        return httpx.Response(status, text="ok", request=request)

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = HttpFetcher(
        client=client,
        max_retries=2,
        requests_per_second=1e9,
        backoff_base_seconds=0.25,
        jitter_ms=100,
        random_source=lambda: 0.5,
        sleep=fake_sleep,
    )
    result = await fetcher.fetch(FetchRequest(url="https://example.test/page"))

    assert result.status_code == 200
    assert fetcher.request_count == 2
    assert fetcher.retry_count == 1
    assert any(delay == pytest.approx(0.30) for delay in sleeps)
    await client.aclose()


@pytest.mark.asyncio
async def test_http_fetcher_does_not_retry_client_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404, text="missing", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = HttpFetcher(client=client, max_retries=3, requests_per_second=1e9)
    result = await fetcher.fetch(FetchRequest(url="https://example.test/missing"))

    assert result.status_code == 404
    assert attempts == 1
    assert fetcher.retry_count == 0
    await client.aclose()


def test_fetch_request_allows_only_get_and_post() -> None:
    request = FetchRequest(
        url="https://example.test/query",
        method="post",
        json_body={"b": 2, "a": 1},
    )
    assert request.method == "POST"
    with pytest.raises(ValidationError):
        FetchRequest(url="https://example.test/", method="DELETE")


@pytest.mark.asyncio
async def test_cached_fetcher_hashes_sensitive_headers_without_persisting_them(tmp_path) -> None:
    class StubFetcher:
        calls = 0

        async def fetch(self, request: FetchRequest) -> FetchResult:
            self.calls += 1
            return FetchResult(
                url=request.url,
                status_code=200,
                body="payload",
                content_type="text/plain",
                source="stub",
            )

    inner = StubFetcher()
    cached = CachedFetcher(inner, tmp_path, ttl_hours=1)
    first = FetchRequest(
        url="https://example.test/query",
        method="POST",
        headers={"Authorization": "Bearer super-secret"},
        json_body={"b": 2, "a": 1},
    )
    second = FetchRequest(
        url=first.url,
        method="POST",
        headers={"X-Algolia-API-Key": "also-secret"},
        json_body={"a": 1, "b": 2},
    )

    assert not (await cached.fetch(first)).from_cache
    assert not (await cached.fetch(second)).from_cache
    same_representation = second.model_copy(update={"json_body": {"b": 2, "a": 1}})
    assert (await cached.fetch(same_representation)).from_cache
    assert inner.calls == 2
    for path in tmp_path.glob("*.json"):
        metadata = path.read_text(encoding="utf-8")
        assert "super-secret" not in metadata
        assert "also-secret" not in metadata
        assert "headers" not in json.loads(metadata)


@pytest.mark.asyncio
async def test_offline_cache_accepts_stale_but_force_refresh_fails(tmp_path) -> None:
    class StubFetcher:
        async def fetch(self, request: FetchRequest) -> FetchResult:
            return FetchResult(
                url=request.url,
                status_code=200,
                body="snapshot",
                source="stub",
            )

    request = FetchRequest(url="https://example.test/page")
    online = CachedFetcher(StubFetcher(), tmp_path, ttl_hours=0)
    await online.fetch(request)
    offline = CachedFetcher(StubFetcher(), tmp_path, ttl_hours=0, offline=True)

    assert (await offline.fetch(request)).body == "snapshot"
    with pytest.raises(OfflineCacheMissError):
        await offline.fetch(request.model_copy(update={"force_refresh": True}))
