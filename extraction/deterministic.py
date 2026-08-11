"""Deterministic Safco PDP extraction from JSON-LD and embedded masterData."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from bs4 import BeautifulSoup

from models import ExtractionResult, FieldSource, PriceVisibility, Product, ProductVariant
from utils.normalization import normalize_pack_size, normalize_text, normalize_url, parse_price

from .hashing import normalized_content_hash
from .jsonld import breadcrumb_path, parse_json_ld, select_breadcrumbs, select_product
from .master_data import MasterDataDecodeError, decode_master_data


_PACK_PATTERN = re.compile(
    r"(?i)\b(?:[a-z]+\s+of\s+\d[\d,]*|\d[\d,]*\s*(?:/|per\s+)"
    r"(?:box|bx|case|cs|pack|pk|each|ea|count|ct|bag|bottle|pair|kit|set|roll|pouch|tube|tray|vial)s?)\b"
)
_PRICE_ATTRIBUTE = re.compile(r"data-price-amount\s*=\s*['\"]([^'\"]+)", re.I)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _clean_text(value: Any) -> str | None:
    text = normalize_text(value)
    if text is None:
        return None
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    return normalize_text(text)


def _brand_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return _clean_text(value.get("name"))
    return _clean_text(value)


def _image_urls(value: Any, *, base_url: str) -> list[str]:
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for raw in values:
        if isinstance(raw, dict):
            raw = raw.get("url") or raw.get("contentUrl")
        if not raw:
            continue
        try:
            url = normalize_url(raw, base_url)
        except ValueError:
            continue
        if url and url not in result:
            result.append(url)
    return result


def _first_offer(product_node: dict[str, Any]) -> dict[str, Any]:
    offers = product_node.get("offers")
    if isinstance(offers, list):
        return next((offer for offer in offers if isinstance(offer, dict)), {})
    return offers if isinstance(offers, dict) else {}


def _offer_price(offer: dict[str, Any]) -> Decimal | None:
    return parse_price(offer.get("price") if offer.get("price") is not None else offer.get("lowPrice"))


def _availability_label(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    text = text.rsplit("/", 1)[-1].replace("_", "-").replace("-", " ")
    return normalize_text(_CAMEL_BOUNDARY.sub(" ", text))


def _price_from_variant(raw: dict[str, Any]) -> Decimal | None:
    """Safco's numeric ``product_price`` is authoritative over rendered HTML."""

    price = parse_price(raw.get("product_price"))
    if price is not None:
        return price
    price_block = raw.get("price")
    if isinstance(price_block, dict):
        candidate = price_block.get("price")
    else:
        candidate = price_block
    if isinstance(candidate, str):
        match = _PRICE_ATTRIBUTE.search(candidate)
        if match:
            return parse_price(match.group(1))
    return parse_price(candidate)


def _pack_from_variant(raw: dict[str, Any], description: str | None) -> tuple[str | None, FieldSource]:
    for key in ("pack", "pack_size", "unit_pack_size"):
        explicit = normalize_pack_size(raw.get(key))
        if explicit:
            return explicit, FieldSource.EMBEDDED_STATE
    if description:
        match = _PACK_PATTERN.search(description)
        if match:
            return normalize_pack_size(match.group(0)), FieldSource.DERIVED
    return None, FieldSource.NOT_AVAILABLE


def _visibility(price: Decimal | None, html: str) -> PriceVisibility:
    if price is not None:
        return PriceVisibility.PUBLIC
    lowered = html.casefold()
    if "login to see price" in lowered or "sign in to see price" in lowered:
        return PriceVisibility.LOGIN_REQUIRED
    return PriceVisibility.NOT_PRESENT


def _is_grouped_product_page(html: str) -> bool:
    """Return whether Safco rendered the PDP with its grouped-product template."""

    body = BeautifulSoup(html, "html.parser").body
    if body is None:
        return False
    return any(
        str(class_name).casefold() == "page-product-grouped"
        for class_name in (body.get("class") or [])
    )


def _variant_payload_supports_images(payload: dict[str, Any], images: list[str]) -> None:
    # This conditional keeps the extractor compatible while the shared model is
    # upgraded; current models expose image_urls and receive the field normally.
    if "image_urls" in ProductVariant.model_fields:
        payload["image_urls"] = images


