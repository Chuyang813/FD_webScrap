"""Stable hashes used for cache and variant identities."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_text(value: str) -> str:
    """Return the hexadecimal SHA-256 digest for UTF-8 text."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def content_hash(value: str | bytes) -> str:
    """Hash fetched text or raw response bytes without lossy conversion."""

    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def stable_json_hash(value: Any) -> str:
    """Hash a JSON-compatible value independent of mapping key order."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256_text(payload)
