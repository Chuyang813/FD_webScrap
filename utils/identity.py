"""Shared product-variant identity rules."""

from __future__ import annotations

from models.variant import ProductVariant

from .hashing import stable_json_hash


def variant_identity(variant: ProductVariant) -> str:
    """Prefer SKU; otherwise retain all fields needed to distinguish options."""

    if variant.sku:
        return f"sku:{variant.sku.casefold()}"
    identity = {
        "item_number": variant.item_number.casefold() if variant.item_number else None,
        "product_code": variant.product_code.casefold() if variant.product_code else None,
        "option_values": {
            key.casefold(): value.casefold()
            for key, value in sorted(
                variant.option_values.items(),
                key=lambda item: item[0].casefold(),
            )
        },
    }
    if not any((identity["item_number"], identity["product_code"], identity["option_values"])):
        return "default"
    return f"fallback:{stable_json_hash(identity)}"
