"""Conservative normalization for catalog values."""

from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

_WHITESPACE = re.compile(r"\s+")
_PRICE_TOKEN = re.compile(r"(?<!\w)[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "msclkid",
}
_PACK_UNITS = {
    "bx": "box",
    "box": "box",
    "boxes": "box",
    "cs": "case",
    "case": "case",
    "cases": "case",
    "ct": "count",
    "count": "count",
    "ea": "each",
    "each": "each",
    "pc": "piece",
    "pcs": "piece",
    "piece": "piece",
    "pieces": "piece",
    "pk": "pack",
    "pkg": "pack",
    "pack": "pack",
    "packs": "pack",
    "bag": "bag",
    "bags": "bag",
    "bottle": "bottle",
    "bottles": "bottle",
    "pair": "pair",
    "pairs": "pair",
    "kit": "kit",
    "kits": "kit",
    "set": "set",
    "sets": "set",
    "roll": "roll",
    "rolls": "roll",
    "pouch": "pouch",
    "pouches": "pouch",
    "tube": "tube",
    "tubes": "tube",
    "tray": "tray",
    "trays": "tray",
    "vial": "vial",
    "vials": "vial",
}


def normalize_text(value: Any) -> str | None:
    """Decode entities, collapse whitespace, and map blank values to ``None``."""

    if value is None:
        return None
    text = html.unescape(str(value)).replace("\u00a0", " ")
    text = _WHITESPACE.sub(" ", text).strip()
    return text or None


def normalize_url(
    value: Any,
    base_url: str | None = None,
    *,
    remove_tracking: bool = True,
) -> str | None:
    """Return an HTTP(S) URL in a stable form.

    Relative URLs require ``base_url``. Fragments and common tracking parameters
    are removed, query parameters are sorted, and non-root trailing slashes are
    stripped. Invalid or unsupported URLs raise ``ValueError``.
    """

    text = normalize_text(value)
    if text is None:
        return None
    if base_url:
        text = urljoin(base_url, text)

    parsed = urlsplit(text)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"expected an absolute HTTP(S) URL, got {text!r}")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid URL port in {text!r}") from exc

    host = parsed.hostname.encode("idna").decode("ascii").lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    path = quote(path, safe="/%:@-._~!$&'()*+,;=")

    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    if remove_tracking:
        query_items = [
            (key, item)
            for key, item in query_items
            if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_PARAMETERS
        ]
    query = urlencode(sorted(query_items), doseq=True)
    return urlunsplit((scheme, host, path, query, ""))


def parse_price(value: Any) -> Decimal | None:
    """Extract a finite decimal amount from common North-American price text."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, (int, float)):
        try:
            amount = Decimal(str(value))
        except InvalidOperation:
            return None
        return amount if amount.is_finite() else None

    text = normalize_text(value)
    if text is None:
        return None
    match = _PRICE_TOKEN.search(text.replace("−", "-"))
    if not match:
        return None
    try:
        amount = Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None
    prefix = text[: match.start()]
    wrapped_negative = text.startswith("(") and text.endswith(")")
    currency_negative = bool(re.search(r"-\s*[$€£¥]?\s*$", prefix))
    if amount > 0 and (wrapped_negative or currency_negative):
        amount = -amount
    return amount if amount.is_finite() else None


def normalize_price(value: Any) -> Decimal | None:
    """Backward-compatible name for :func:`parse_price`."""

    return parse_price(value)


def normalize_pack_size(value: Any) -> str | None:
    """Normalize simple pack expressions while preserving unfamiliar text.

    Examples: ``"Box of 100"`` and ``"100 / BX"`` both become ``"100/box"``.
    """

    text = normalize_text(value)
    if text is None:
        return None

    patterns = (
        re.fullmatch(r"(?i)([a-z]+)\s+of\s+([\d,]+)", text),
        re.fullmatch(r"(?i)([\d,]+)\s*(?:/|per)\s*([a-z]+)", text),
        re.fullmatch(r"(?i)([\d,]+)\s+([a-z]+)", text),
    )
    for index, match in enumerate(patterns):
        if not match:
            continue
        if index == 0:
            unit, quantity = match.group(1).lower(), match.group(2)
        else:
            quantity, unit = match.group(1), match.group(2).lower()
        canonical_unit = _PACK_UNITS.get(unit)
        if canonical_unit:
            return f"{quantity.replace(',', '')}/{canonical_unit}"
    return text
