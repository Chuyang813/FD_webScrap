"""Optional, non-critical-path shadow LLM support."""

from .openai_compatible import (
    LLMQuotaExhaustedError,
    LLMSettings,
    OpenAICompatibleAdapter,
)
from .shadow import ShadowEvidence, ShadowLLMExtractor, build_shadow_context

__all__ = [
    "LLMQuotaExhaustedError",
    "LLMSettings",
    "OpenAICompatibleAdapter",
    "ShadowEvidence",
    "ShadowLLMExtractor",
    "build_shadow_context",
]
