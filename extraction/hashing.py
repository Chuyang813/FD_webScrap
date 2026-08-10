"""Content identities derived from normalized catalog records."""

from __future__ import annotations

from models import Product, ProductVariant
from utils.hashing import stable_json_hash


def normalized_content_hash(product: Product, variants: list[ProductVariant]) -> str:
    """Hash normalized data only; timestamps, warnings, and source HTML are excluded."""

    product_payload = product.model_dump(
        mode="json",
        exclude={"content_hash", "scraped_at"},
    )
    variant_payloads = [variant.model_dump(mode="json") for variant in variants]
    variant_payloads.sort(
        key=lambda value: (
            value.get("sku") or "",
            value.get("item_number") or "",
            value.get("product_code") or "",
            repr(value.get("option_values") or {}),
        )
    )
    return stable_json_hash({"product": product_payload, "variants": variant_payloads})
