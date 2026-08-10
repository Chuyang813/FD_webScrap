"""Shared catalog and crawl enums."""

from enum import Enum


class PriceVisibility(str, Enum):
    PUBLIC = "public"
    LOGIN_REQUIRED = "login_required"
    NOT_PRESENT = "not_present"
    UNKNOWN = "unknown"


class FieldSource(str, Enum):
    API = "api"
    JSON_LD = "json_ld"
    EMBEDDED_STATE = "embedded_state"
    DOM = "dom"
    LLM_FALLBACK = "llm_fallback"
    LLM_SHADOW = "llm_shadow"
    DERIVED = "derived"
    NOT_AVAILABLE = "not_available"


class PageType(str, Enum):
    CATEGORY = "category"
    PRODUCT = "product"
    UNKNOWN = "unknown"


class CrawlStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
