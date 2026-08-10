from decimal import Decimal
from pathlib import Path

from extraction import DeterministicProductExtractor, decode_master_data
from models import FieldSource, PriceVisibility


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_glove_master_data_decodes_twice_and_extracts_four_variants() -> None:
    html = fixture("glove_product.html")
    decoded = decode_master_data(html)
    assert decoded is not None
    assert list(decoded) == ["4710010", "4710018", "4710024", "4710036"]

    result = DeterministicProductExtractor().extract(
        html,
        "https://www.safcodental.com/product/silkcare-reg?utm_source=test",
    )
    assert result.product.name == "Silkcare"
    assert result.product.category_path == ["Dental Supplies", "Dental Exam Gloves", "Latex gloves"]
    assert result.product.price_visibility is PriceVisibility.PUBLIC
    assert len(result.variants) == 4
    first = result.variants[0]
    assert first.sku == "4710010"
    assert first.item_number == "CR7815"
    assert first.option_values == {"description": "x-small, 100/box"}
    assert first.unit_pack_size == "100/box"
    assert first.availability == "In stock"
    assert first.price == Decimal("18.990000")  # product_price wins over rendered HTML
    assert first.field_provenance["price"] is FieldSource.EMBEDDED_STATE


def test_suture_fixture_preserves_item_group_and_multiple_variants() -> None:
    result = DeterministicProductExtractor().extract(
        fixture("suture_product.html"),
        "https://www.safcodental.com/product/perma-sharp-reg-sutures",
    )
    assert len(result.variants) == 3
    assert result.variants[0].option_values == {
        "description": 'C-6, plain gut, 5-0, 27", 12/box',
        "itemgroup": "C-6 needle (3/8 circle, premium reverse cut)",
    }
    assert result.variants[0].unit_pack_size == "12/box"
    assert result.variants[2].availability == "Direct from manufacturer"


def test_json_ld_only_page_emits_fallback_variant() -> None:
    result = DeterministicProductExtractor().extract(
        fixture("jsonld_only_product.html"),
        "https://www.safcodental.com/product/jsonld-only",
    )
    assert len(result.variants) == 1
    assert result.variants[0].sku == "ONLY-1"
    assert result.variants[0].price == Decimal("12.50")
    assert result.method_summary["variants"] == "json_ld_fallback"
    assert "fallback variant" in result.warnings[-1]
    assert result.variants_complete is False
    assert "variants_complete" in result.missing_expected_fields


def test_content_hash_uses_normalized_extraction_not_raw_html() -> None:
    html = fixture("glove_product.html")
    extractor = DeterministicProductExtractor()
    first = extractor.extract(html, "https://www.safcodental.com/product/silkcare-reg")
    second = extractor.extract(
        html.replace("<body>", "<body>\n<!-- irrelevant raw HTML -->\n"),
        "https://www.safcodental.com/product/silkcare-reg",
    )
    assert first.product.content_hash == second.product.content_hash
