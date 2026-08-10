"""SQLite bootstrap and transaction handling."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_url TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    brand TEXT,
    category_path_json TEXT NOT NULL DEFAULT '[]',
    description TEXT,
    specifications_json TEXT NOT NULL DEFAULT '{}',
    image_urls_json TEXT NOT NULL DEFAULT '[]',
    alternative_products_json TEXT NOT NULL DEFAULT '[]',
    price_visibility TEXT NOT NULL,
    field_provenance_json TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT,
    scraped_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    variant_key TEXT NOT NULL,
    sku TEXT,
    item_number TEXT,
    product_code TEXT,
    option_values_json TEXT NOT NULL DEFAULT '{}',
    image_urls_json TEXT NOT NULL DEFAULT '[]',
    price TEXT,
    currency TEXT,
    price_visibility TEXT NOT NULL,
    unit_pack_size TEXT,
    availability TEXT,
    field_provenance_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    UNIQUE(product_id, variant_key)
);

CREATE TABLE IF NOT EXISTS crawl_state (
    url TEXT PRIMARY KEY,
    page_type TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    last_error TEXT,
    last_fetched_at TEXT,
    content_hash TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crawl_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
    created_at TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_variants_product_id ON variants(product_id);
CREATE INDEX IF NOT EXISTS idx_crawl_state_status ON crawl_state(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_crawl_errors_url ON crawl_errors(url, created_at);
"""


class Database:
    """A small connection manager that also supports ``:memory:`` tests."""

    def __init__(self, path: str | Path = "data/catalog.db") -> None:
        self.path = str(path)
        self._lock = RLock()
        self._memory_connection: sqlite3.Connection | None = None
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self.path == ":memory:":
                if self._memory_connection is None:
                    self._memory_connection = self._open()
                yield self._memory_connection
                return

            connection = self._open()
            try:
                yield connection
            finally:
                connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            try:
                connection.execute("BEGIN")
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            connection.commit()

    def close(self) -> None:
        with self._lock:
            if self._memory_connection is not None:
                self._memory_connection.close()
                self._memory_connection = None

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def initialize_database(path: str | Path = "data/catalog.db") -> Database:
    return Database(path)
