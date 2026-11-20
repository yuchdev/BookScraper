# 02 - Read-only `DedupSource` replaces `StorageBackend`

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/README.md)
**Status:** ✅ Complete
**Depends on:** [01-cli-staging-file-flag.md](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/01-cli-staging-file-flag.md), [01.0/03 staging file I/O](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/03-staging-file-io.md)
**Role:** Python Expert

## Context

`backends/storage.py`'s `StorageBackend` couples dedup reads with `save_books()`. `scrape_details.scrape_book()`
and `commands/search.py` only need the reads; the write now happens once, at the end of the run, to the staging file.

## Requirements

- In `backends/storage.py` (or a renamed `backends/dedup.py`), replace `StorageBackend` with
  `DedupSource` (`book_exists_by_hash`, `book_exists_by_asin`, `leanpub_book_exists`, `close`) - **no write method**.
- `StagingDedupSource(index: StagingIndex)` - backed by 01.0/03's in-memory index.
- `CompositeDedupSource(*sources)` - "exists" if any source says so (used by subtask 03).
- `resolve_dedup_source(staging_file: Path, check_mongo: bool) -> DedupSource` replaces `resolve_store_backend()`:
  loads the staging file with `missing_ok=True` (a `StagingFileError` → critical log + `print_log` error + exit 1),
  pre-flights its directory's write permission, and adds the Mongo source only when `check_mongo` is true.
- `scrape_book()` and `commands/search.py` take a `DedupSource`; `commands/*.run()` collect results and hand them to
  01.0/03 / subtask 04 for the single write at the end.
- Remove `JsonBackend`, `MongoBackend`, and `resolve_store_backend` and update `backends/__init__.py`'s re-exports.
  `backends/local/store.py` disappears if 01.0/03 chose "new module" rather than "rename".
- `detect_schema.py` mentions `StorageBackend` (per grep) - update or drop that reference.

## Files

- Modify `src/bookscraper/backends/storage.py`, `src/bookscraper/backends/__init__.py`,
  `src/bookscraper/scraping/scrape_details.py`, `src/bookscraper/commands/scrape_urls.py`,
  `src/bookscraper/commands/search.py`, `src/bookscraper/commands/detect_schema.py`.
- Rewrite `tests/mock/backends/test_storage.py` → `tests/mock/backends/test_dedup.py`; update
  `tests/mock/commands/test_scrape_urls.py`, `tests/mock/commands/test_search.py`.

## Tests

`test_staging_source_detects_hash_asin_leanpub`, `test_composite_true_if_any_source_true`,
`test_resolve_without_check_mongo_never_touches_database` (patch `database.get_mongo_collection` to fail the test if
called), `test_resolve_corrupt_staging_file_exits`, `test_resolve_unwritable_directory_exits`,
`test_dedup_source_has_no_write_method`.

## Success criteria

- [x] `grep -rn "StorageBackend\|resolve_store_backend\|save_books" src/bookscraper/{scraping,commands}` is empty.
- [x] The import-direction rule from CLAUDE.md still holds.
