import csv
import json

from models import (
    CrawlStatus,
    ExtractionResult,
    FieldSource,
    PageType,
    PriceVisibility,
    Product,
    ProductVariant,
)
from storage import ProductRepository, build_variant_key, export_products_csv, export_products_json


def make_product(name: str = "Nitrile Gloves") -> Product:
    return Product(
        name=name,
        brand="Safco",
        category_path=["Gloves", "Exam Gloves"],
        product_url="https://example.com/catalog/gloves/42",
        specifications={"Material": "Nitrile"},
        image_urls=["https://example.com/images/42.jpg"],
        price_visibility=PriceVisibility.PUBLIC,
        field_provenance={"name": FieldSource.DOM},
        content_hash="abc123",
    )


def make_variant(sku: str | None, size: str, price: str) -> ProductVariant:
    return ProductVariant(
        product_url="https://example.com/catalog/gloves/42",
        sku=sku,
        item_number="42" if sku is None else None,
        option_values={"Size": size},
        image_urls=[f"https://example.com/images/{size.lower()}.jpg"],
        price=price,
        currency="USD",
        price_visibility=PriceVisibility.PUBLIC,
        unit_pack_size="100 / box",
        field_provenance={"price": FieldSource.API},
    )


def test_product_and_variant_upserts_are_idempotent_and_replace_stale_rows(tmp_path) -> None:
    repository = ProductRepository(tmp_path / "catalog.db")
    product = make_product()
    small = make_variant("SKU-S", "Small", "10.00")
    medium = make_variant("SKU-M", "Medium", "12.00")

    first_id = repository.upsert_product(product, [small, medium])
    second_id = repository.upsert_product(make_product("Updated Gloves"), [
        make_variant("SKU-M", "Medium", "13.00")
    ])

    assert first_id == second_id
    assert repository.count_products() == 1
    assert repository.count_variants() == 1
    assert repository.get_product(product.product_url).name == "Updated Gloves"  # type: ignore[union-attr]
    variants = repository.list_variants(first_id)
    assert variants[0].sku == "SKU-M"
    assert str(variants[0].price) == "13.00"
    assert variants[0].image_urls == ["https://example.com/images/medium.jpg"]


def test_fallback_variant_key_is_stable() -> None:
    first = make_variant(None, "Large", "9.99")
    second = ProductVariant(
        product_url=first.product_url,
        item_number="42",
        option_values={"size": "large"},
        price="20.00",
    )

    assert build_variant_key(first) == build_variant_key(second)
    assert build_variant_key(first).startswith("fallback:")


def test_crawl_state_transitions_resume_and_error_history(tmp_path) -> None:
    repository = ProductRepository(tmp_path / "catalog.db")
    url = "https://example.com/product/1"

    assert repository.enqueue_url(url, PageType.PRODUCT)
    assert not repository.enqueue_url(url, PageType.PRODUCT)
    state = repository.mark_url_in_progress(url)
    assert state.status is CrawlStatus.IN_PROGRESS
    assert state.attempt_count == 1
    assert repository.resumable_urls(max_attempts=3) == [url]

    repository.mark_url_failed(url, "temporary timeout")
    error_id = repository.record_error(
        url,
        "TimeoutError",
        "temporary timeout",
        context={"fetch_source": "http"},
    )
    assert error_id == 1
    assert repository.resumable_urls(max_attempts=3) == [url]
    assert repository.list_errors(url)[0].context == {"fetch_source": "http"}

    completed = repository.mark_url_complete(url, content_hash="hash")
    assert completed.status is CrawlStatus.COMPLETED
    assert completed.attempt_count == 0
    assert completed.last_error is None
    assert repository.resumable_urls() == []


def test_incomplete_extraction_does_not_overwrite_complete_snapshot(tmp_path) -> None:
    repository = ProductRepository(tmp_path / "catalog.db")
    original = make_product("Complete Gloves")
    repository.upsert_product(
        original,
        [make_variant("SKU-S", "Small", "10.00"), make_variant("SKU-M", "Medium", "11.00")],
    )
    degraded = ExtractionResult(
        product=make_product("Fallback Metadata"),
        variants=[make_variant("PARENT", "Unknown", "9.00")],
        variants_complete=False,
    )

    repository.upsert_extraction(degraded)

    assert repository.get_product(original.product_url).name == "Complete Gloves"  # type: ignore[union-attr]
    assert {item.sku for item in repository.list_variants(original.product_url)} == {
        "SKU-S",
        "SKU-M",
    }


def test_json_and_csv_exports_preserve_variants(tmp_path) -> None:
    repository = ProductRepository(tmp_path / "catalog.db")
    repository.upsert_product(make_product(), [make_variant("SKU-M", "Medium", "12.00")])

    json_path = export_products_json(repository, tmp_path / "out" / "products.json")
    csv_path = export_products_csv(repository, tmp_path / "out" / "products.csv")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload[0]["variants"][0]["sku"] == "SKU-M"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["product_name"] == "Nitrile Gloves"
    assert rows[0]["sku"] == "SKU-M"


def test_database_contains_only_the_four_domain_tables(tmp_path) -> None:
    repository = ProductRepository(tmp_path / "catalog.db")
    with repository.database.connection() as connection:
        names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert names == {"products", "variants", "crawl_state", "crawl_errors"}
