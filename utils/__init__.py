"""Small, dependency-free helpers shared across the crawler."""

from .hashing import content_hash, sha256_text, stable_json_hash
from .logging import JsonFormatter, get_json_logger, log_event
from .normalization import (
    normalize_pack_size,
    normalize_price,
    normalize_text,
    normalize_url,
    parse_price,
)
from .urls import deduplicate_urls

__all__ = [
    "deduplicate_urls",
    "content_hash",
    "get_json_logger",
    "JsonFormatter",
    "log_event",
    "normalize_pack_size",
    "normalize_price",
    "normalize_text",
    "normalize_url",
    "parse_price",
    "sha256_text",
    "stable_json_hash",
]
