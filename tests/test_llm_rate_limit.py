"""Rate limiting and quota handling for the optional shadow LLM adapter.

All provider traffic is mocked, so running these tests never spends API quota.
"""

from __future__ import annotations

import httpx
import pytest

from llm.openai_compatible import (
    LLMQuotaExhaustedError,
    LLMSettings,
    OpenAICompatibleAdapter,
)
from llm.shadow import ShadowLLMExtractor


SETTINGS = LLMSettings(
    provider="gemini",
    model="test-model",
    api_key="test-key",
    base_url="https://example.invalid/v1",
)

VALID_BODY = {
    "choices": [
        {
            "message": {
                "content": (
                    '{"product": {"name": "Example", '
                    '"product_url": "https://example.invalid/product/a"}, '
                    '"variants": []}'
                )
            }
        }
    ]
}


def _adapter(handler, *, max_retries: int = 2, rpm: float = 5):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    adapter = OpenAICompatibleAdapter(
        SETTINGS,
        transport=httpx.MockTransport(handler),
        requests_per_minute=rpm,
        max_retries=max_retries,
        sleep=fake_sleep,
    )
    return adapter, slept


@pytest.mark.asyncio
async def test_retries_then_raises_quota_error_after_persistent_429() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": {"message": "quota"}})

    adapter, slept = _adapter(handler, max_retries=2)

    with pytest.raises(LLMQuotaExhaustedError):
        await adapter.extract(context="{}")

    assert calls["n"] == 3, "should try once then retry twice"
    assert slept, "backoff should sleep between retries"


@pytest.mark.asyncio
async def test_recovers_when_a_retry_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"})
        return httpx.Response(200, json=VALID_BODY)

    adapter, slept = _adapter(handler)
    result = await adapter.extract(context="{}")

    assert result.product.name == "Example"
    assert calls["n"] == 2
    assert slept[0] == pytest.approx(0.01), "Retry-After should be honoured"


@pytest.mark.asyncio
async def test_rate_limiter_paces_consecutive_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=VALID_BODY)

    adapter, slept = _adapter(handler, rpm=60)  # one call per second

    await adapter.extract(context="{}")
    assert slept == [], "the first call should not wait"

    await adapter.extract(context="{}")
    assert slept and slept[0] > 0, "the second call should be paced"


@pytest.mark.asyncio
async def test_shadow_extractor_stops_sampling_after_quota_exhaustion() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": {"message": "quota"}})

    adapter, _ = _adapter(handler, max_retries=1)
    extractor = ShadowLLMExtractor(SETTINGS, adapter=adapter)

    first = await extractor.extract("<html></html>", "https://example.invalid/product/a")
    assert first.status == "failed"
    assert extractor.quota_exhausted is True
    spent = calls["n"]

    second = await extractor.extract("<html></html>", "https://example.invalid/product/b")
    assert second.status == "skipped"
    assert second.request_sent is False
    assert "quota exhausted" in (second.reason or "")
    assert calls["n"] == spent, "no further provider calls should be made"
