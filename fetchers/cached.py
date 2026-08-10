"""Small, inspectable disk cache for fetch results."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .base import FetchRequest, FetchResult, Fetcher, OfflineCacheMissError, utc_now


@dataclass(slots=True)
class CacheStats:
    hits: int = 0
    misses: int = 0
    writes: int = 0


class CachedFetcher:
    """Wrap a fetcher with a TTL-aware ``.body``/``.json`` disk cache."""

    def __init__(
        self,
        fetcher: Fetcher,
        directory: str | Path = "cache/pages",
        *,
        ttl_hours: float = 24,
        enabled: bool = True,
        offline: bool = False,
    ) -> None:
        if ttl_hours < 0:
            raise ValueError("ttl_hours cannot be negative")
        self.fetcher = fetcher
        self.directory = Path(directory)
        self.ttl = timedelta(hours=ttl_hours)
        self.enabled = enabled
        self.offline = offline
        self.stats = CacheStats()

    @classmethod
    def from_config(
        cls,
        fetcher: Fetcher,
        config: Any,
        *,
        offline: bool = False,
    ) -> "CachedFetcher":
        return cls(
            fetcher,
            config.cache.directory,
            ttl_hours=config.cache.ttl_hours,
            enabled=config.cache.enabled,
            offline=offline,
        )

    @staticmethod
    def cache_key(request: FetchRequest) -> str:
        body = json.dumps(
            request.json_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        # Representation/auth headers influence the response. Values are only
        # part of this one-way digest and are never stored in cache metadata.
        headers = sorted((key.casefold(), value) for key, value in request.headers.items())
        material = json.dumps(
            [request.method.upper(), request.url, headers, body],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    async def fetch(self, request: FetchRequest) -> FetchResult:
        if not self.enabled:
            if self.offline:
                raise OfflineCacheMissError(f"cache is disabled in offline mode: {request.url}")
            return await self.fetcher.fetch(request)

        key = self.cache_key(request)
        cached = None if request.force_refresh else self._load(key, allow_stale=self.offline)
        if cached is not None:
            self.stats.hits += 1
            return cached

        self.stats.misses += 1
        if self.offline:
            qualifier = "forced refresh" if request.force_refresh else "cache miss"
            raise OfflineCacheMissError(f"offline {qualifier}: {request.method} {request.url}")

        result = await self.fetcher.fetch(request)
        if 200 <= result.status_code < 400:
            self._store(key, request, result)
            self.stats.writes += 1
        return result

    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.directory / f"{key}.body", self.directory / f"{key}.json"

    def _load(self, key: str, *, allow_stale: bool) -> FetchResult | None:
        body_path, metadata_path = self._paths(key)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            raw_body = body_path.read_bytes()
            fetched_at = datetime.fromisoformat(metadata["fetched_at"])
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            if not allow_stale and utc_now() - fetched_at > self.ttl:
                return None
            expected_hash = metadata["content_hash"]
            body = self._decode_verified_body(raw_body, expected_hash)
            if body is None:
                return None
            return FetchResult(
                url=metadata["url"],
                status_code=metadata["status_code"],
                body=body,
                content_type=metadata.get("content_type"),
                fetched_at=fetched_at,
                source="cache",
                from_cache=True,
                content_hash=expected_hash,
            )
        except (OSError, KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _decode_verified_body(raw: bytes, expected_hash: str) -> str | None:
        candidates = [raw]
        # Cache files written by older Windows builds used text-mode newline
        # translation. Recover those snapshots once without weakening hashes.
        undoubled = raw.replace(b"\r\r\n", b"\r\n")
        candidates.append(undoubled)
        if undoubled.endswith(b"\r\n"):
            candidates.append(undoubled[:-2] + b"\n")
        candidates.append(raw.replace(b"\r\r\n", b"\n"))
        candidates.append(CachedFetcher._undo_windows_text_translation(raw))
        for candidate in candidates:
            if hashlib.sha256(candidate).hexdigest() == expected_hash:
                return candidate.decode("utf-8")
        return None

    @staticmethod
    def _undo_windows_text_translation(raw: bytes) -> bytes:
        restored = bytearray()
        index = 0
        while index < len(raw):
            if raw[index : index + 3] == b"\r\r\n":
                restored.extend(b"\r\n")
                index += 3
            elif raw[index : index + 2] == b"\r\n":
                restored.extend(b"\n")
                index += 2
            else:
                restored.append(raw[index])
                index += 1
        return bytes(restored)

    def _store(self, key: str, request: FetchRequest, result: FetchResult) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        body_path, metadata_path = self._paths(key)
        metadata = {
            "cache_key": key,
            "method": request.method,
            "request_url": request.url,
            "url": result.url,
            "status_code": result.status_code,
            "content_type": result.content_type,
            "fetched_at": result.fetched_at.isoformat(),
            "content_hash": result.content_hash,
            "fetch_source": result.source,
        }
        self._atomic_write(body_path, result.body)
        self._atomic_write(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
        )

    @staticmethod
    def _atomic_write(path: Path, value: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(value.encode("utf-8"))
        os.replace(temporary, path)
