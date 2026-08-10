from decimal import Decimal

import pytest

from utils.normalization import normalize_pack_size, normalize_text, normalize_url, parse_price


def test_normalize_text_collapses_whitespace_and_entities() -> None:
    assert normalize_text("  Nitrile\u00a0 &amp;  Latex\n") == "Nitrile & Latex"
    assert normalize_text("   ") is None
    assert normalize_text(None) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$19.99", Decimal("19.99")),
        ("CAD 1,234.50 / case", Decimal("1234.50")),
        (Decimal("4.20"), Decimal("4.20")),
        ("Login to see price", None),
        ("", None),
    ],
)
def test_parse_price(raw: object, expected: Decimal | None) -> None:
    assert parse_price(raw) == expected


def test_normalize_url_canonicalizes_and_removes_tracking() -> None:
    assert normalize_url(
        "HTTPS://WWW.SAFCoDental.com:443/catalog/gloves/?b=2&utm_source=x&a=1#details"
    ) == "https://www.safcodental.com/catalog/gloves?a=1&b=2"
    assert normalize_url("../item/42/", "https://example.com/catalog/gloves/") == (
        "https://example.com/catalog/item/42"
    )


def test_normalize_url_rejects_non_http_urls() -> None:
    with pytest.raises(ValueError):
        normalize_url("javascript:alert(1)")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Box of 100", "100/box"),
        ("100 / BX", "100/box"),
        ("50 per Case", "50/case"),
        ("  assorted starter kit ", "assorted starter kit"),
        ("", None),
    ],
)
def test_normalize_pack_size(raw: str, expected: str | None) -> None:
    assert normalize_pack_size(raw) == expected


def test_normalize_realistic_bag_pack_size() -> None:
    assert normalize_pack_size("100/bag") == "100/bag"
