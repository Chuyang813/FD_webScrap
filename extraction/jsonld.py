"""Small, tolerant JSON-LD helpers for Safco product pages."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from bs4 import BeautifulSoup

from utils.normalization import normalize_text, normalize_url


def parse_json_ld(html: str) -> tuple[list[Any], list[str]]:
    """Parse JSON-LD scripts without making one malformed block fatal."""

    documents: list[Any] = []
    warnings: list[str] = []
    soup = BeautifulSoup(html, "html.parser")
    for index, script in enumerate(soup.select('script[type="application/ld+json"]')):
        raw = (script.string or script.get_text() or "").strip()
        if raw.startswith("<!--"):
            raw = raw[4:]
        if raw.endswith("-->"):
            raw = raw[:-3]
        try:
            documents.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError) as exc:
            warnings.append(f"json_ld[{index}] ignored: {exc.msg if hasattr(exc, 'msg') else exc}")
    return documents, warnings


def iter_json_ld_nodes(value: Any) -> Iterator[dict[str, Any]]:
    """Yield every object from arrays, ``@graph`` documents, and nested nodes."""

    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_json_ld_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_ld_nodes(child)


def has_type(node: dict[str, Any], wanted: str) -> bool:
    value = node.get("@type")
    return value == wanted or isinstance(value, list) and wanted in value


def find_typed_nodes(documents: list[Any], wanted: str) -> list[dict[str, Any]]:
    return [
        node
        for document in documents
        for node in iter_json_ld_nodes(document)
        if has_type(node, wanted)
    ]


def select_product(documents: list[Any], page_url: str) -> dict[str, Any] | None:
    """Prefer the Product whose ``url``/``@id`` identifies the current PDP."""

    try:
        target = normalize_url(page_url)
    except ValueError:
        target = None

    def score(node: dict[str, Any]) -> tuple[int, int]:
        matches = 0
        for key in ("url", "@id"):
            candidate = node.get(key)
            if not isinstance(candidate, str):
                continue
            try:
                if target is not None and normalize_url(candidate) == target:
                    matches += 10
            except ValueError:
                pass
        completeness = sum(bool(node.get(key)) for key in ("name", "sku", "offers", "brand"))
        return matches, completeness

    products = find_typed_nodes(documents, "Product")
    return max(products, key=score) if products else None


def select_breadcrumbs(documents: list[Any]) -> dict[str, Any] | None:
    breadcrumbs = find_typed_nodes(documents, "BreadcrumbList")
    return max(breadcrumbs, key=lambda node: len(node.get("itemListElement") or []), default=None)


def breadcrumb_path(
    breadcrumbs: dict[str, Any] | None,
    *,
    page_url: str,
    product_name: str | None,
) -> list[str]:
    """Return category names, excluding Home and the current product crumb."""

    if not breadcrumbs:
        return []
    elements = breadcrumbs.get("itemListElement")
    if not isinstance(elements, list):
        return []
    ordered = sorted(
        (item for item in elements if isinstance(item, dict)),
        key=lambda item: item.get("position") if isinstance(item.get("position"), (int, float)) else 10**9,
    )
    result: list[str] = []
    try:
        canonical_page = normalize_url(page_url)
    except ValueError:
        canonical_page = None
    for entry in ordered:
        nested = entry.get("item") if isinstance(entry.get("item"), dict) else {}
        name = normalize_text(entry.get("name") or nested.get("name"))
        item_url = entry.get("item") if isinstance(entry.get("item"), str) else nested.get("url")
        if not name or name.casefold() == "home":
            continue
        is_current_url = False
        if item_url and canonical_page:
            try:
                is_current_url = normalize_url(item_url) == canonical_page
            except ValueError:
                pass
        if is_current_url or product_name and name.casefold() == product_name.casefold():
            continue
        if name not in result:
            result.append(name)
    return result
