"""URL-specific convenience functions."""

from __future__ import annotations

from collections.abc import Iterable

from .normalization import normalize_url


def deduplicate_urls(urls: Iterable[str], base_url: str | None = None) -> list[str]:
    """Canonicalize and deduplicate URLs without changing first-seen order."""

    result: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        url = normalize_url(raw_url, base_url)
        if url is not None and url not in seen:
            seen.add(url)
            result.append(url)
    return result


canonicalize_url = normalize_url
