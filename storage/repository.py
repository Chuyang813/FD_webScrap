"""Idempotent persistence for products and crawl checkpoints."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from models import (
    CrawlError,
    CrawlState,
    CrawlStatus,
    ExtractionResult,
    PageType,
    Product,
    ProductVariant,
)
from utils.identity import variant_identity
from utils.normalization import normalize_text, normalize_url

from .database import Database


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _provenance(value: Mapping[str, Any]) -> dict[str, str]:
    return {key: getattr(source, "value", str(source)) for key, source in value.items()}


def build_variant_key(variant: ProductVariant) -> str:
    """Build a stable product-scoped identity, even when SKU is unavailable."""
    return variant_identity(variant)


class ProductRepository:
    def __init__(self, database: Database | str | Path = "data/catalog.db") -> None:
        self.database = database if isinstance(database, Database) else Database(database)

    def upsert_product(
        self,
        product: Product,
        variants: Iterable[ProductVariant] | None = None,
    ) -> int:
        """Insert or update a product and, optionally, its variants atomically."""

        now = _iso(_now())
        variant_list = list(variants) if variants is not None else None
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO products (
                    canonical_url, name, brand, category_path_json, description,
                    specifications_json, image_urls_json, alternative_products_json,
                    price_visibility, field_provenance_json, content_hash, scraped_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_url) DO UPDATE SET
                    name = excluded.name,
                    brand = excluded.brand,
                    category_path_json = excluded.category_path_json,
                    description = excluded.description,
                    specifications_json = excluded.specifications_json,
                    image_urls_json = excluded.image_urls_json,
                    alternative_products_json = excluded.alternative_products_json,
                    price_visibility = excluded.price_visibility,
                    field_provenance_json = excluded.field_provenance_json,
                    content_hash = excluded.content_hash,
                    scraped_at = excluded.scraped_at,
                    updated_at = excluded.updated_at
                """,
                (
                    product.product_url,
                    product.name,
                    product.brand,
                    _json(product.category_path),
                    product.description,
                    _json(product.specifications),
                    _json(product.image_urls),
                    _json(product.alternative_product_urls),
                    product.price_visibility.value,
                    _json(_provenance(product.field_provenance)),
                    product.content_hash,
                    _iso(product.scraped_at),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT id FROM products WHERE canonical_url = ?", (product.product_url,)
            ).fetchone()
            assert row is not None
            product_id = int(row["id"])
            for variant in variant_list or ():
                if variant.product_url != product.product_url:
                    raise ValueError("variant product_url does not match the product")
                self._upsert_variant(connection, product_id, variant, now)
            if variant_list is not None:
                current_keys = [build_variant_key(variant) for variant in variant_list]
                if current_keys:
                    placeholders = ",".join("?" for _ in current_keys)
                    connection.execute(
                        f"DELETE FROM variants WHERE product_id = ? AND variant_key NOT IN ({placeholders})",
                        (product_id, *current_keys),
                    )
                else:
                    connection.execute("DELETE FROM variants WHERE product_id = ?", (product_id,))
        return product_id

    def upsert_extraction(self, result: ExtractionResult) -> int:
        if not result.variants_complete:
            existing_id = self.get_product_id(result.product.product_url)
            if existing_id is not None:
                # Preserve the entire prior snapshot. Merging fallback metadata
                # with old variants would make content_hash internally false.
                return existing_id
        return self.upsert_product(result.product, result.variants)

    def _upsert_variant(
        self,
        connection: sqlite3.Connection,
        product_id: int,
        variant: ProductVariant,
        updated_at: str | None = None,
    ) -> int:
        product_row = connection.execute(
            "SELECT canonical_url FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if product_row is None:
            raise ValueError(f"unknown product id: {product_id}")
        if product_row["canonical_url"] != variant.product_url:
            raise ValueError("variant product_url does not match the product")

        variant_key = build_variant_key(variant)
        connection.execute(
            """
            INSERT INTO variants (
                product_id, variant_key, sku, item_number, product_code,
                option_values_json, image_urls_json, price, currency, price_visibility,
                unit_pack_size, availability, field_provenance_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, variant_key) DO UPDATE SET
                sku = excluded.sku,
                item_number = excluded.item_number,
                product_code = excluded.product_code,
                option_values_json = excluded.option_values_json,
                image_urls_json = excluded.image_urls_json,
                price = excluded.price,
                currency = excluded.currency,
                price_visibility = excluded.price_visibility,
                unit_pack_size = excluded.unit_pack_size,
                availability = excluded.availability,
                field_provenance_json = excluded.field_provenance_json,
                updated_at = excluded.updated_at
            """,
            (
                product_id,
                variant_key,
                variant.sku,
                variant.item_number,
                variant.product_code,
                _json(variant.option_values),
                _json(variant.image_urls),
                str(variant.price) if variant.price is not None else None,
                variant.currency,
                variant.price_visibility.value,
                variant.unit_pack_size,
                variant.availability,
                _json(_provenance(variant.field_provenance)),
                updated_at or _iso(_now()),
            ),
        )
        row = connection.execute(
            "SELECT id FROM variants WHERE product_id = ? AND variant_key = ?",
            (product_id, variant_key),
        ).fetchone()
        assert row is not None
        return int(row["id"])

    def upsert_variant(self, product_id: int, variant: ProductVariant) -> int:
        with self.database.transaction() as connection:
            return self._upsert_variant(connection, product_id, variant)

    def get_product_id(self, product_url: str) -> int | None:
        url = normalize_url(product_url)
        if url is None:
            return None
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT id FROM products WHERE canonical_url = ?", (url,)
            ).fetchone()
        return int(row["id"]) if row else None

    def get_product(self, product_url: str) -> Product | None:
        url = normalize_url(product_url)
        if url is None:
            return None
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM products WHERE canonical_url = ?", (url,)
            ).fetchone()
        return self._product_from_row(row) if row else None

    def list_products(self) -> list[Product]:
        with self.database.connection() as connection:
            rows = connection.execute("SELECT * FROM products ORDER BY id").fetchall()
        return [self._product_from_row(row) for row in rows]

    def list_variants(self, product: int | str) -> list[ProductVariant]:
        if isinstance(product, str):
            product_id = self.get_product_id(product)
            if product_id is None:
                return []
        else:
            product_id = product
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT variants.*, products.canonical_url
                FROM variants
                JOIN products ON products.id = variants.product_id
                WHERE variants.product_id = ?
                ORDER BY variants.id
                """,
                (product_id,),
            ).fetchall()
        return [self._variant_from_row(row) for row in rows]

    def list_extractions(self) -> list[ExtractionResult]:
        results: list[ExtractionResult] = []
        for product in self.list_products():
            results.append(
                ExtractionResult(product=product, variants=self.list_variants(product.product_url))
            )
        return results

    def count_products(self) -> int:
        with self.database.connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM products").fetchone()
        return int(row["count"])

    def count_variants(self) -> int:
        with self.database.connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM variants").fetchone()
        return int(row["count"])

    def enqueue_url(self, url: str, page_type: PageType = PageType.UNKNOWN) -> bool:
        canonical_url = normalize_url(url)
        if canonical_url is None:
            raise ValueError("crawl URL is required")
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO crawl_state (
                    url, page_type, status, attempt_count, updated_at
                ) VALUES (?, ?, ?, 0, ?)
                """,
                (canonical_url, page_type.value, CrawlStatus.PENDING.value, _iso(_now())),
            )
        return cursor.rowcount == 1

    def mark_url_in_progress(self, url: str, page_type: PageType | None = None) -> CrawlState:
        canonical_url = self._required_url(url)
        now = _iso(_now())
        resolved_page_type = page_type or PageType.UNKNOWN
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO crawl_state (
                    url, page_type, status, attempt_count, last_error, updated_at
                ) VALUES (?, ?, ?, 1, NULL, ?)
                ON CONFLICT(url) DO UPDATE SET
                    page_type = CASE
                        WHEN excluded.page_type = ? THEN crawl_state.page_type
                        ELSE excluded.page_type
                    END,
                    status = excluded.status,
                    attempt_count = crawl_state.attempt_count + 1,
                    last_error = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    canonical_url,
                    resolved_page_type.value,
                    CrawlStatus.IN_PROGRESS.value,
                    now,
                    PageType.UNKNOWN.value,
                ),
            )
        state = self.get_crawl_state(canonical_url)
        assert state is not None
        return state

    def set_crawl_status(
        self,
        url: str,
        status: CrawlStatus,
        *,
        attempt_count: int | None = None,
        page_type: PageType | None = None,
        last_error: str | None = None,
        last_fetched_at: datetime | None = None,
        content_hash: str | None = None,
    ) -> CrawlState:
        canonical_url = self._required_url(url)
        existing = self.get_crawl_state(canonical_url)
        resolved_page_type = page_type or (existing.page_type if existing else PageType.UNKNOWN)
        resolved_attempt_count = (
            attempt_count
            if attempt_count is not None
            else existing.attempt_count if existing else 0
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO crawl_state (
                    url, page_type, status, attempt_count, last_error,
                    last_fetched_at, content_hash, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    page_type = excluded.page_type,
                    status = excluded.status,
                    attempt_count = excluded.attempt_count,
                    last_error = excluded.last_error,
                    last_fetched_at = COALESCE(excluded.last_fetched_at, crawl_state.last_fetched_at),
                    content_hash = COALESCE(excluded.content_hash, crawl_state.content_hash),
                    updated_at = excluded.updated_at
                """,
                (
                    canonical_url,
                    resolved_page_type.value,
                    status.value,
                    resolved_attempt_count,
                    normalize_text(last_error),
                    _iso(last_fetched_at),
                    normalize_text(content_hash),
                    _iso(_now()),
                ),
            )
        state = self.get_crawl_state(canonical_url)
        assert state is not None
        return state

    def mark_url_complete(
        self,
        url: str,
        *,
        content_hash: str | None = None,
        fetched_at: datetime | None = None,
        page_type: PageType | None = None,
    ) -> CrawlState:
        return self.set_crawl_status(
            url,
            CrawlStatus.COMPLETED,
            attempt_count=0,
            page_type=page_type,
            last_fetched_at=fetched_at or _now(),
            content_hash=content_hash,
        )

    mark_url_completed = mark_url_complete

    def mark_url_failed(self, url: str, error: str) -> CrawlState:
        return self.set_crawl_status(url, CrawlStatus.FAILED, last_error=error)

    def mark_url_skipped(self, url: str, reason: str | None = None) -> CrawlState:
        return self.set_crawl_status(url, CrawlStatus.SKIPPED, last_error=reason)

    def get_crawl_state(self, url: str) -> CrawlState | None:
        canonical_url = normalize_url(url)
        if canonical_url is None:
            return None
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM crawl_state WHERE url = ?", (canonical_url,)
            ).fetchone()
        return self._crawl_state_from_row(row) if row else None

    def list_crawl_states(self, status: CrawlStatus | None = None) -> list[CrawlState]:
        query = "SELECT * FROM crawl_state"
        parameters: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (status.value,)
        query += " ORDER BY updated_at, url"
        with self.database.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._crawl_state_from_row(row) for row in rows]

    def resumable_urls(self, *, max_attempts: int = 3) -> list[str]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT url FROM crawl_state
                WHERE status IN (?, ?) OR (status = ? AND attempt_count <= ?)
                ORDER BY updated_at, url
                """,
                (
                    CrawlStatus.PENDING.value,
                    CrawlStatus.IN_PROGRESS.value,
                    CrawlStatus.FAILED.value,
                    max_attempts,
                ),
            ).fetchall()
        return [str(row["url"]) for row in rows]

    def record_error(
        self,
        url: str,
        error_type: str,
        error_message: str,
        *,
        attempt_number: int | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> int:
        canonical_url = self._required_url(url)
        state = self.get_crawl_state(canonical_url)
        attempt = attempt_number or (state.attempt_count if state and state.attempt_count else 1)
        error = CrawlError(
            url=canonical_url,
            error_type=error_type,
            error_message=error_message,
            attempt_number=attempt,
            context=dict(context or {}),
        )
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO crawl_errors (
                    url, error_type, error_message, attempt_number, created_at, context_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    error.url,
                    error.error_type,
                    error.error_message,
                    error.attempt_number,
                    _iso(error.created_at),
                    _json(error.context),
                ),
            )
        return int(cursor.lastrowid)

    def list_errors(self, url: str | None = None) -> list[CrawlError]:
        query = "SELECT * FROM crawl_errors"
        parameters: tuple[str, ...] = ()
        if url is not None:
            query += " WHERE url = ?"
            parameters = (self._required_url(url),)
        query += " ORDER BY id"
        with self.database.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            CrawlError(
                id=row["id"],
                url=row["url"],
                error_type=row["error_type"],
                error_message=row["error_message"],
                attempt_number=row["attempt_number"],
                created_at=row["created_at"],
                context=_load_json(row["context_json"], {}),
            )
            for row in rows
        ]

    @staticmethod
    def _required_url(url: str) -> str:
        canonical_url = normalize_url(url)
        if canonical_url is None:
            raise ValueError("URL is required")
        return canonical_url

    @staticmethod
    def _product_from_row(row: sqlite3.Row) -> Product:
        return Product(
            name=row["name"],
            brand=row["brand"],
            category_path=_load_json(row["category_path_json"], []),
            product_url=row["canonical_url"],
            description=row["description"],
            specifications=_load_json(row["specifications_json"], {}),
            image_urls=_load_json(row["image_urls_json"], []),
            alternative_product_urls=_load_json(row["alternative_products_json"], []),
            price_visibility=row["price_visibility"],
            field_provenance=_load_json(row["field_provenance_json"], {}),
            content_hash=row["content_hash"],
            scraped_at=row["scraped_at"],
        )

    @staticmethod
    def _variant_from_row(row: sqlite3.Row) -> ProductVariant:
        return ProductVariant(
            product_url=row["canonical_url"],
            sku=row["sku"],
            item_number=row["item_number"],
            product_code=row["product_code"],
            option_values=_load_json(row["option_values_json"], {}),
            image_urls=_load_json(row["image_urls_json"], []),
            price=row["price"],
            currency=row["currency"],
            price_visibility=row["price_visibility"],
            unit_pack_size=row["unit_pack_size"],
            availability=row["availability"],
            field_provenance=_load_json(row["field_provenance_json"], {}),
        )

    @staticmethod
    def _crawl_state_from_row(row: sqlite3.Row) -> CrawlState:
        return CrawlState(
            url=row["url"],
            page_type=row["page_type"],
            status=row["status"],
            attempt_count=row["attempt_count"],
            last_error=row["last_error"],
            last_fetched_at=row["last_fetched_at"],
            content_hash=row["content_hash"],
            updated_at=row["updated_at"],
        )


Repository = ProductRepository