class DeterministicProductExtractor:
    """Extract one Safco product family page into the normalized domain contract."""

    def extract(self, html: str, url: str) -> ExtractionResult:
        documents, warnings = parse_json_ld(html)
        product_node = select_product(documents, url)
        if product_node is None:
            raise ValueError(f"no Product JSON-LD found for {url}")

        canonical_url = normalize_url(product_node.get("url") or url, url)
        if canonical_url is None:
            raise ValueError("product URL is required")
        name = _clean_text(product_node.get("name"))
        if not name:
            raise ValueError("Product JSON-LD has no name")
        brand = _brand_name(product_node.get("brand"))
        description = _clean_text(product_node.get("description"))
        breadcrumbs = select_breadcrumbs(documents)
        categories = breadcrumb_path(
            breadcrumbs,
            page_url=canonical_url,
            product_name=name,
        )
        if not categories:
            category = _clean_text(product_node.get("category"))
            categories = [category] if category else []
        images = _image_urls(product_node.get("image"), base_url=canonical_url)
        offer = _first_offer(product_node)
        json_ld_price = _offer_price(offer)

        product_provenance: dict[str, FieldSource] = {
            "name": FieldSource.JSON_LD,
            "product_url": FieldSource.JSON_LD if product_node.get("url") else FieldSource.DERIVED,
            "alternative_product_urls": FieldSource.NOT_AVAILABLE,
            "specifications": FieldSource.NOT_AVAILABLE,
            "price_visibility": FieldSource.DERIVED,
        }
        for field, value in (
            ("brand", brand),
            ("description", description),
            ("category_path", categories),
            ("image_urls", images),
        ):
            if value:
                product_provenance[field] = FieldSource.JSON_LD

        variants: list[ProductVariant] = []
        try:
            master_data = decode_master_data(html)
        except MasterDataDecodeError as exc:
            master_data = None
            warnings.append(str(exc))
        # An empty mapping is the page stating this family has no child items,
        # which is a complete answer. A missing or unreadable payload is not.
        no_child_items = master_data == {}
        empty_grouped_product = no_child_items and _is_grouped_product_page(html)

        if master_data:
            for mapping_sku, raw in master_data.items():
                sku = _clean_text(raw.get("sku") or mapping_sku)
                item_number = _clean_text(raw.get("manufacturer_part_number"))
                variant_description = _clean_text(raw.get("description"))
                item_group = _clean_text(raw.get("itemgroup"))
                option_values = {
                    key: value
                    for key, value in (
                        ("description", variant_description),
                        ("itemgroup", item_group),
                    )
                    if value
                }
                pack, pack_source = _pack_from_variant(raw, variant_description)
                price = _price_from_variant(raw)
                availability = _clean_text(raw.get("stock_availability_label")) or _availability_label(
                    raw.get("stock_availability")
                )
                variant_images: list[str] = []
                for image_key in ("image", "main_image", "images"):
                    for image_url in _image_urls(raw.get(image_key), base_url=canonical_url):
                        if image_url not in variant_images:
                            variant_images.append(image_url)
                provenance: dict[str, FieldSource] = {
                    "product_url": FieldSource.DERIVED,
                    "price_visibility": FieldSource.DERIVED,
                }
                for field, value in (
                    ("sku", sku),
                    ("item_number", item_number),
                    ("option_values", option_values),
                    ("price", price),
                    ("availability", availability),
                    ("image_urls", variant_images),
                ):
                    if value not in (None, "", [], {}):
                        provenance[field] = FieldSource.EMBEDDED_STATE
                if pack:
                    provenance["unit_pack_size"] = pack_source
                payload: dict[str, Any] = {
                    "product_url": canonical_url,
                    "sku": sku,
                    "item_number": item_number,
                    "option_values": option_values,
                    "price": price,
                    "currency": _clean_text(raw.get("currency")) or _clean_text(offer.get("priceCurrency")) or "USD",
                    "price_visibility": _visibility(price, html),
                    "unit_pack_size": pack,
                    "availability": availability,
                    "field_provenance": provenance,
                }
                _variant_payload_supports_images(payload, variant_images)
                variants.append(ProductVariant.model_validate(payload))

        variants_complete = bool(variants) or no_child_items
        if empty_grouped_product:
            warnings.append(
                "grouped product page reports no child items in window.masterData"
            )
        elif not variants:
            sku = _clean_text(product_node.get("sku"))
            fallback_images = images
            fallback_price = json_ld_price
            provenance = {
                "product_url": FieldSource.JSON_LD,
                "price_visibility": FieldSource.DERIVED,
            }
            for field, value in (
                ("sku", sku),
                ("item_number", product_node.get("mpn")),
                ("price", fallback_price),
                ("currency", offer.get("priceCurrency")),
                ("availability", offer.get("availability")),
                ("image_urls", fallback_images),
            ):
                if value:
                    provenance[field] = FieldSource.JSON_LD
            fallback_payload: dict[str, Any] = {
                "product_url": canonical_url,
                "sku": sku,
                "item_number": _clean_text(product_node.get("mpn")),
                "price": fallback_price,
                "currency": _clean_text(offer.get("priceCurrency")),
                "price_visibility": _visibility(fallback_price, html),
                "availability": _availability_label(offer.get("availability")),
                "field_provenance": provenance,
            }
            _variant_payload_supports_images(fallback_payload, fallback_images)
            variants.append(ProductVariant.model_validate(fallback_payload))
            if not no_child_items:
                warnings.append(
                    "window.masterData absent or unusable; emitted JSON-LD fallback variant"
                )

        product_visibility = (
            PriceVisibility.PUBLIC
            if any(variant.price_visibility is PriceVisibility.PUBLIC for variant in variants)
            else _visibility(json_ld_price, html)
        )
        product = Product(
            name=name,
            brand=brand,
            category_path=categories,
            product_url=canonical_url,
            description=description,
            image_urls=images,
            alternative_product_urls=[],
            price_visibility=product_visibility,
            field_provenance=product_provenance,
        )
        product.content_hash = normalized_content_hash(product, variants)
        product.field_provenance["content_hash"] = FieldSource.DERIVED

        expected = {
            "brand": brand,
            "description": description,
            "category_path": categories,
            "image_urls": images,
        }
        missing = [field for field, value in expected.items() if not value]
        if not variants_complete:
            missing.append("variants_complete")
        return ExtractionResult(
            product=product,
            variants=variants,
            variants_complete=variants_complete,
            warnings=warnings,
            missing_expected_fields=missing,
            method_summary={
                "product": "json_ld",
                "variants": (
                    "embedded_state"
                    if master_data
                    else "embedded_state_empty_grouped"
                    if empty_grouped_product
                    else "json_ld_single_item"
                    if no_child_items
                    else "json_ld_fallback"
                ),
            },
        )


def extract_product(html: str, url: str) -> ExtractionResult:
    """Convenience API used by tests and the extractor agent."""

    return DeterministicProductExtractor().extract(html, url)
