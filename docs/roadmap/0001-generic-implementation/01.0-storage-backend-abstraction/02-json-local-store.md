# 02 - JSON local store

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/README.md)
**Status:** ✅ Complete
**Depends on:** [01-storage-backend-contract.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/01-storage-backend-contract.md)
**Role:** Python Expert

## Context

The `json` backend replaces the disposable CSV dump with a store that *behaves like* the Mongo collection: it
persists across runs and rejects duplicate `hash` values the way Mongo's unique index does.

## Requirements

- `src/bookscraper/backends/local/store.py` with module constant `LOCAL_STORE_FILE = Path("books.json")`.
- `_load_books(path) -> list[dict]`: missing file → `[]`; malformed JSON or a non-list top level → `[]` plus an
  error log (never raises).
- `save_books(books, path)`: appends books whose `hash` is new; skips duplicates and books with no `hash`
  (counted as errors); writes only if at least one book was inserted; logs and prints an
  `N new, N duplicates, N errors` summary.
- `check_book_exists(hash)`, `check_amazon_asin_exists(asin)`, `leanpub_book_exists(book_id, slug)` - falsy inputs
  return `False` without reading the file.
- `JsonBackend` in `storage.py` delegates each contract method to these functions.

## Files

- Create `src/bookscraper/backends/local/store.py`, `src/bookscraper/backends/local/__init__.py`.
- Modify `src/bookscraper/backends/storage.py` - `JsonBackend`.
- `.gitignore` - `books.json` (run artifact, but persistent).

## Tests

`tests/unit/backends/local/test_store.py` (real file I/O under `tmp_path` via `isolated_local_store`):
`test_creates_file_with_content_on_fresh_store`, `test_duplicate_hash_is_skipped`,
`test_new_book_appends_alongside_existing`, `test_book_missing_hash_is_skipped_not_written`,
`test_mixed_batch_writes_only_valid_book`, `test_empty_list_is_noop_no_file_created`,
`test_all_duplicates_does_not_rewrite_missing_file`, `test_match_by_book_id_alone`, `test_match_by_slug_alone`,
`test_malformed_json_returns_empty_list`, `test_valid_json_non_list_returns_empty_list`,
`test_nonexistent_file_returns_empty_list`.

## Success criteria

- [x] A book saved in run N is reported as a duplicate in run N+1.
- [x] No test reads or writes the real repo-root `books.json`.
