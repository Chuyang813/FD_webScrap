"""Optional shadow execution with explicit completed/skipped/failed evidence."""

from __future__ import annotations

import json
from typing import Literal

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

from agents.validator import ValidatorAgent
from extraction.jsonld import parse_json_ld
from extraction.master_data import MasterDataDecodeError, decode_master_data
from models import ExtractionResult, FieldSource

from .openai_compatible import (
    LLMQuotaExhaustedError,
    LLMSettings,
    OpenAICompatibleAdapter,
)


class ShadowEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "skipped", "failed"]
    provider: str | None = None
    model: str | None = None
    request_sent: bool = False
    reason: str | None = None
    context_characters: int = Field(default=0, ge=0)
    result: ExtractionResult | None = None


def build_shadow_context(html: str, url: str, *, max_characters: int = 50_000) -> str:
    """Prefer bounded structured page state over a multi-megabyte HTML document."""

    documents, _ = parse_json_ld(html)
    try:
        master_data = decode_master_data(html)
    except MasterDataDecodeError:
        master_data = None
    context: dict[str, object] = {"page_url": url, "json_ld": documents}
    if master_data:
        context["master_data"] = master_data
    else:
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        context["page_text"] = text[: max_characters // 2]
    rendered = json.dumps(context, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(rendered) <= max_characters:
        return rendered
    # The context is prompt text, not an API JSON body field requiring inner validity.
    return rendered[:max_characters] + "\n[context truncated]"


def _mark_shadow_provenance(result: ExtractionResult) -> None:
    product = result.product
    for field in (
        "name",
        "brand",
        "category_path",
        "product_url",
        "description",
        "specifications",
        "image_urls",
        "alternative_product_urls",
        "price_visibility",
    ):
        value = getattr(product, field)
        if value not in (None, "", [], {}):
            product.field_provenance[field] = FieldSource.LLM_SHADOW
    for variant in result.variants:
        for field in (
            "product_url",
            "sku",
            "item_number",
            "product_code",
            "option_values",
            "image_urls",
            "price",
            "currency",
            "price_visibility",
            "unit_pack_size",
            "availability",
        ):
            value = getattr(variant, field)
            if value not in (None, "", [], {}):
                variant.field_provenance[field] = FieldSource.LLM_SHADOW


class ShadowLLMExtractor:
    """Run only when configured; callers can always continue after non-completion."""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        *,
        adapter: OpenAICompatibleAdapter | None = None,
        validator: ValidatorAgent | None = None,
        max_context_characters: int = 50_000,
        requests_per_minute: float = 5,
        max_retries: int = 2,
    ) -> None:
        self.settings = settings or LLMSettings.from_env()
        self.adapter = adapter or OpenAICompatibleAdapter(
            self.settings,
            requests_per_minute=requests_per_minute,
            max_retries=max_retries,
        )
        self.validator = validator or ValidatorAgent()
        self.max_context_characters = max_context_characters
        self.quota_exhausted = False

    async def extract(self, html: str, url: str) -> ShadowEvidence:
        if not self.settings.configured:
            return ShadowEvidence(
                status="skipped",
                provider=self.settings.provider,
                model=self.settings.model,
                request_sent=False,
                reason="missing configuration: " + ", ".join(self.settings.missing_configuration),
            )
        if self.quota_exhausted:
            # Spending more attempts cannot succeed and only produces noise.
            return ShadowEvidence(
                status="skipped",
                provider=self.settings.provider,
                model=self.settings.model,
                request_sent=False,
                reason="provider quota exhausted earlier in this run",
            )
        context = build_shadow_context(
            html,
            url,
            max_characters=self.max_context_characters,
        )
        try:
            result = await self.adapter.extract(context=context)
            _mark_shadow_provenance(result)
            validation = self.validator.validate(result)
            if not validation.valid or validation.result is None:
                return ShadowEvidence(
                    status="failed",
                    provider=self.settings.provider,
                    model=self.settings.model,
                    request_sent=True,
                    reason="shadow validation failed: " + "; ".join(validation.errors),
                    context_characters=len(context),
                )
            return ShadowEvidence(
                status="completed",
                provider=self.settings.provider,
                model=self.settings.model,
                request_sent=True,
                context_characters=len(context),
                result=validation.result,
            )
        except LLMQuotaExhaustedError as exc:
            self.quota_exhausted = True
            return ShadowEvidence(
                status="failed",
                provider=self.settings.provider,
                model=self.settings.model,
                request_sent=True,
                reason=str(exc),
                context_characters=len(context),
            )
        except Exception as exc:  # Provider failures are evidence, never crawl failures.
            return ShadowEvidence(
                status="failed",
                provider=self.settings.provider,
                model=self.settings.model,
                request_sent=True,
                reason=f"{type(exc).__name__}: {exc}",
                context_characters=len(context),
            )

    run = extract
