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
from typing import Optional

from pymongo.collection import Collection

from .. import models
from ..book_utils import check_local_write_permission, print_log
from ..cli import STORE_BACKENDS
from .local import store
from .mongo import config, database, deduplicate
from .mongo.cert_rotation import cert_expiry_warning
from .save_summary import SaveSummary

logger = logging.getLogger(__name__)


def _reject_invalid(books: list[dict]) -> tuple[list[dict], int]:
    """Split ``books`` into the ones that pass :func:`models.validate_document` and a reject count.

    The single save-boundary gate both backends apply before writing (subtask 05): a document
    failing validation is never persisted, counts as a :class:`SaveSummary` error, and is logged
    exactly once with its ``url`` and the problem list - never the document itself, since scraped
    text is untrusted (03.0 security review).

    :param books: The candidate documents handed to a backend's ``save_books``.
    :returns: A ``(valid, rejected)`` tuple - the documents that passed, and the count that did not.
    """
    valid: list[dict] = []
    rejected = 0
    for book in books:
        problems = models.validate_document(book)
        if not problems:
            valid.append(book)
            continue
        rejected += 1
        url = book.get("url", "<no url>") if isinstance(book, dict) else "<non-dict document>"
        message = f"Rejected invalid document (url={url}): {'; '.join(problems)}"
        logger.error(message)
        print_log(f"Error: {message}", "error")
    return valid, rejected


def _with_rejects(summary: SaveSummary, rejected: int) -> SaveSummary:
    """Fold ``rejected`` save-boundary rejections into ``summary``'s error count.

    Returns ``summary`` unchanged when nothing was rejected, so an all-valid batch keeps the exact
    object its backend produced (the delegation identity the mock-tier tests rely on).

    :param summary: The :class:`SaveSummary` the underlying store returned for the valid subset.
    :param rejected: The number of documents rejected by :func:`_reject_invalid`.
    :returns: A :class:`SaveSummary` whose ``errors`` includes the rejected documents.
    """
    if not rejected:
        return summary
    return SaveSummary(
        inserted=summary.inserted,
        duplicates=summary.duplicates,
        errors=summary.errors + rejected,
    )


class StorageBackend(ABC):
    """Uniform read/write contract shared by the mongo and json backends."""

    @abstractmethod
    def save_books(self, books: list[dict]) -> SaveSummary:
        """Persist ``books``, skipping those whose ``hash`` already exists, and return a :class:`SaveSummary`."""

    @abstractmethod
    def book_exists_by_hash(self, book_hash: str) -> bool:
        """Report whether a book with the given ``hash`` already exists in the backend."""

    @abstractmethod
    def book_exists_by_asin(self, asin: str) -> bool:
        """Report whether a book with the given Amazon ``asin`` already exists in the backend."""

    @abstractmethod
    def leanpub_book_exists(self, book_id: Optional[str], book_slug: Optional[str]) -> bool:
        """Report whether a Leanpub book matching ``book_id`` or ``book_slug`` already exists in the backend."""

    def close(self):
        """Optional cleanup hook; no-op unless a backend holds a live connection."""


class MongoBackend(StorageBackend):
    """Thin :class:`StorageBackend` adapter over the MongoDB functions in :mod:`.mongo.database`."""

    def __init__(self, collection: Collection):
        """
        :param collection: The MongoDB collection this backend reads from and writes to.
        """
        self._collection = collection

    def save_books(self, books: list[dict]) -> SaveSummary:
        """Validate each document at the save boundary, then delegate to :func:`database.save_books_to_mongodb`.

        Documents failing :func:`models.validate_document` are never written; they are counted as
        :class:`SaveSummary` errors and logged once (subtask 05).
        """
        valid, rejected = _reject_invalid(books)
        return _with_rejects(database.save_books_to_mongodb(valid, self._collection), rejected)

    def book_exists_by_hash(self, book_hash: str) -> bool:
        """Delegate to :func:`database.check_book_exists_in_db`."""
        return database.check_book_exists_in_db(book_hash, self._collection)

    def book_exists_by_asin(self, asin: str) -> bool:
        """Delegate to :func:`database.check_amazon_asin_exists_in_db`."""
        return database.check_amazon_asin_exists_in_db(asin, self._collection)

    def leanpub_book_exists(self, book_id: Optional[str], book_slug: Optional[str]) -> bool:
        """Delegate to :func:`deduplicate.leanpub_prescrape_deduplicate`."""
        return deduplicate.leanpub_prescrape_deduplicate(book_id, book_slug, self._collection)

    def close(self):
        """Close the shared MongoDB connection via :func:`database.close_mongo_connection`."""
        database.close_mongo_connection()


