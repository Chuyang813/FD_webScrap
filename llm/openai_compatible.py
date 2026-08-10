"""Minimal environment-configured OpenAI-compatible structured-output adapter."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
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
    timeout_seconds: float = Field(default=30, gt=0)

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


class OpenAICompatibleAdapter:
    """Call ``chat/completions`` with strict JSON Schema and Pydantic validation."""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport

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
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            transport=self._transport,
        ) as client:
            response = await client.post(endpoint, headers=headers, json=self.request_payload(context=context))
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
