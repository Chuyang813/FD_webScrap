"""Category navigation through Safco's page-provided Algolia configuration."""

from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

from fetchers import FetchRequest, Fetcher
from models.enums import PageType
from policy import RobotsDecision, RobotsDisallowedError, RobotsPolicy
from utils.urls import canonicalize_url

_ALGOLIA_ASSIGNMENT = re.compile(r"window\.algoliaConfig\s*=\s*")
_FORBIDDEN_PRODUCT_PATH = re.compile(r"^/catalog/product/view/id(?:/|$)", re.IGNORECASE)


class NavigationError(RuntimeError):
    pass


class DiscoveredURL(BaseModel):
    """One canonical product URL plus its discovery provenance."""

    model_config = ConfigDict(extra="forbid")

    url: str
    page_type: PageType = PageType.PRODUCT
    discovered_from: str
    method: Literal["algolia", "json_ld_item_list"]
    object_id: str | None = None


def extract_algolia_config(html: str) -> dict[str, Any]:
    """Decode the JSON value assigned to ``window.algoliaConfig``."""

    match = _ALGOLIA_ASSIGNMENT.search(html)
    if match is None:
        raise ValueError("window.algoliaConfig was not found")
    value, _ = json.JSONDecoder().raw_decode(html, match.end())
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("window.algoliaConfig is not an object")
    return value


def is_forbidden_product_url(url: str) -> bool:
    """Reject Magento's robots-disallowed internal product route."""

    return bool(_FORBIDDEN_PRODUCT_PATH.match(urlsplit(url).path))


def extract_json_ld_product_urls(html: str, base_url: str) -> list[str]:
    """Extract product URLs from JSON-LD ``ItemList`` objects."""

    urls: list[str] = []
    seen: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for node in _walk_json(payload):
            if not isinstance(node, dict) or not _has_type(node, "ItemList"):
                continue
            elements = node.get("itemListElement", [])
            if not isinstance(elements, list):
                elements = [elements]
            for element in elements:
                candidate = _item_url(element)
                if not candidate:
                    continue
                try:
                    url = canonicalize_url(candidate, base_url)
                except ValueError:
                    continue
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)
    return urls


