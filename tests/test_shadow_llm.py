import json

import httpx
import pytest

from llm import LLMSettings, OpenAICompatibleAdapter, ShadowLLMExtractor
from models import ExtractionResult, FieldSource, PriceVisibility, Product, ProductVariant


def sample_result() -> ExtractionResult:
    url = "https://example.com/product/one"
    return ExtractionResult(
        product=Product(
            name="Product One",
            product_url=url,
            price_visibility=PriceVisibility.PUBLIC,
        ),
        variants=[
            ProductVariant(
                product_url=url,
                sku="SKU-1",
                price="10.00",
                currency="USD",
                price_visibility=PriceVisibility.PUBLIC,
            )
        ],
    )


@pytest.mark.asyncio
async def test_missing_environment_returns_skipped_evidence_without_request(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    evidence = await ShadowLLMExtractor().extract(
        "<html><body>safe deterministic flow</body></html>",
        "https://example.com/product/one",
    )
    assert evidence.status == "skipped"
    assert evidence.request_sent is False
    assert "LLM_PROVIDER" in evidence.reason
    assert evidence.result is None


@pytest.mark.asyncio
async def test_adapter_sends_strict_schema_and_validates_pydantic_output() -> None:
    expected = sample_result()

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        schema = payload["response_format"]["json_schema"]
        assert payload["model"] == "test-model"
        assert schema["strict"] is True
        assert schema["schema"]["additionalProperties"] is False
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": expected.model_dump_json()}}]},
        )

    settings = LLMSettings(
        provider="compatible",
        model="test-model",
        api_key="secret",
        base_url="https://llm.example/v1",
    )
    adapter = OpenAICompatibleAdapter(settings, transport=httpx.MockTransport(handler))
    evidence = await ShadowLLMExtractor(settings, adapter=adapter).extract(
        "<html><body>Product One</body></html>",
        "https://example.com/product/one",
    )
    assert evidence.status == "completed"
    assert evidence.request_sent is True
    assert evidence.result is not None
    assert evidence.result.product.field_provenance["name"] is FieldSource.LLM_SHADOW
    assert evidence.result.variants[0].field_provenance["sku"] is FieldSource.LLM_SHADOW


@pytest.mark.asyncio
async def test_invalid_provider_payload_is_failed_evidence_not_an_exception() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"unexpected":true}'}}]})

    settings = LLMSettings(
        provider="compatible",
        model="test-model",
        api_key="secret",
        base_url="https://llm.example/v1",
    )
    adapter = OpenAICompatibleAdapter(settings, transport=httpx.MockTransport(handler))
    evidence = await ShadowLLMExtractor(settings, adapter=adapter).extract(
        "<html></html>",
        "https://example.com/product/one",
    )
    assert evidence.status == "failed"
    assert evidence.request_sent is True
    assert evidence.result is None
