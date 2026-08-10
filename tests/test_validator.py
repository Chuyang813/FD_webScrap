from decimal import Decimal

import pytest
from pydantic import ValidationError

from agents.validator import ValidatorAgent
from models import ExtractionResult, FieldSource, PriceVisibility, Product, ProductVariant


def test_product_normalizes_fields_and_deduplicates_images() -> None:
    product = Product(
        name="  Safco   Exam Gloves ",
        product_url="https://example.com/gloves/1/",
        category_path=[" Gloves ", "", " Nitrile  Gloves"],
        image_urls=[
            "https://example.com/a.jpg#hero",
            "https://example.com/a.jpg",
        ],
        field_provenance={"name": "dom"},
    )

    assert product.name == "Safco Exam Gloves"
    assert product.product_url == "https://example.com/gloves/1"
    assert product.category_path == ["Gloves", "Nitrile Gloves"]
    assert product.image_urls == ["https://example.com/a.jpg"]
    assert product.field_provenance["name"] is FieldSource.DOM


def test_product_requires_name_and_valid_url() -> None:
    with pytest.raises(ValidationError):
        Product(name="  ", product_url="https://example.com/product")
    with pytest.raises(ValidationError):
        Product(name="Gloves", product_url="not-a-url")


def test_variant_parses_price_pack_and_variant_images() -> None:
    variant = ProductVariant(
        product_url="https://example.com/product",
        sku="  SKU-1 ",
        price="$21.50",
        currency="cad",
        unit_pack_size="Box of 100",
        image_urls=["https://example.com/blue.jpg", "https://example.com/blue.jpg#x"],
        price_visibility=PriceVisibility.PUBLIC,
    )

    assert variant.sku == "SKU-1"
    assert variant.price == Decimal("21.50")
    assert variant.currency == "CAD"
    assert variant.unit_pack_size == "100/box"
    assert variant.image_urls == ["https://example.com/blue.jpg"]


def test_variant_rejects_negative_price() -> None:
    with pytest.raises(ValidationError):
        ProductVariant(product_url="https://example.com/product", price="-$1.00")


def test_mutable_defaults_are_not_shared() -> None:
    first = Product(name="One", product_url="https://example.com/one")
    second = Product(name="Two", product_url="https://example.com/two")
    first.specifications["Material"] = "Nitrile"

    assert second.specifications == {}
    assert ExtractionResult(product=first).warnings == []


def test_validator_keeps_same_item_number_with_different_options() -> None:
    product = Product(name="Gloves", product_url="https://example.com/product/gloves")
    variants = [
        ProductVariant(
            product_url=product.product_url,
            item_number="MFG-42",
            option_values={"Size": size},
        )
        for size in ("Small", "Large")
    ]

    report = ValidatorAgent().validate(ExtractionResult(product=product, variants=variants))

    assert report.valid
    assert report.duplicate_variants_removed == 0
    assert len(report.result.variants) == 2  # type: ignore[union-attr]
