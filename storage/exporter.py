"""Reviewer-friendly JSON and one-row-per-variant CSV exports."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from models import ExtractionResult, ProductVariant

from .repository import ProductRepository

CSV_COLUMNS = [
    "product_name",
    "brand",
    "category_path",
    "product_url",
    "sku",
    "item_number",
    "product_code",
    "variant_options_json",
    "price",
    "currency",
    "price_visibility",
    "unit_pack_size",
    "availability",
    "description",
    "specifications_json",
    "image_urls_json",
    "variant_image_urls_json",
    "alternative_products_json",
    "field_provenance_json",
    "scraped_at",
]


def _results(source: ProductRepository | Iterable[ExtractionResult]) -> list[ExtractionResult]:
    if isinstance(source, ProductRepository):
        return source.list_extractions()
    return list(source)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def export_products_json(
    source: ProductRepository | Iterable[ExtractionResult],
    destination: str | Path,
) -> Path:
    """Export nested product records, retaining their variant arrays."""

    records: list[dict[str, Any]] = []
    for result in _results(source):
        record = result.product.model_dump(mode="json")
        record["variants"] = [variant.model_dump(mode="json") for variant in result.variants]
        records.append(record)

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_products_csv(
    source: ProductRepository | Iterable[ExtractionResult],
    destination: str | Path,
) -> Path:
    """Export one CSV row per variant (or one blank-variant row per product)."""

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for result in _results(source):
            variants: list[ProductVariant | None] = result.variants or [None]
            for variant in variants:
                writer.writerow(_csv_row(result, variant))
    return path


def _csv_row(result: ExtractionResult, variant: ProductVariant | None) -> dict[str, Any]:
    product = result.product
    product_provenance = product.model_dump(mode="json")["field_provenance"]
    variant_provenance = (
        variant.model_dump(mode="json")["field_provenance"] if variant is not None else {}
    )
    return {
        "product_name": product.name,
        "brand": product.brand or "",
        "category_path": " > ".join(product.category_path),
        "product_url": product.product_url,
        "sku": variant.sku if variant else "",
        "item_number": variant.item_number if variant else "",
        "product_code": variant.product_code if variant else "",
        "variant_options_json": _json_dump(variant.option_values if variant else {}),
        "price": str(variant.price) if variant and variant.price is not None else "",
        "currency": variant.currency if variant else "",
        "price_visibility": (
            variant.price_visibility.value if variant else product.price_visibility.value
        ),
        "unit_pack_size": variant.unit_pack_size if variant else "",
        "availability": variant.availability if variant else "",
        "description": product.description or "",
        "specifications_json": _json_dump(product.specifications),
        "image_urls_json": _json_dump(product.image_urls),
        "variant_image_urls_json": _json_dump(variant.image_urls if variant else []),
        "alternative_products_json": _json_dump(product.alternative_product_urls),
        "field_provenance_json": _json_dump(
            {"product": product_provenance, "variant": variant_provenance}
        ),
        "scraped_at": product.scraped_at.isoformat(),
    }


class CatalogExporter:
    def __init__(self, repository: ProductRepository) -> None:
        self.repository = repository

    def export_json(self, destination: str | Path) -> Path:
        return export_products_json(self.repository, destination)

    def export_csv(self, destination: str | Path) -> Path:
        return export_products_csv(self.repository, destination)
