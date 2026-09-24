# 07 - JSON store atomic write & index cache

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/README.md)
**Status:** ⬜ Not started
**Depends on:** [02-json-local-store.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/02-json-local-store.md)
**Role:** Python Expert

## Context

Two problems in `backends/local/store.py`:

1. **Non-atomic write.** `_write_books()` is `path.write_text(...)`. A crash or `Ctrl+C` mid-write leaves a
   truncated `books.json`, and the next `_load_books()` then treats it as malformed and returns `[]`. The next save
   then **overwrites the whole accumulated store** with only the new books. That's silent data loss in the one file
   CLAUDE.md says never to lose.
2. **O(file) per lookup.** Every `check_book_exists` / `check_amazon_asin_exists` / `leanpub_book_exists` call
   re-reads and re-parses the entire file. `search` calls `leanpub_book_exists` once per candidate, so the cost is
   O(candidates × store size).

## Requirements

- **Atomic write:** `_write_books()` writes to a temp file in the same directory (`.books.json.<random>`),
  `fsync`s it, then `os.replace`s it over the target. Same pattern as `cert_rotation.atomic_write` (no `0600`
  needed: the store holds public metadata). Extract a shared `atomic_write_bytes(path, data, mode=None)` into
  `book_utils.py` only if both call sites can use it unchanged. Otherwise keep a local helper. Don't make
  `backends/local/` import from `backends/mongo/`.
- **Refuse to clobber a corrupt store:** if `_load_books()` hits `JSONDecodeError` on an existing file,
  `save_books()` must raise a new `LocalStoreCorruptError` (subclass of `Exception`) instead of writing. The
  message names the file and suggests restoring it from a backup. The lookup functions keep returning `False`
  (unchanged).
- **Backup on write:** before replacing, copy the current file to `books.json.bak` (one generation).
- **In-memory index:** `JsonBackend` builds, on first use, one cached snapshot:
  `{"hashes": set, "asins": set, "book_ids": set, "slugs": set}`. It is invalidated/updated by `save_books()` in
  the same process. The module-level functions keep their current `path`-parameter signatures (tests use them);
  the cache lives on the `JsonBackend` instance.
- `resolve_store_backend("json")` surfaces `LocalStoreCorruptError` at pre-flight: `print_log` error +
  `sys.exit(1)`, before any scraping starts.

## Files

- Modify `src/bookscraper/backends/local/store.py` - atomic write, backup, `LocalStoreCorruptError`.
- Modify `src/bookscraper/backends/storage.py` - `JsonBackend` cache, pre-flight corrupt-store check.
- Modify `tests/unit/backends/local/test_store.py`, `tests/mock/backends/test_storage.py`.
- Modify `.gitignore` - `books.json.bak`, `.books.json.*`.

## Tests

- `test_write_is_atomic_and_leaves_no_temp_files`
- `test_interrupted_write_preserves_previous_store` (monkeypatch `os.replace` to raise; original content intact)
- `test_save_refuses_to_overwrite_corrupt_store`
- `test_backup_file_holds_previous_generation`
- `test_json_backend_reads_file_once_for_many_lookups` (spy on `_load_books`)
- `test_json_backend_cache_sees_books_saved_in_same_process`
- `test_preflight_exits_on_corrupt_store`

## Success criteria

- [ ] Killing the process at any point during `save_books()` never reduces the number of books in `books.json`.
- [ ] 1,000 lookups against a 5,000-book store parse the file once.
