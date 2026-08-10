from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class CrawlerConfig(BaseModel):
    max_retries: int = 3
    max_products: int | None = None


class FetchConfig(BaseModel):
    connect_timeout_seconds: float = 10
    read_timeout_seconds: float = 30
    user_agent: str = "FrontierDentalTakeHomeBot/0.1"


class CacheConfig(BaseModel):
    enabled: bool = True
    ttl_hours: float = 24
    directory: Path = Path("cache/pages")


class RobotsConfig(BaseModel):
    enforce: bool = True
    unknown_policy: str = "deny"


class RateLimitConfig(BaseModel):
    requests_per_second: float = 1
    max_concurrency: int = 2
    jitter_ms: int = 300


class CategoryConfig(BaseModel):
    name: str
    url: str


class StorageConfig(BaseModel):
    sqlite_path: Path = Path("data/catalog.db")


class OutputConfig(BaseModel):
    json_path: Path = Path("output/products.json")
    csv_path: Path = Path("output/products.csv")
    agreement_path: Path = Path("output/agreement_report.json")
    run_report_path: Path = Path("output/run_report.json")


class LLMConfig(BaseModel):
    enabled: bool = True
    shadow_sample: int = 2
    agreement_threshold: float = Field(default=0.8, ge=0, le=1)
    # Free provider tiers are commonly capped near five requests per minute.
    requests_per_minute: float = Field(default=5, gt=0)
    max_retries: int = Field(default=2, ge=0)


class AppConfig(BaseModel):
    crawler: CrawlerConfig = Field(default_factory=CrawlerConfig)
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    robots: RobotsConfig = Field(default_factory=RobotsConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    categories: list[CategoryConfig]
    storage: StorageConfig = Field(default_factory=StorageConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    with Path(path).open(encoding="utf-8") as stream:
        return AppConfig.model_validate(yaml.safe_load(stream))
