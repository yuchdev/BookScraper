"""
Storage backend selection: --store-backend {mongo|json} picks a single backend used
for BOTH reads (duplicate checks) and writes (saving scraped books) - see CLAUDE.md's
"Storage backend" section.

Replaces the old resolve_output_destinations()/OutputDestinations dual-write design
(-c/--output-to-csv + -m/--output-to-mongo), which let CSV and MongoDB both be written
per run, with CSV as a disposable, per-run-overwritten dump that was never consulted
for duplicate checks. --store-backend makes the choice explicit and singular, and both
backends now implement identical read+write behavior - see local/store.py's JSON store
(persistent, accumulates across runs, schema-identical to a MongoDB document) vs.
mongo/database.py's MongoDB collection.
"""

from __future__ import annotations

import logging
import pathlib
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from pymongo.collection import Collection

from ..book_utils import check_local_write_permission, print_log
from .local import store
from .mongo import config, database, deduplicate
from .mongo.cert_rotation import cert_expiry_warning

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SaveSummary:
    """
    Outcome of a :meth:`StorageBackend.save_books` call, identical in shape for
    both backends so a caller can distinguish a 100%-duplicate batch from a total
    failure (which it could not when ``save_books`` returned ``None``).

    :ivar inserted: Documents newly written.
    :ivar duplicates: Documents skipped because their ``hash`` already exists.
    :ivar errors: Documents rejected for any other reason.
    """

    inserted: int
    duplicates: int
    errors: int

    @property
    def total(self) -> int:
        """Total documents accounted for: ``inserted + duplicates + errors``."""
        return self.inserted + self.duplicates + self.errors


class StorageBackend(ABC):
    """Uniform read/write contract shared by the mongo and json backends."""

    @abstractmethod
    def save_books(self, books: list[dict]) -> SaveSummary: ...

    @abstractmethod
    def book_exists_by_hash(self, book_hash: str) -> bool: ...

    @abstractmethod
    def book_exists_by_asin(self, asin: str) -> bool: ...

    @abstractmethod
    def leanpub_book_exists(self, book_id: Optional[str], book_slug: Optional[str]) -> bool: ...

    def close(self) -> None:  # noqa: B027 - intentional no-op default, not every backend holds a connection
        """Optional cleanup hook; no-op unless a backend holds a live connection."""


class MongoBackend(StorageBackend):
    def __init__(self, collection: Collection) -> None:
        self._collection = collection

    def save_books(self, books: list[dict]) -> SaveSummary:
        return database.save_books_to_mongodb(books, self._collection)

    def book_exists_by_hash(self, book_hash: str) -> bool:
        return database.check_book_exists_in_db(book_hash, self._collection)

    def book_exists_by_asin(self, asin: str) -> bool:
        return database.check_amazon_asin_exists_in_db(asin, self._collection)

    def leanpub_book_exists(self, book_id: Optional[str], book_slug: Optional[str]) -> bool:
        return deduplicate.leanpub_prescrape_deduplicate(book_id, book_slug, self._collection)

    def close(self) -> None:
        database.close_mongo_connection()


