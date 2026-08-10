"""Minimal environment-configured OpenAI-compatible structured-output adapter."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from models import ExtractionResult


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    base_url: str | None = None
    # Structured extraction over a bounded page context genuinely exceeded 30s in
    # testing, so the default allows for a slow generation before retrying.
    timeout_seconds: float = Field(default=60, gt=0)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "LLMSettings":
        values = os.environ if environ is None else environ
        return cls(
            provider=values.get("LLM_PROVIDER") or None,
            model=values.get("LLM_MODEL") or None,
            api_key=values.get("LLM_API_KEY") or None,
            base_url=values.get("LLM_BASE_URL") or None,
        )

    @property
    def resolved_base_url(self) -> str | None:
        if self.base_url:
            return self.base_url.rstrip("/")
        if self.provider and self.provider.casefold() == "openai":
            return "https://api.openai.com/v1"
        return None

    @property
    def missing_configuration(self) -> list[str]:
        missing = [
            name
            for name, value in (
                ("LLM_PROVIDER", self.provider),
                ("LLM_MODEL", self.model),
                ("LLM_API_KEY", self.api_key),
                ("LLM_BASE_URL", self.resolved_base_url),
            )
            if not value
        ]
        return missing

    @property
    def configured(self) -> bool:
        return not self.missing_configuration


class LLMQuotaExhaustedError(RuntimeError):
    """The provider kept returning 429 after the configured retries.

    Raised so the caller can stop sampling for the rest of the run instead of
    spending further attempts against a limit that will not clear in time.
    """


class OpenAICompatibleAdapter:
    """Call ``chat/completions`` with strict JSON Schema and Pydantic validation."""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        requests_per_minute: float = 5,
        max_retries: int = 2,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self.min_interval = 60.0 / requests_per_minute if requests_per_minute > 0 else 0.0
        self.max_retries = max_retries
        self._sleep = sleep or asyncio.sleep
        self._last_call_at: float | None = None

    async def _respect_rate_limit(self) -> None:
        """Free provider tiers cap requests per minute; pace calls to stay under."""

        if self.min_interval <= 0 or self._last_call_at is None:
            return
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < self.min_interval:
            await self._sleep(self.min_interval - elapsed)

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return max(0.0, float(header))
            except ValueError:
                pass
        return float(2**attempt)

    def request_payload(self, *, context: str) -> dict[str, Any]:
        schema = ExtractionResult.model_json_schema()
        return {
            "model": self.settings.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Extract only facts present in the supplied Safco page context. "
                        "Do not invent missing values. Return the required schema."
                    ),
                },
                {"role": "user", "content": context},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "shadow_product_extraction",
                    "strict": True,
                    "schema": schema,
                },
            },
        }

    async def extract(self, *, context: str) -> ExtractionResult:
        missing = self.settings.missing_configuration
        if missing:
            raise RuntimeError("missing LLM configuration: " + ", ".join(missing))
        endpoint = f"{self.settings.resolved_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        payload_body = self.request_payload(context=context)
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            transport=self._transport,
        ) as client:
            for attempt in range(self.max_retries + 1):
                await self._respect_rate_limit()
                self._last_call_at = time.monotonic()
                try:
                    response = await client.post(endpoint, headers=headers, json=payload_body)
                except (httpx.TimeoutException, httpx.TransportError):
                    # Transient transport faults get the same budget as a 429;
                    # a slow generation is not a permanent failure.
                    if attempt == self.max_retries:
                        raise
                    await self._sleep(float(2**attempt))
                    continue
                if response.status_code != 429:
                    break
                if attempt == self.max_retries:
                    raise LLMQuotaExhaustedError(
                        f"provider returned 429 after {self.max_retries + 1} attempts; "
                        "the request or daily quota is exhausted"
                    )
                await self._sleep(self._retry_delay(response, attempt))
            response.raise_for_status()
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("OpenAI-compatible response has no assistant content") from exc
        if isinstance(content, list):
            content = "".join(
                block.get("text", "") for block in content if isinstance(block, dict)
            )
        if not isinstance(content, str):
            raise ValueError("OpenAI-compatible assistant content must be text")
        # Validation remains local even when a provider claims schema adherence.
        try:
            return ExtractionResult.model_validate_json(content)
        except json.JSONDecodeError as exc:
            raise ValueError("assistant content is not valid JSON") from exc
