from pathlib import Path

import pytest

from agents import DiscoveredURL
from app_config import AppConfig, load_config
from orchestrator import CatalogOrchestrator, CrawlCandidate, RunOptions


def isolated_config(tmp_path: Path) -> AppConfig:
    config = load_config().model_copy(deep=True)
    config.cache.directory = tmp_path / "cache"
    config.storage.sqlite_path = tmp_path / "catalog.db"
    config.output.json_path = tmp_path / "products.json"
    config.output.csv_path = tmp_path / "products.csv"
    config.output.agreement_path = tmp_path / "agreement.json"
    config.output.run_report_path = tmp_path / "run.json"
    config.output.dashboard_path = tmp_path / "dashboard.html"
    return config


def test_isolated_config_keeps_all_generated_files_in_tmp_path(tmp_path) -> None:
    config = isolated_config(tmp_path)

    assert all(
        path.is_relative_to(tmp_path)
        for path in (
            config.storage.sqlite_path,
            config.cache.directory,
            config.output.json_path,
            config.output.csv_path,
            config.output.agreement_path,
            config.output.run_report_path,
            config.output.dashboard_path,
        )
    )


@pytest.mark.asyncio
async def test_zero_discovery_is_failed_without_network(tmp_path) -> None:
    orchestrator = CatalogOrchestrator(isolated_config(tmp_path), RunOptions(no_llm=True))

    async def no_robots() -> None:
        return None

    async def no_discovery():
        return [[], []]

    orchestrator._load_robots = no_robots  # type: ignore[method-assign]
    orchestrator._discover_categories = no_discovery  # type: ignore[method-assign]

    report = await orchestrator.run()

    assert report["status"] == "failed"
    assert report["products_planned"] == 0


@pytest.mark.asyncio
async def test_mixed_success_and_failure_is_partial(tmp_path) -> None:
    config = isolated_config(tmp_path)
    orchestrator = CatalogOrchestrator(config, RunOptions(no_llm=True))
    candidate = CrawlCandidate(
        category=config.categories[0],
        discovered=DiscoveredURL(
            url="https://www.safcodental.com/product/example",
            discovered_from=config.categories[0].url,
            method="algolia",
        ),
    )

    async def no_robots() -> None:
        return None

    async def one_discovery():
        orchestrator.discovery[config.categories[0].name] = {"status": "completed", "count": 1}
        return [[candidate], []]

    async def partial_product(_: CrawlCandidate) -> None:
        orchestrator.metrics.products_extracted += 1
        orchestrator.metrics.failures += 1

    orchestrator._load_robots = no_robots  # type: ignore[method-assign]
    orchestrator._discover_categories = one_discovery  # type: ignore[method-assign]
    orchestrator._process_product = partial_product  # type: ignore[method-assign]

    report = await orchestrator.run()

    assert report["status"] == "partial"
    assert report["products_planned"] == 1


def test_cross_origin_redirect_is_rejected(tmp_path) -> None:
    orchestrator = CatalogOrchestrator(isolated_config(tmp_path), RunOptions(no_llm=True))
    try:
        with pytest.raises(ValueError, match="cross-origin"):
            orchestrator._require_allowed_final_url(
                "https://www.safcodental.com/product/example",
                "https://example.net/product/example",
            )
    finally:
        import asyncio

        asyncio.run(orchestrator.http_fetcher.aclose())
        orchestrator.database.close()