class JsonBackend(StorageBackend):
    """
    File-backed backend over :mod:`bookscraper.backends.local.store`.

    Holds an in-memory index of the store's ``hash``/``asin``/``book_id``/``slug`` values,
    built once (lazily) from the file and kept current by :meth:`save_books` for the life
    of the process. Every lookup then hits the index rather than re-reading and re-parsing
    the whole file, so ``search``'s per-candidate ``leanpub_book_exists`` calls cost
    O(candidates) instead of O(candidates x store size).
    """

    _INDEX_FIELDS = (("hashes", "hash"), ("asins", "asin"), ("book_ids", "book_id"), ("slugs", "slug"))

    def __init__(self) -> None:
        self._index: Optional[dict[str, set[str]]] = None

    @classmethod
    def _add_to_index(cls, index: dict[str, set[str]], book: dict) -> None:
        for bucket, field in cls._INDEX_FIELDS:
            value = book.get(field)
            if value:
                index[bucket].add(value)

    def _ensure_index(self) -> dict[str, set[str]]:
        if self._index is None:
            index: dict[str, set[str]] = {bucket: set() for bucket, _ in self._INDEX_FIELDS}
            # Pass the path explicitly (read now, not at def time) so tests that
            # monkeypatch store.LOCAL_STORE_FILE stay isolated from the real store.
            for book in store._load_books(store.LOCAL_STORE_FILE):
                if isinstance(book, dict):
                    self._add_to_index(index, book)
            self._index = index
        return self._index

    def save_books(self, books: list[dict]) -> SaveSummary:
        summary = store.save_books(books, store.LOCAL_STORE_FILE)
        # Mirror in the cache exactly what store.save_books just persisted: books that
        # carry a hash not already known (duplicates and hash-less errors are not written).
        if self._index is not None:
            for book in books:
                if not isinstance(book, dict):
                    continue
                book_hash = book.get("hash")
                if book_hash and book_hash not in self._index["hashes"]:
                    self._add_to_index(self._index, book)
        return summary

    def book_exists_by_hash(self, book_hash: str) -> bool:
        if not book_hash:
            return False
        return book_hash in self._ensure_index()["hashes"]

    def book_exists_by_asin(self, asin: str) -> bool:
        if not asin:
            return False
        return asin in self._ensure_index()["asins"]

    def leanpub_book_exists(self, book_id: Optional[str], book_slug: Optional[str]) -> bool:
        if not book_id and not book_slug:
            return False
        index = self._ensure_index()
        if book_id and book_id in index["book_ids"]:
            return True
        return bool(book_slug) and book_slug in index["slugs"]


def _warn_if_cert_expiring() -> None:
    """
    Emit an advisory pre-flight warning if the active X.509 client certificate is expired or
    close to expiring, before the mongo backend connects.

    Advisory only: it resolves the active cert via ``config.resolve_mongo_connection()`` and
    delegates to the non-raising :func:`cert_expiry_warning`. A resolver failure
    (:class:`~bookscraper.backends.mongo.config.ConfigError`) is swallowed here - the real
    connection attempt that follows surfaces it properly - and a cert-parse failure returns a
    warning string rather than crashing (ROT-16, ROT-17). No PEM/key bytes reach the log or
    console (ROT-3).
    """
    try:
        _uri, cert_file = config.resolve_mongo_connection()
    except config.ConfigError as e:
        # Deferred on purpose: the real connection attempt that follows surfaces it loudly.
        logger.debug(f"cert expiry pre-flight skipped: {type(e).__name__}")
        return
    warning = cert_expiry_warning(pathlib.Path(cert_file) if cert_file else None)
    if warning:
        print_log(warning, "warning")


def resolve_store_backend(backend_name: str) -> StorageBackend:
    """
    Runs a pre-flight check for the requested backend and returns a ready-to-use
    StorageBackend. Exits the process if that backend isn't usable - there is no
    interactive fallback: --store-backend is required, so the choice is always
    explicit (see cli.py).
    """
    print_log(f"\nRunning pre-flight checks for '{backend_name}' storage backend...", "info")

    if backend_name == "mongo":
        _warn_if_cert_expiring()
        print_log("  Checking MongoDB connection...", "info")
        mongo_collection = database.get_mongo_collection()
        if mongo_collection is None:
            print_log("  MongoDB connection: FAILED. Cannot use 'mongo' backend.", "error")
            sys.exit(1)
        print_log("  MongoDB connection: OK", "info")
        return MongoBackend(mongo_collection)

    if backend_name == "json":
        print_log("  Checking local write permissions...", "info")
        if not check_local_write_permission():
            print_log("  Local write permission: FAILED. Cannot use 'json' backend.", "error")
            sys.exit(1)
        print_log("  Local write permission: OK", "info")
        print_log("  Checking local store integrity...", "info")
        try:
            store.assert_store_readable(store.LOCAL_STORE_FILE)
        except store.LocalStoreCorruptError as e:
            print_log(f"  Local store: CORRUPT. Cannot use 'json' backend. {e}", "error")
            sys.exit(1)
        print_log("  Local store: OK", "info")
        return JsonBackend()

    print_log(f"Unknown storage backend: {backend_name!r}", "error")
    sys.exit(1)
