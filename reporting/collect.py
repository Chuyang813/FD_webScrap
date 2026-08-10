"""Read the catalogue and the run artifacts into a shape the dashboard renders.

Kept separate from rendering so the numbers can be tested without parsing HTML.
The database is the source of truth; the JSON reports only add run-scoped facts
the tables cannot know, such as discovery counts and shadow evidence.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PRODUCT_FIELDS: tuple[tuple[str, str], ...] = (
    ("name", "name"),
    ("brand", "brand"),
    ("category_path", "category_path_json"),
    ("description", "description"),
    ("specifications", "specifications_json"),
    ("image_urls", "image_urls_json"),
    ("alternative_products", "alternative_products_json"),
    ("price_visibility", "price_visibility"),
)

VARIANT_FIELDS: tuple[tuple[str, str], ...] = (
    ("sku", "sku"),
    ("item_number", "item_number"),
    ("product_code", "product_code"),
    ("price", "price"),
    ("currency", "currency"),
    ("unit_pack_size", "unit_pack_size"),
    ("availability", "availability"),
    ("image_urls", "image_urls_json"),
)

#: Rendered in this order; anything else falls to the end alphabetically.
PROVENANCE_ORDER: tuple[str, ...] = (
    "json_ld",
    "embedded_state",
    "api",
    "dom",
    "derived",
    "llm_shadow",
    "llm_fallback",
    "not_available",
)

_EMPTY = {None, "", "[]", "{}", "null"}


@dataclass(slots=True)
class DashboardData:
    generated_at: str
    run_status: str | None = None
    run_metrics: dict[str, Any] = field(default_factory=dict)
    discovery: dict[str, dict[str, Any]] = field(default_factory=dict)

    products: int = 0
    variants: int = 0
    product_completeness: list[tuple[str, float, int]] = field(default_factory=list)
    variant_completeness: list[tuple[str, float, int]] = field(default_factory=list)
    provenance: list[tuple[str, int]] = field(default_factory=list)
    categories: list[tuple[str, int]] = field(default_factory=list)
    brands: list[tuple[str, int]] = field(default_factory=list)
    price_visibility: list[tuple[str, int]] = field(default_factory=list)
    crawl_state: list[tuple[str, int]] = field(default_factory=list)
    crawl_errors: list[tuple[str, int]] = field(default_factory=list)
    variants_per_product: list[tuple[str, int]] = field(default_factory=list)

    agreement_status: str | None = None
    agreement_model: str | None = None
    agreement_sample: int = 0
    agreement_core: float | None = None
    agreement_advisory: float | None = None
    agreement_threshold: float = 0.80
    agreement_core_fields: list[tuple[str, float]] = field(default_factory=list)
    agreement_advisory_fields: list[tuple[str, float]] = field(default_factory=list)

    @property
    def discovered(self) -> int:
        return sum(int(item.get("count") or 0) for item in self.discovery.values())

    @property
    def coverage(self) -> float | None:
        total = self.discovered
        return self.products / total if total else None


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _completeness(
    connection: sqlite3.Connection,
    table: str,
    fields: tuple[tuple[str, str], ...],
    total: int,
) -> list[tuple[str, float, int]]:
    """Count rows whose column holds a real value, treating [] and {} as empty."""

    rows: list[tuple[str, float, int]] = []
    for label, column in fields:
        populated = 0
        for (value,) in connection.execute(f"select {column} from {table}"):
            text = value if isinstance(value, str) else (None if value is None else str(value))
            if text is not None and text.strip() not in _EMPTY:
                populated += 1
        rows.append((label, populated / total if total else 0.0, populated))
    return rows


def _provenance(connection: sqlite3.Connection) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for table in ("products", "variants"):
        for (blob,) in connection.execute(f"select field_provenance_json from {table}"):
            if not blob:
                continue
            try:
                mapping = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if isinstance(mapping, dict):
                counter.update(str(value) for value in mapping.values())
    ordered = [(name, counter.pop(name)) for name in PROVENANCE_ORDER if name in counter]
    ordered.extend(sorted(counter.items()))
    return ordered


def _grouped(connection: sqlite3.Connection, sql: str, limit: int | None = None) -> list[tuple[str, int]]:
    rows = [(str(name), int(count)) for name, count in connection.execute(sql) if name is not None]
    return rows[:limit] if limit else rows


def collect_dashboard_data(
    *,
    sqlite_path: str | Path,
    run_report_path: str | Path | None = None,
    agreement_path: str | Path | None = None,
    generated_at: str,
    agreement_threshold: float = 0.80,
) -> DashboardData:
    run = _read_json(run_report_path)
    agreement = _read_json(agreement_path)

    data = DashboardData(
        generated_at=generated_at,
        run_status=run.get("status"),
        run_metrics=run.get("metrics") or {},
        discovery=run.get("discovery") or {},
        agreement_threshold=agreement_threshold,
    )

    connection = sqlite3.connect(f"file:{Path(sqlite_path)}?mode=ro", uri=True)
    try:
        data.products = connection.execute("select count(*) from products").fetchone()[0]
        data.variants = connection.execute("select count(*) from variants").fetchone()[0]
        data.product_completeness = _completeness(
            connection, "products", PRODUCT_FIELDS, data.products
        )
        data.variant_completeness = _completeness(
            connection, "variants", VARIANT_FIELDS, data.variants
        )
        data.provenance = _provenance(connection)
        data.price_visibility = _grouped(
            connection,
            "select price_visibility, count(*) from products"
            " group by price_visibility order by 2 desc",
        )
        data.crawl_state = _grouped(
            connection, "select status, count(*) from crawl_state group by status order by 2 desc"
        )
        data.crawl_errors = _grouped(
            connection,
            "select error_type, count(*) from crawl_errors group by error_type order by 2 desc",
        )
        data.brands = _grouped(
            connection,
            "select brand, count(*) from products where brand is not null"
            " group by brand order by 2 desc, 1 asc",
            limit=12,
        )
        data.variants_per_product = _grouped(
            connection,
            "select p.name, count(v.id) from products p join variants v"
            " on v.product_id = p.id group by p.id order by 2 desc, 1 asc",
            limit=10,
        )

        # Roll the full path up to its second level so the chart shows the
        # assigned categories rather than a long tail of leaf nodes.
        counter: Counter[str] = Counter()
        for (blob,) in connection.execute("select category_path_json from products"):
            try:
                path = json.loads(blob) if blob else []
            except json.JSONDecodeError:
                continue
            if isinstance(path, list) and len(path) >= 2:
                counter[" > ".join(str(part) for part in path[1:3])] += 1
        data.categories = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:12]
    finally:
        connection.close()

    if agreement:
        data.agreement_status = agreement.get("status")
        data.agreement_model = agreement.get("model")
        data.agreement_sample = int(agreement.get("sample_size") or 0)
        data.agreement_core = agreement.get("overall_agreement")
        data.agreement_advisory = agreement.get("advisory_agreement")
        scores: dict[str, float] = agreement.get("field_agreement") or {}
        core = set(agreement.get("core_fields") or [])
        advisory = set(agreement.get("advisory_fields") or [])
        data.agreement_core_fields = sorted(
            ((name, value) for name, value in scores.items() if name in core),
            key=lambda item: (-item[1], item[0]),
        )
        data.agreement_advisory_fields = sorted(
            ((name, value) for name, value in scores.items() if name in advisory),
            key=lambda item: (-item[1], item[0]),
        )
    return data
