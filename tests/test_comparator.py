from copy import deepcopy
from decimal import Decimal

from extraction.comparator import ExtractionComparator, compare_results
from models import ExtractionResult, PriceVisibility, Product, ProductVariant


def result(*, name: str = "Exam Gloves", price: str = "19.99", images: list[str] | None = None) -> ExtractionResult:
    url = "https://example.com/product/gloves"
    return ExtractionResult(
        product=Product(
            name=name,
            brand="Safco",
            product_url=url,
            description="Powder-free nitrile examination gloves.",
            specifications={"Material": "Nitrile"},
            image_urls=images or ["https://example.com/b.jpg", "https://example.com/a.jpg"],
            price_visibility=PriceVisibility.PUBLIC,
        ),
        variants=[
            ProductVariant(
                product_url=url,
                sku="SKU-1",
                item_number="MFG-1",
                option_values={"size": "Medium"},
                image_urls=["https://example.com/m.jpg"],
                price=price,
                currency="USD",
                price_visibility=PriceVisibility.PUBLIC,
                unit_pack_size="100/box",
                availability="In stock",
            )
        ],
    )


def test_equivalent_normalized_values_and_sets_agree() -> None:
    deterministic = result()
    shadow = result(name="  exam   gloves ", images=["https://example.com/a.jpg", "https://example.com/b.jpg"])
    report = compare_results(deterministic, shadow)
    assert report.terminology == "cross-extractor agreement"
    assert report.field_agreement["name"] == 1
    assert report.field_agreement["image_urls"] == 1
    assert report.field_agreement["sku"] == 1
    assert report.overall_agreement == 1


def test_price_uses_numeric_tolerance_and_missing_field_disagrees() -> None:
    deterministic = result(price="19.99")
    shadow = result(price="20.00")
    shadow.variants[0].item_number = None
    report = ExtractionComparator(price_tolerance=Decimal("0.01")).compare(deterministic, shadow)
    assert report.field_agreement["price"] == 1
    assert report.field_agreement["item_number"] == 0
    # item_number is advisory, so it lowers the advisory score but must not move
    # the headline value that the drift threshold reads.
    assert report.advisory_agreement < 1
    assert report.overall_agreement == 1


def test_core_disagreement_lowers_the_headline_score() -> None:
    deterministic = result()
    shadow = result()
    shadow.product.brand = "Different"

    report = compare_results(deterministic, shadow)

    assert report.field_agreement["brand"] == 0
    assert report.overall_agreement < 1, "a core field must move the threshold value"


def test_advisory_disagreement_alone_never_triggers_drift() -> None:
    """Representation differences must not be able to manufacture an alert."""

    deterministic = result()
    shadow = result()
    shadow.product.category_path = ["Something", "Else"]
    shadow.variants[0].option_values = {"pack": "100 per box"}
    shadow.variants[0].item_number = "OTHER"
    shadow.variants[0].unit_pack_size = "box of 100"

    report = compare_results(deterministic, shadow)

    assert report.advisory_agreement < 1, "advisory fields should register the difference"
    assert report.overall_agreement == 1, "core fields, and the threshold, stay untouched"
    assert {item.tier for item in report.comparisons} == {"core", "advisory"}
    for name in ("category_path", "option_values", "item_number"):
        assert report.field_agreement[name] < 1


def test_equivalent_pack_size_wording_is_normalized_before_comparison() -> None:
    """"box of 100" and "100/box" are the same fact, not a disagreement."""

    deterministic = result()
    shadow = result()
    shadow.variants[0].unit_pack_size = "box of 100"

    report = compare_results(deterministic, shadow)

    assert report.field_agreement["unit_pack_size"] == 1


def test_availability_label_punctuation_is_not_a_disagreement() -> None:
    deterministic = result()
    shadow = result()
    shadow.variants[0].availability = "InStock"

    report = compare_results(deterministic, shadow)

    assert report.field_agreement["availability"] == 1


def test_batch_report_records_sample_size_and_field_agreement() -> None:
    left = result()
    exact = result()
    changed = deepcopy(exact)
    changed.product.brand = "Different"
    report = ExtractionComparator().summarize([(left, exact), (left, changed)])
    assert report.sample_size == 2
    assert report.field_agreement["brand"] == 0.5
    assert set(report.model_dump()) == {
        "terminology",
        "sample_size",
        "overall_agreement",
        "advisory_agreement",
        "field_agreement",
        "core_fields",
        "advisory_fields",
        "products",
    }
    assert "sku" in report.core_fields
    assert "category_path" in report.advisory_fields


def test_category_and_missing_variant_are_disagreements() -> None:
    deterministic = result()
    shadow = result()
    shadow.product.category_path = ["Different"]
    shadow.variants = []

    report = compare_results(deterministic, shadow)

    assert report.field_agreement["category_path"] == 0
    assert report.field_agreement["currency"] == 0
    assert report.field_agreement["variant_image_urls"] == 0
