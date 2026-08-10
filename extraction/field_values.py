"""Pull the known-correct value of a named field out of a successful extraction.

The repair validator needs ground truth, and a page that already extracted cleanly
is exactly that. This maps a field name onto the value the deterministic extractor
found, so any candidate locator can be measured against it.
"""

from __future__ import annotations

from models import ExtractionResult


#: Fields a locator can plausibly point at. Structural flags such as
#: ``variants_complete`` are excluded: there is no markup to locate.
REPAIRABLE_FIELDS: frozenset[str] = frozenset(
    {"name", "brand", "description", "sku", "item_number", "product_code", "price"}
)

_PRODUCT_FIELDS = {"name", "brand", "description"}


def repairable_field(name: str) -> str | None:
    """Normalize a reported failure into a locatable field name, or None."""

    candidate = name.split(":", 1)[-1].strip()
    return candidate if candidate in REPAIRABLE_FIELDS else None


def known_value(result: ExtractionResult, field_name: str) -> str | None:
    """The value this extraction established for the field, if it has one."""

    if field_name in _PRODUCT_FIELDS:
        value = getattr(result.product, field_name, None)
        return str(value).strip() or None if value else None

    for variant in result.variants:
        value = getattr(variant, field_name, None)
        if value not in (None, ""):
            text = str(value).strip()
            if text:
                return text
    return None