class JsonBackend(StorageBackend):
    """
    File-backed backend over :mod:`bookscraper.backends.local.store`.

    Holds an in-memory index of the store's ``hash``/``asin``/``book_id``/``slug`` values,
    built once (lazily) from the file and kept current by :meth:`save_books` for the life
    of the process. Every lookup then hits the index rather than re-reading and re-parsing
    the whole file, so ``search``'s per-candidate ``leanpub_book_exists`` calls cost
    O(candidates) instead of O(candidates x store size).

    The ``book_ids`` bucket is fed from both ``book_id`` (v1 Leanpub documents) and
    ``source_id`` (schema-2 documents, which fold the Leanpub book id into that shared field),
    so a Leanpub book stays deduped by id whether it was written before or after the 03.1
    migration. A schema-2 Amazon ``source_id`` is its ASIN, which cannot collide with a
    Leanpub book id (short numeric strings), so sharing the bucket is safe.
    """

    _INDEX_FIELDS = (
        ("hashes", "hash"),
        ("asins", "asin"),
        ("book_ids", "book_id"),
        ("book_ids", "source_id"),
        ("slugs", "slug"),
    )

    def __init__(self, path: Optional[pathlib.Path] = None):
        """Create the backend with its in-memory index unbuilt (built lazily on first lookup).

        :param path: The store file this backend reads from and writes to. ``None`` (the default)
            defers to :data:`store.LOCAL_STORE_FILE` at every access, so a test that monkeypatches
            that module constant stays isolated; an explicit path (from ``--json-store``) is used
            verbatim.
        """
        self._path = path
        self._index: Optional[dict[str, set[str]]] = None

    def _store_path(self) -> pathlib.Path:
        """Return the store file to use, resolving the ``None`` default lazily.

        :returns: The explicit path given at construction, else the current
            :data:`store.LOCAL_STORE_FILE` (read now, not at construction time).
        """
        return self._path if self._path is not None else store.LOCAL_STORE_FILE

    @classmethod
    def _add_to_index(cls, index: dict[str, set[str]], book: dict):
        """Add ``book``'s non-empty indexed field values into the matching buckets of ``index``."""
        for bucket, field in cls._INDEX_FIELDS:
            value = book.get(field)
            if value:
                index[bucket].add(value)

    def _ensure_index(self) -> dict[str, set[str]]:
        """Return the in-memory index, building it from the store file on first access."""
        if self._index is None:
            index: dict[str, set[str]] = {bucket: set() for bucket, _ in self._INDEX_FIELDS}
            # Pass the path explicitly (read now, not at def time) so tests that
            # monkeypatch store.LOCAL_STORE_FILE stay isolated from the real store.
            for book in store._load_books(self._store_path()):
                if isinstance(book, dict):
                    self._add_to_index(index, book)
            self._index = index
        return self._index

    def save_books(self, books: list[dict]) -> SaveSummary:
        """Validate at the save boundary, persist the valid documents, and mirror them into the index.

        Documents failing :func:`models.validate_document` are never written; they are counted as
        :class:`SaveSummary` errors and logged once (subtask 05). Only the valid documents reach
        :func:`store.save_books` and the in-memory index.
        """
        valid, rejected = _reject_invalid(books)
        summary = store.save_books(valid, self._store_path())
        # Mirror in the cache exactly what store.save_books just persisted: valid books that
        # carry a hash not already known (duplicates are not re-added; invalid ones never reach here).
        if self._index is not None:
            for book in valid:
                book_hash = book.get("hash")
                if book_hash and book_hash not in self._index["hashes"]:
                    self._add_to_index(self._index, book)
        return _with_rejects(summary, rejected)

    def book_exists_by_hash(self, book_hash: str) -> bool:
        """Report whether ``book_hash`` is present in the in-memory index."""
        if not book_hash:
            return False
        return book_hash in self._ensure_index()["hashes"]

    def book_exists_by_asin(self, asin: str) -> bool:
        """Report whether ``asin`` is present in the in-memory index."""
        if not asin:
            return False
        return asin in self._ensure_index()["asins"]

    def leanpub_book_exists(self, book_id: Optional[str], book_slug: Optional[str]) -> bool:
        """Report whether ``book_id`` or ``book_slug`` is present in the in-memory index."""
        # Same guard as the mongo backend and store.leanpub_book_exists: only a plain string is a
        # lookup key (a dict smuggled in from scraped data would otherwise raise on the set lookup).
        book_id = book_id if isinstance(book_id, str) else None
        book_slug = book_slug if isinstance(book_slug, str) else None
        if not book_id and not book_slug:
            return False
        index = self._ensure_index()
        if book_id and book_id in index["book_ids"]:
            return True
        return bool(book_slug) and book_slug in index["slugs"]


def _warn_if_cert_expiring():
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


def resolve_store_backend(backend_name: str, json_store: Optional[pathlib.Path] = None) -> StorageBackend:
    """
    Runs a pre-flight check for the requested backend and returns a ready-to-use
    StorageBackend. Exits the process if that backend isn't usable - there is no
    interactive fallback: --store-backend is required, so the choice is always
    explicit (see cli.py).

    :param backend_name: The ``--store-backend`` value (``"mongo"`` or ``"json"``).
    :param json_store: The ``--json-store`` path for the json backend, or ``None`` to use the
        default ``books.json``. The write-permission pre-flight checks this file's directory, and
        the returned :class:`JsonBackend` reads and writes it. Ignored for the mongo backend.
    :returns: A ready-to-use :class:`StorageBackend`.
    """
    if backend_name not in STORE_BACKENDS:
        print_log(f"Unknown storage backend: {backend_name!r}", "error")
        sys.exit(1)

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
        store_path = json_store if json_store is not None else store.LOCAL_STORE_FILE
        # Check the store file's own directory (not always "."), so --json-store pointing elsewhere
        # is pre-flighted where it will actually be written.
        directory = str(store_path.parent) or "."
        print_log("  Checking local write permissions...", "info")
        if not check_local_write_permission(directory):
            print_log("  Local write permission: FAILED. Cannot use 'json' backend.", "error")
            sys.exit(1)
        print_log("  Local write permission: OK", "info")
        print_log("  Checking local store integrity...", "info")
        try:
            store.assert_store_readable(store_path)
        except store.LocalStoreCorruptError as e:
            print_log(f"  Local store: CORRUPT. Cannot use 'json' backend. {e}", "error")
            sys.exit(1)
        print_log("  Local store: OK", "info")
        return JsonBackend(store_path)
