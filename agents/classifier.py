"""Deterministic page classification from URL and root JSON-LD types."""

from __future__ import annotations

import json
from typing import Any, Literal
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

from fetchers import FetchResult
from models.enums import PageType


class PageClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_type: PageType
    method: Literal["deterministic"] = "deterministic"
    confidence: float = Field(ge=0, le=1)
    signals: list[str] = Field(default_factory=list)


class PageClassifier:
    """Classify without an LLM so results stay reproducible."""

    def classify(
        self,
        url: str | FetchResult,
        body: str | None = None,
    ) -> PageClassification:
        if isinstance(url, FetchResult):
            body = url.body
            url = url.url
        body = body or ""
        root_types = _root_json_ld_types(body)
        path = urlsplit(url).path.lower().rstrip("/")
        signals: list[str] = []

        if "Product" in root_types:
            signals.append("json_ld:Product")
            return PageClassification(
                page_type=PageType.PRODUCT,
                confidence=0.99,
                signals=signals,
            )
        if "ItemList" in root_types:
            signals.append("json_ld:ItemList")
            return PageClassification(
                page_type=PageType.CATEGORY,
                confidence=0.98,
                signals=signals,
            )
        if path.startswith("/product/") or path.startswith("/catalog/product/view/id/"):
            signals.append("url:product")
            return PageClassification(
                page_type=PageType.PRODUCT,
                confidence=0.85,
                signals=signals,
            )
        if path == "/catalog" or path.startswith("/catalog/"):
            signals.append("url:catalog")
            return PageClassification(
                page_type=PageType.CATEGORY,
                confidence=0.75,
                signals=signals,
            )
        return PageClassification(
            page_type=PageType.UNKNOWN,
            confidence=0,
            signals=[],
        )


def classify_page(url: str, body: str = "") -> PageClassification:
    return PageClassifier().classify(url, body)


def _root_json_ld_types(html: str) -> set[str]:
    types: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.string or script.get_text())
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for node in _root_nodes(value):
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type")
            if isinstance(node_type, str):
                types.add(node_type)
            elif isinstance(node_type, list):
                types.update(item for item in node_type if isinstance(item, str))
    return types


def _root_nodes(value: Any):
    if isinstance(value, list):
        yield from value
    elif isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            yield from graph

