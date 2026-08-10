"""masterData has three meanings and the extractor must not conflate them."""

from __future__ import annotations

from pathlib import Path

import pytest

from extraction import DeterministicProductExtractor, decode_master_data
from extraction.master_data import MasterDataDecodeError


FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.safcodental.com/product/micro-touch-reg-nitrafree-trade-pink"


def _page(master_data_literal: str) -> str:
    """Minimal page carrying only what the decoder needs."""

    return (
        "<html><body><script>"
        f"window.masterData = {master_data_literal};"
        "</script></body></html>"
    )


def test_absent_assignment_returns_none() -> None:
    assert decode_master_data("<html><body></body></html>") is None


def test_empty_array_means_no_child_items_not_a_failure() -> None:
    # PHP encodes an empty associative array as [], which reaches us as "[]".
    assert decode_master_data(_page('"[]"')) == {}


def test_empty_object_is_also_no_child_items() -> None:
    assert decode_master_data(_page('"{}"')) == {}


def test_populated_mapping_is_returned() -> None:
    decoded = decode_master_data(_page('"{\\"123\\": {\\"sku\\": \\"123\\"}}"'))
    assert decoded == {"123": {"sku": "123"}}


def test_non_empty_array_still_raises() -> None:
    with pytest.raises(MasterDataDecodeError, match="empty array"):
        decode_master_data(_page('"[1, 2]"'))


def test_malformed_payload_still_raises() -> None:
    with pytest.raises(MasterDataDecodeError):
        decode_master_data(_page('"{not json"'))


def test_single_item_product_is_complete_not_degraded() -> None:
    """A family with no children is fully extracted from its JSON-LD offer."""

    html = (FIXTURES / "single_item_product.html").read_text(encoding="utf-8")
    result = DeterministicProductExtractor().extract(html, URL)

    assert result.variants_complete is True, "no child items is a complete answer"
    assert "variants_complete" not in result.missing_expected_fields
    assert result.warnings == [], "a normal single-item product raises no warning"
    assert result.method_summary["variants"] == "json_ld_single_item"

    assert len(result.variants) == 1
    variant = result.variants[0]
    assert variant.sku == "DRCDA"
    assert str(variant.price) == "15.99"
    assert variant.availability == "In Stock"
    assert result.product.category_path == [
        "Dental Supplies",
        "Dental Exam Gloves",
        "Nitrile gloves",
    ]


def test_unreadable_payload_is_still_reported_incomplete() -> None:
    """A real decode failure must stay visible rather than look like no children."""

    html = (FIXTURES / "single_item_product.html").read_text(encoding="utf-8")
    broken = html.replace('window.masterData = "[]";', 'window.masterData = "{broken";')
    assert broken != html, "fixture no longer contains the expected assignment"

    result = DeterministicProductExtractor().extract(broken, URL)

    assert result.variants_complete is False
    assert "variants_complete" in result.missing_expected_fields
    assert any("masterData" in warning for warning in result.warnings)
    assert result.method_summary["variants"] == "json_ld_fallback"
