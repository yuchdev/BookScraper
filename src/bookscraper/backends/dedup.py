"""
Read-only duplicate checks for step 1 (scrape-urls / search) - ADR 0003.

Replaces the old StorageBackend (mongo|json, read + write). Under the staged pipeline no
scraping code writes anywhere but the staging file, and that single write happens once at the
end of a run (staging.append_to_staging). So the interface handed to the scrapers has lookups
only - there is no save method to call by mistake.

- StagingDedupSource: the staging file's in-memory StagingIndex (always on).
- MongoDedupSource: MongoDB lookups, only with --check-mongo; connects without creating indexes
  and reads from secondaries when available.
- CompositeDedupSource: "exists" if any source says so.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from pymongo import ReadPreference
from pymongo.collection import Collection

from ..book_utils import check_local_write_permission, logger, print_log
from .local.staging import StagingFileError, StagingIndex, load_staging
from .mongo import database, deduplicate


class DedupSource(ABC):
    """Read-only duplicate lookups used while scraping. Deliberately has no write method."""

    @abstractmethod
    def book_exists_by_hash(self, book_hash: str) -> bool: ...

    @abstractmethod
    def book_exists_by_asin(self, asin: str) -> bool: ...

    @abstractmethod
    def leanpub_book_exists(self, book_id: Optional[str], book_slug: Optional[str]) -> bool: ...

    def close(self) -> None:  # noqa: B027 - intentional no-op default
        """Optional cleanup hook; no-op unless a source holds a live connection."""


class StagingDedupSource(DedupSource):
    def __init__(self, index: StagingIndex) -> None:
        self._index = index

    def book_exists_by_hash(self, book_hash: str) -> bool:
        return self._index.has_hash(book_hash)

    def book_exists_by_asin(self, asin: str) -> bool:
        return self._index.has_asin(asin)

    def leanpub_book_exists(self, book_id: Optional[str], book_slug: Optional[str]) -> bool:
        return self._index.has_leanpub(book_id, book_slug)


class MongoDedupSource(DedupSource):
    """MongoDB lookups for --check-mongo. Uses find_one only; never inserts, updates, or indexes."""

    def __init__(self, collection: Collection) -> None:
        self._collection = collection.with_options(read_preference=ReadPreference.SECONDARY_PREFERRED)

    def book_exists_by_hash(self, book_hash: str) -> bool:
        return bool(book_hash) and database.check_book_exists_in_db(book_hash, self._collection)

    def book_exists_by_asin(self, asin: str) -> bool:
        return bool(asin) and database.check_amazon_asin_exists_in_db(asin, self._collection)

    def leanpub_book_exists(self, book_id: Optional[str], book_slug: Optional[str]) -> bool:
        return deduplicate.leanpub_prescrape_deduplicate(book_id, book_slug, self._collection)

    def close(self) -> None:
        database.close_mongo_connection()


class CompositeDedupSource(DedupSource):
    def __init__(self, *sources: DedupSource) -> None:
        self._sources = sources

    def book_exists_by_hash(self, book_hash: str) -> bool:
        return any(s.book_exists_by_hash(book_hash) for s in self._sources)

    def book_exists_by_asin(self, asin: str) -> bool:
        return any(s.book_exists_by_asin(asin) for s in self._sources)

    def leanpub_book_exists(self, book_id: Optional[str], book_slug: Optional[str]) -> bool:
        return any(s.leanpub_book_exists(book_id, book_slug) for s in self._sources)

    def close(self) -> None:
        for source in self._sources:
            source.close()


def resolve_dedup_source(staging_file: Path, check_mongo: bool) -> DedupSource:
    """
    Pre-flights step 1 and returns its dedup source. Exits the process (code 1) when the staging
    file is corrupt, its directory is not writable, or --check-mongo was given and MongoDB is
    unreachable - an explicit flag fails explicitly, never silently downgrading to staging-only.
    """
    staging_file = Path(staging_file)
    print_log(f"\nStaging file: {staging_file}", "info")
    print_log(f"Duplicate check against MongoDB (--check-mongo): {'ON (read-only)' if check_mongo else 'off'}", "info")

    try:
        documents = load_staging(staging_file, missing_ok=True)
    except StagingFileError as e:
        logger.critical(f"Cannot use staging file: {e}")
        print_log(f"Critical Error: {e}", "error")
        sys.exit(1)

    directory = staging_file.parent
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print_log(f"Critical Error: cannot create staging directory '{directory}': {e}", "error")
        sys.exit(1)
    if not check_local_write_permission(str(directory)):
        print_log(f"Critical Error: staging directory '{directory}' is not writable.", "error")
        sys.exit(1)

    staging_source = StagingDedupSource(StagingIndex(documents))
    print_log(f"  {len(documents)} document(s) already in the staging file.", "info")
    if not check_mongo:
        return staging_source

    print_log("  Connecting to MongoDB for read-only duplicate checks...", "info")
    collection = database.get_mongo_collection(ensure_indexes=False)
    if collection is None:
        print_log("  MongoDB connection: FAILED. Re-run without --check-mongo to scrape offline.", "error")
        sys.exit(1)
    return CompositeDedupSource(staging_source, MongoDedupSource(collection))
