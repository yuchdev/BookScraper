"""
JSON-backed local storage, the `json` counterpart to database.py's MongoDB storage.

Unlike the old CSV output (a disposable, per-run, overwritten dump), this store is
persistent and accumulates across runs - one JSON array of book documents at
LOCAL_STORE_FILE, written with the exact same fields a MongoDB insert would use. This
is what makes `--store-backend json` and `--store-backend mongo` produce the same
duplicate-check behavior: a book saved in one run is still there, and still detected
as a duplicate, in the next.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Optional

from ...book_utils import atomic_write_bytes, print_log
from ..save_summary import SaveSummary

module_logger = logging.getLogger(__name__)

LOCAL_STORE_FILE = Path("books.json")


class LocalStoreCorruptError(Exception):
    """
    The local store file exists but is not valid JSON. Raised by :func:`save_books`
    (via :func:`assert_store_readable`) so a corrupt store fails loudly at pre-flight
    instead of being silently overwritten - which would discard every book it holds,
    the one file CLAUDE.md says never to lose.
    """


def _load_books(path: Path = LOCAL_STORE_FILE, *, raise_on_corrupt: bool = False) -> list[dict]:
    """
    Load the store as a list of book documents.

    :param path: Store file location.
    :param raise_on_corrupt: When ``True``, an existing-but-unparseable file raises
        :class:`LocalStoreCorruptError` instead of being treated as empty. Writers set
        this so they never clobber a store they could not read; read-only lookups leave
        it ``False`` and degrade to an empty result.
    :returns: The parsed list, or ``[]`` when the file is missing, not a JSON list, or
        (with ``raise_on_corrupt`` false) unreadable.
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        if raise_on_corrupt:
            raise LocalStoreCorruptError(
                f"Local store {path} is not valid JSON ({e}); refusing to overwrite it and "
                f"lose its contents. Restore it from {path}.bak or another backup (or move it "
                "aside to start fresh), then re-run."
            ) from e
        module_logger.error(f"Failed to read local store {path}: {e}")
        print_log(f"Error: Failed to read local store {path}: {e}", "error")
        return []
    except OSError as e:
        module_logger.error(f"Failed to read local store {path}: {e}")
        print_log(f"Error: Failed to read local store {path}: {e}", "error")
        return []
    return data if isinstance(data, list) else []


def assert_store_readable(path: Path = LOCAL_STORE_FILE):
    """
    Raise :class:`LocalStoreCorruptError` if an existing store file cannot be parsed.

    Called by ``resolve_store_backend('json')`` at pre-flight so a corrupt store aborts
    the run before any scraping starts, rather than at the first save.

    :param path: Store file location.
    """
    _load_books(path, raise_on_corrupt=True)


def _write_books(books: list[dict], path: Path = LOCAL_STORE_FILE):
    """
    Persist ``books`` atomically, keeping one backup generation.

    Before replacing the live file it is copied to ``<name>.bak`` (one generation), then
    the new content is written to a temp file, ``fsync``ed, and ``os.replace``d over the
    target - so an interrupt mid-write leaves the previous store fully intact rather than
    a truncated file the next load would discard.
    """
    payload = json.dumps(books, indent=2, default=str).encode("utf-8")
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.bak"))
    atomic_write_bytes(path, payload)


def save_books(books: list[dict], path: Path = LOCAL_STORE_FILE) -> SaveSummary:
    """
    Appends new books to the local JSON store, skipping any whose 'hash' already
    exists - mirroring MongoDB's unique index on 'hash' (database.save_books_to_mongodb's
    duplicate skip).

    :param books: Book documents to persist.
    :param path: Store file location (overridable for tests).
    :returns: A :class:`~bookscraper.backends.storage.SaveSummary` matching the shape
        the mongo backend returns, so both backends are interchangeable to the caller.
    :raises LocalStoreCorruptError: If ``path`` exists but is not valid JSON; the store
        is left untouched rather than overwritten (and its books lost).
    """
    existing_books = _load_books(path, raise_on_corrupt=True)
    existing_hashes = {b.get("hash") for b in existing_books if isinstance(b, dict)}

    num_inserted = 0
    num_duplicates = 0
    num_errors = 0

    for book in books:
        book_hash = book.get("hash") if isinstance(book, dict) else None
        if not book_hash:
            module_logger.error(f"Book missing 'hash', cannot store: {book}")
            num_errors += 1
            continue
        if book_hash in existing_hashes:
            module_logger.info(f"Duplicate book found by hash '{book_hash}'. Skipping insertion.")
            num_duplicates += 1
            continue
        existing_books.append(book)
        existing_hashes.add(book_hash)
        num_inserted += 1
        module_logger.info(f"Inserted book: {book.get('title', 'Unknown Title')}")

    if num_inserted:
        _write_books(existing_books, path)

    module_logger.info(
        f"Local JSON store insertion summary: {num_inserted} new, "
        f"{num_duplicates} duplicate{'s' if num_duplicates != 1 else ''}, "
        f"{num_errors} error{'s' if num_errors != 1 else ''}."
    )
    print_log(
        f"Local JSON store insertion summary: {num_inserted} new, {num_duplicates} duplicates, {num_errors} errors.",
        "success",
    )
    return SaveSummary(inserted=num_inserted, duplicates=num_duplicates, errors=num_errors)


# The lookups below re-read and re-parse the whole file on every call. Production code goes
# through JsonBackend's in-memory index instead (backends/storage.py); don't call these from
# a hot path such as a per-candidate dedup loop.


def check_book_exists(book_hash: str, path: Path = LOCAL_STORE_FILE) -> bool:
    """
    Report whether a book with the given ``hash`` already exists in the store.

    :param book_hash: The SHA-256 book hash to look up; a falsy value returns ``False``.
    :param path: Store file location.
    :returns: ``True`` if any stored document carries a matching ``hash``.
    """
    if not book_hash:
        return False
    return any(b.get("hash") == book_hash for b in _load_books(path) if isinstance(b, dict))


def check_amazon_asin_exists(asin: str, path: Path = LOCAL_STORE_FILE) -> bool:
    """
    Report whether a book with the given Amazon ``asin`` already exists in the store.

    :param asin: The Amazon ASIN to look up; a falsy value returns ``False``.
    :param path: Store file location.
    :returns: ``True`` if any stored document carries a matching ``asin``.
    """
    if not asin:
        return False
    return any(b.get("asin") == asin for b in _load_books(path) if isinstance(b, dict))


def leanpub_book_exists(book_id: Optional[str], book_slug: Optional[str], path: Path = LOCAL_STORE_FILE) -> bool:
    """
    Report whether a Leanpub book already exists in the store, matched by id or slug.

    :param book_id: The Leanpub book id to look up.
    :param book_slug: The Leanpub book slug to look up.
    :param path: Store file location.
    :returns: ``True`` if a stored document matches ``book_id`` or ``book_slug``; ``False``
        when neither identifier is provided.
    """
    if not book_id and not book_slug:
        module_logger.warning("No book_id or book_slug provided for Leanpub book existence check.")
        return False
    for book in _load_books(path):
        if not isinstance(book, dict):
            continue
        # Match both schema shapes by book id: v1 documents store it as ``book_id``, schema-2
        # (03.1) documents fold it into the shared ``source_id`` field.
        if book_id and book_id in (book.get("book_id"), book.get("source_id")):
            return True
        if book_slug and book.get("slug") == book_slug:
            return True
    return False