class Navigator:
    """Discover product-family pages without parsing complete product records."""

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        robots_policy: RobotsPolicy | None = None,
        hits_per_page: int | None = None,
        max_pages: int | None = None,
        max_products: int | None = None,
    ) -> None:
        if hits_per_page is not None and hits_per_page < 1:
            raise ValueError("hits_per_page must be positive")
        if max_pages is not None and max_pages < 1:
            raise ValueError("max_pages must be positive")
        if max_products is not None and max_products < 1:
            raise ValueError("max_products must be positive")
        self.fetcher = fetcher
        self.robots_policy = robots_policy
        self.hits_per_page = hits_per_page
        self.max_pages = max_pages
        self.max_products = max_products
        self.pages_fetched = 0
        self.last_total_hits: int | None = None
        self.last_method: str | None = None

    @classmethod
    def from_config(
        cls,
        fetcher: Fetcher,
        config: Any,
        *,
        robots_policy: RobotsPolicy | None = None,
    ) -> "Navigator":
        return cls(
            fetcher,
            robots_policy=robots_policy,
            max_products=config.crawler.max_products,
        )

    async def discover(
        self,
        category_url: str,
        *,
        force_refresh: bool = False,
    ) -> list[DiscoveredURL]:
        self.pages_fetched = 0
        self.last_total_hits = None
        self.last_method = None
        category_url = canonicalize_url(category_url) or category_url
        self._require_crawlable(category_url)
        category = await self.fetcher.fetch(
            FetchRequest(url=category_url, force_refresh=force_refresh)
        )
        if not 200 <= category.status_code < 300:
            raise NavigationError(
                f"category fetch returned HTTP {category.status_code}: {category_url}"
            )

        algolia_error: Exception | None = None
        try:
            config = extract_algolia_config(category.body)
            discovered = await self._discover_algolia(
                category_url,
                config,
                force_refresh=force_refresh,
            )
            if discovered:
                self.last_method = "algolia"
                return discovered
        except Exception as exc:
            algolia_error = exc

        fallback = self._discover_json_ld(category_url, category.body)
        if fallback:
            self.last_method = "json_ld_item_list"
            return fallback
        if algolia_error is not None:
            raise NavigationError(
                "Algolia discovery failed and JSON-LD ItemList fallback was empty"
            ) from algolia_error
        return []

    async def discover_urls(
        self,
        category_url: str,
        *,
        force_refresh: bool = False,
    ) -> list[str]:
        """Convenience API for callers that only need URL strings."""

        return [
            item.url
            for item in await self.discover(category_url, force_refresh=force_refresh)
        ]

    async def _discover_algolia(
        self,
        category_url: str,
        config: dict[str, Any],
        *,
        force_refresh: bool,
    ) -> list[DiscoveredURL]:
        application_id = _required_string(config, "applicationId")
        api_key = _required_string(config, "apiKey")
        index_name = _required_string(config, "indexName")
        if not index_name.endswith("_products"):
            index_name += "_products"

        request_config = config.get("request")
        if not isinstance(request_config, dict):
            raise ValueError("Algolia request config is missing")
        path = _required_string(request_config, "path")
        level = int(request_config["level"])
        per_page = self.hits_per_page or int(config.get("hitsPerPage") or 20)
        endpoint = (
            f"https://{application_id.lower()}-dsn.algolia.net/1/indexes/"
            f"{quote(index_name, safe='')}/query"
        )
        headers = {
            "Content-Type": "application/json",
            "X-Algolia-Application-Id": application_id,
            "X-Algolia-API-Key": api_key,
        }

        found: list[DiscoveredURL] = []
        seen: set[str] = set()
        page = 0
        while True:
            payload = {
                "query": "",
                "page": page,
                "hitsPerPage": per_page,
                "facetFilters": [f"categories.level{level}:{path}"],
                "numericFilters": ["visibility_catalog=1"],
                "distinct": True,
            }
            result = await self.fetcher.fetch(
                FetchRequest(
                    url=endpoint,
                    method="POST",
                    headers=headers,
                    json_body=payload,
                    force_refresh=force_refresh,
                )
            )
            self.pages_fetched += 1
            if not 200 <= result.status_code < 300:
                raise NavigationError(
                    f"Algolia returned HTTP {result.status_code} for page {page}"
                )
            response = json.loads(result.body)
            if not isinstance(response, dict) or not isinstance(response.get("hits"), list):
                raise NavigationError("Algolia response did not contain a hits list")
            self.last_total_hits = _optional_int(response.get("nbHits"))

            for hit in response["hits"]:
                if not isinstance(hit, dict):
                    continue
                url = self._url_from_hit(hit, category_url)
                if url is None or url in seen:
                    continue
                seen.add(url)
                found.append(
                    DiscoveredURL(
                        url=url,
                        discovered_from=category_url,
                        method="algolia",
                        object_id=(str(hit["objectID"]) if hit.get("objectID") is not None else None),
                    )
                )
                if self.max_products is not None and len(found) >= self.max_products:
                    return found

            page += 1
            nb_pages = max(1, _optional_int(response.get("nbPages")) or 1)
            if page >= nb_pages or (self.max_pages is not None and page >= self.max_pages):
                return found

    def _url_from_hit(self, hit: dict[str, Any], category_url: str) -> str | None:
        for key in ("family_url", "url"):
            candidate = hit.get(key)
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            try:
                url = canonicalize_url(candidate, category_url)
            except ValueError:
                continue
            if url and self._is_product_url_allowed(url, category_url):
                return url
        return None

    def _discover_json_ld(self, category_url: str, html: str) -> list[DiscoveredURL]:
        found: list[DiscoveredURL] = []
        for url in extract_json_ld_product_urls(html, category_url):
            if not self._is_product_url_allowed(url, category_url):
                continue
            found.append(
                DiscoveredURL(
                    url=url,
                    discovered_from=category_url,
                    method="json_ld_item_list",
                )
            )
            if self.max_products is not None and len(found) >= self.max_products:
                break
        return found

    discover_product_urls = discover_urls

    def _is_product_url_allowed(self, url: str, category_url: str) -> bool:
        if urlsplit(url).netloc.lower() != urlsplit(category_url).netloc.lower():
            return False
        if is_forbidden_product_url(url):
            return False
        if not urlsplit(url).path.lower().startswith("/product/"):
            return False
        return self._policy_allows(url)

    def _require_crawlable(self, url: str) -> None:
        if not self._policy_allows(url):
            raise RobotsDisallowedError(f"robots policy does not allow {url}")

    def _policy_allows(self, url: str) -> bool:
        if self.robots_policy is None:
            return True
        decision = self.robots_policy.check(url)
        if decision is RobotsDecision.ALLOWED:
            return True
        if decision is RobotsDecision.DISALLOWED:
            return False
        return self.robots_policy.is_allowed(url)


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Algolia config is missing {key}")
    return value


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _walk_json(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _has_type(node: dict[str, Any], expected: str) -> bool:
    value = node.get("@type")
    return value == expected or (isinstance(value, list) and expected in value)


def _item_url(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    item = value.get("item")
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("url", "@id"):
            if isinstance(item.get(key), str):
                return item[key]
    for key in ("url", "@id"):
        if isinstance(value.get(key), str):
            return value[key]
    return None
