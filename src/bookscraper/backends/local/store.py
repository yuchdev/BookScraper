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
from pathlib import Path
from typing import Optional

from ...book_utils import print_log

module_logger = logging.getLogger(__name__)

LOCAL_STORE_FILE = Path("books.json")


def _load_books(path: Path = LOCAL_STORE_FILE) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError) as e:
        module_logger.error(f"Failed to read local store {path}: {e}")
        print_log(f"Error: Failed to read local store {path}: {e}", "error")
        return []


def _write_books(books: list[dict], path: Path = LOCAL_STORE_FILE) -> None:
    path.write_text(json.dumps(books, indent=2, default=str), encoding="utf-8")


def save_books(books: list[dict], path: Path = LOCAL_STORE_FILE) -> None:
    """
    Appends new books to the local JSON store, skipping any whose 'hash' already
    exists - mirroring MongoDB's unique index on 'hash' (database.save_books_to_mongodb's
    DuplicateKeyError skip).
    """
    existing_books = _load_books(path)
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


def check_book_exists(book_hash: str, path: Path = LOCAL_STORE_FILE) -> bool:
    if not book_hash:
        return False
    return any(b.get("hash") == book_hash for b in _load_books(path) if isinstance(b, dict))


def check_amazon_asin_exists(asin: str, path: Path = LOCAL_STORE_FILE) -> bool:
    if not asin:
        return False
    return any(b.get("asin") == asin for b in _load_books(path) if isinstance(b, dict))


def leanpub_book_exists(book_id: Optional[str], book_slug: Optional[str], path: Path = LOCAL_STORE_FILE) -> bool:
    if not book_id and not book_slug:
        module_logger.warning("No book_id or book_slug provided for Leanpub book existence check.")
        return False
    for book in _load_books(path):
        if not isinstance(book, dict):
            continue
        if book_id and book.get("book_id") == book_id:
            return True
        if book_slug and book.get("slug") == book_slug:
            return True
    return False
