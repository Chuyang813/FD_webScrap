"""The dashboard must report the catalogue accurately and stay self-contained."""

from __future__ import annotations

import json
import re
from pathlib import Path

from models import ExtractionResult, FieldSource, PriceVisibility, Product, ProductVariant
from reporting import collect_dashboard_data, render_dashboard, write_dashboard
from storage import ProductRepository


URL = "https://example.com/product/gloves"


def _product(url: str = URL, *, with_specs: bool = False) -> Product:
    return Product(
        name="Nitrile Gloves",
        brand="Safco",
        category_path=["Dental Supplies", "Dental Exam Gloves", "Nitrile gloves"],
        product_url=url,
        description="Powder-free.",
        specifications={"Material": "Nitrile"} if with_specs else {},
        image_urls=["https://example.com/a.jpg"],
        alternative_product_urls=[],
        price_visibility=PriceVisibility.PUBLIC,
        field_provenance={"name": FieldSource.JSON_LD, "specifications": FieldSource.NOT_AVAILABLE},
    )


def _variant(sku: str, url: str = URL) -> ProductVariant:
    return ProductVariant(
        product_url=url,
        sku=sku,
        price="19.99",
        currency="USD",
        price_visibility=PriceVisibility.PUBLIC,
        availability="In stock",
        field_provenance={"sku": FieldSource.EMBEDDED_STATE, "price": FieldSource.EMBEDDED_STATE},
    )


def _seed(tmp_path: Path) -> Path:
    db = tmp_path / "catalog.db"
    repository = ProductRepository(db)
    repository.upsert_product(_product(), [_variant("A-1"), _variant("A-2")])
    repository.upsert_product(
        _product("https://example.com/product/sutures", with_specs=True),
        [_variant("B-1", "https://example.com/product/sutures")],
    )
    return db


def _collect(tmp_path: Path, **kwargs):
    return collect_dashboard_data(
        sqlite_path=_seed(tmp_path), generated_at="2026-08-10 00:00 UTC", **kwargs
    )


def test_counts_and_completeness_treat_empty_json_as_missing(tmp_path: Path) -> None:
    data = _collect(tmp_path)

    assert data.products == 2
    assert data.variants == 3

    completeness = dict((name, fraction) for name, fraction, _ in data.product_completeness)
    assert completeness["name"] == 1.0
    # One product has specifications; an empty {} must not count as populated.
    assert completeness["specifications"] == 0.5
    # Both have an empty alternatives list, stored as "[]".
    assert completeness["alternative_products"] == 0.0


def test_provenance_counts_span_products_and_variants(tmp_path: Path) -> None:
    data = _collect(tmp_path)
    counts = dict(data.provenance)

    assert counts["json_ld"] == 2, "one per product"
    assert counts["not_available"] == 2
    assert counts["embedded_state"] == 6, "two fields across three variants"
    # json_ld leads the documented display order.
    assert data.provenance[0][0] == "json_ld"


def test_coverage_uses_discovery_counts_from_the_run_report(tmp_path: Path) -> None:
    run = tmp_path / "run.json"
    run.write_text(
        json.dumps(
            {
                "status": "completed",
                "metrics": {"failures": 0},
                "discovery": {"gloves": {"count": 4, "method": "algolia", "status": "completed"}},
            }
        ),
        encoding="utf-8",
    )
    data = _collect(tmp_path, run_report_path=run)

    assert data.discovered == 4
    assert data.coverage == 0.5
    assert data.run_status == "completed"


def test_missing_reports_degrade_without_raising(tmp_path: Path) -> None:
    data = _collect(tmp_path, run_report_path=tmp_path / "absent.json")

    assert data.coverage is None
    assert data.agreement_status is None
    html = render_dashboard(data)
    assert "No shadow comparison is available" in html


def test_agreement_is_split_into_core_and_advisory(tmp_path: Path) -> None:
    agreement = tmp_path / "agreement.json"
    agreement.write_text(
        json.dumps(
            {
                "status": "partial",
                "model": "test-model",
                "sample_size": 12,
                "overall_agreement": 0.997,
                "advisory_agreement": 0.349,
                "core_fields": ["sku", "price"],
                "advisory_fields": ["category_path"],
                "field_agreement": {"sku": 1.0, "price": 0.99, "category_path": 0.0},
            }
        ),
        encoding="utf-8",
    )
    data = _collect(tmp_path, agreement_path=agreement)

    assert data.agreement_core == 0.997
    assert data.agreement_advisory == 0.349
    assert [name for name, _ in data.agreement_core_fields] == ["sku", "price"]
    assert [name for name, _ in data.agreement_advisory_fields] == ["category_path"]


def test_rendered_page_is_self_contained(tmp_path: Path) -> None:
    html = render_dashboard(_collect(tmp_path))

    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    for pattern in (r"<script", r'src\s*=', r'href\s*=\s*["\']http', r"@import", r"url\(http"):
        assert not re.search(pattern, html, re.I), f"page must not reference {pattern}"


def test_page_declares_both_themes(tmp_path: Path) -> None:
    html = render_dashboard(_collect(tmp_path))

    assert "prefers-color-scheme: dark" in html
    assert ':root[data-theme="dark"]' in html


def test_every_chart_ships_a_table_view(tmp_path: Path) -> None:
    """The table view is the accessibility fallback for every chart section."""

    html = render_dashboard(_collect(tmp_path))

    charts = html.count('<div class="rows">') + html.count('<div class="stack">')
    assert charts >= 3
    assert html.count("Table view") >= 3


def test_write_dashboard_creates_the_file(tmp_path: Path) -> None:
    target = write_dashboard(
        tmp_path / "nested" / "dashboard.html", sqlite_path=_seed(tmp_path)
    )

    assert target.exists()
    assert "Safco catalogue" in target.read_text(encoding="utf-8")
