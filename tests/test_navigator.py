from __future__ import annotations

import json

import pytest

from agents.classifier import PageClassifier
from agents.navigator import Navigator
from fetchers import FetchRequest, FetchResult
from models.enums import PageType
from policy import RobotsPolicy


def category_html() -> str:
    config = {
        "applicationId": "APP123",
        "apiKey": "public-search-key",
        "indexName": "catalog",
        "hitsPerPage": 2,
        "request": {"path": "Dental /// Gloves", "level": 1},
    }
    item_list = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "item": {"@type": "Product", "url": "/product/fallback"},
            },
            {"@type": "ListItem", "item": "/catalog/product/view/id/99"},
        ],
    }
    return (
        f"<script>window.algoliaConfig = {json.dumps(config)};</script>"
        f'<script type="application/ld+json">{json.dumps(item_list)}</script>'
    )


class AlgoliaFetcher:
    def __init__(self, *, fail_api: bool = False) -> None:
        self.fail_api = fail_api
        self.requests: list[FetchRequest] = []

    async def fetch(self, request: FetchRequest) -> FetchResult:
        self.requests.append(request)
        if request.method == "GET":
            return FetchResult(
                url=request.url,
                status_code=200,
                body=category_html(),
                source="stub",
            )
        if self.fail_api:
            return FetchResult(url=request.url, status_code=503, body="", source="stub")
        page = request.json_body["page"]
        hits = (
            [
                {
                    "objectID": "1",
                    "family_url": "https://shop.test/product/a?utm_source=x#top",
                    "url": "https://shop.test/product/wrong",
                },
                {"objectID": "2", "url": "/product/b"},
                {"objectID": "3", "url": "/catalog/product/view/id/3"},
            ]
            if page == 0
            else [
                {"objectID": "1-again", "family_url": "/product/a"},
                {"objectID": "4", "family_url": "/product/c"},
            ]
        )
        return FetchResult(
            url=request.url,
            status_code=200,
            body=json.dumps({"hits": hits, "nbHits": 4, "nbPages": 2}),
            source="stub",
        )


def robots_policy() -> RobotsPolicy:
    return RobotsPolicy.from_text(
        "User-agent: *\nDisallow: /catalog/product/view/id/\n",
        "https://shop.test/robots.txt",
        user_agent="Bot",
    )


@pytest.mark.asyncio
async def test_navigator_queries_algolia_pages_prefers_family_url_and_dedupes() -> None:
    fetcher = AlgoliaFetcher()
    navigator = Navigator(fetcher, robots_policy=robots_policy())
    discovered = await navigator.discover("https://shop.test/catalog/gloves")

    assert [item.url for item in discovered] == [
        "https://shop.test/product/a",
        "https://shop.test/product/b",
        "https://shop.test/product/c",
    ]
    posts = [request for request in fetcher.requests if request.method == "POST"]
    assert len(posts) == 2
    assert posts[0].url.endswith("/indexes/catalog_products/query")
    assert posts[0].json_body["facetFilters"] == [
        "categories.level1:Dental /// Gloves"
    ]
    assert posts[0].json_body["numericFilters"] == ["visibility_catalog=1"]
    assert posts[0].json_body["distinct"] is True
    assert navigator.last_total_hits == 4


@pytest.mark.asyncio
async def test_navigator_falls_back_to_json_ld_item_list() -> None:
    navigator = Navigator(AlgoliaFetcher(fail_api=True), robots_policy=robots_policy())
    discovered = await navigator.discover("https://shop.test/catalog/gloves")
    assert [item.url for item in discovered] == ["https://shop.test/product/fallback"]
    assert discovered[0].method == "json_ld_item_list"


def test_classifier_uses_root_json_ld_without_misreading_nested_products() -> None:
    classifier = PageClassifier()
    category = classifier.classify("https://shop.test/anything", category_html())
    product = classifier.classify(
        "https://shop.test/anything",
        '<script type="application/ld+json">{"@type":"Product"}</script>',
    )
    unknown = classifier.classify("https://shop.test/about", "<html></html>")

    assert category.page_type is PageType.CATEGORY
    assert product.page_type is PageType.PRODUCT
    assert unknown.page_type is PageType.UNKNOWN

