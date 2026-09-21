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

import sys
from abc import ABC, abstractmethod
from typing import Optional

from pymongo.collection import Collection

from ..book_utils import check_local_write_permission, print_log
from .local import store
from .mongo import database, deduplicate


class StorageBackend(ABC):
    """Uniform read/write contract shared by the mongo and json backends."""

    @abstractmethod
    def save_books(self, books: list[dict]) -> None: ...

    @abstractmethod
    def book_exists_by_hash(self, book_hash: str) -> bool: ...

    @abstractmethod
    def book_exists_by_asin(self, asin: str) -> bool: ...

    @abstractmethod
    def leanpub_book_exists(self, book_id: Optional[str], book_slug: Optional[str]) -> bool: ...

    def close(self) -> None:  # noqa: B027 - intentional no-op default, not every backend holds a connection
        """Optional cleanup hook; no-op unless a backend holds a live connection."""


class MongoBackend(StorageBackend):
    def __init__(self, collection: Collection):
        self._collection = collection

    def save_books(self, books: list[dict]) -> None:
        database.save_books_to_mongodb(books, self._collection)

    def book_exists_by_hash(self, book_hash: str) -> bool:
        return database.check_book_exists_in_db(book_hash, self._collection)

    def book_exists_by_asin(self, asin: str) -> bool:
        return database.check_amazon_asin_exists_in_db(asin, self._collection)

    def leanpub_book_exists(self, book_id: Optional[str], book_slug: Optional[str]) -> bool:
        return deduplicate.leanpub_prescrape_deduplicate(book_id, book_slug, self._collection)

    def close(self) -> None:
        database.close_mongo_connection()


class JsonBackend(StorageBackend):
    def save_books(self, books: list[dict]) -> None:
        store.save_books(books)

    def book_exists_by_hash(self, book_hash: str) -> bool:
        return store.check_book_exists(book_hash)

    def book_exists_by_asin(self, asin: str) -> bool:
        return store.check_amazon_asin_exists(asin)

    def leanpub_book_exists(self, book_id: Optional[str], book_slug: Optional[str]) -> bool:
        return store.leanpub_book_exists(book_id, book_slug)


def resolve_store_backend(backend_name: str) -> StorageBackend:
    """
    Runs a pre-flight check for the requested backend and returns a ready-to-use
    StorageBackend. Exits the process if that backend isn't usable - there is no
    interactive fallback: --store-backend is required, so the choice is always
    explicit (see cli.py).
    """
    print_log(f"\nRunning pre-flight checks for '{backend_name}' storage backend...", "info")

    if backend_name == "mongo":
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
        return JsonBackend()

    print_log(f"Unknown storage backend: {backend_name!r}", "error")
    sys.exit(1)
