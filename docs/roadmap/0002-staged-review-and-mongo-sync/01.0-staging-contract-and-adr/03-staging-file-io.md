# 03 - Staging file I/O

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/README.md)
**Status:** ✅ Complete
**Depends on:** [02-staging-document-contract.md](/docs/roadmap/0002-staged-review-and-mongo-sync/01.0-staging-contract-and-adr/02-staging-document-contract.md)
**Role:** Python Expert

## Context

`backends/local/store.py` already reads and writes `books.json`, but it silently returns `[]` on a corrupt file
(which a subsequent save would then overwrite - destroying a reviewer's work), writes non-atomically, and re-parses
the whole file on every lookup. The staging file is now hand-edited, so these gaps become data-loss bugs.

## Requirements

- New module `src/bookscraper/backends/local/staging.py` (or a rename of `store.py` - decide in the PR and keep one
  module, not two):
  - `class StagingFileError(Exception)` - message names the file and, for parse errors, the line/column.
  - `load_staging(path: Path, *, missing_ok: bool) -> list[dict]` - `missing_ok=True` returns `[]` for a missing
    file (scrape commands); `False` raises (push). Corrupt JSON, non-array top level, or a non-object entry
    **raise** - never return `[]`. Accepts a UTF-8 BOM (`utf-8-sig`, as `load_input_urls()` does).
  - `write_staging(path: Path, documents: list[dict]) -> None` - `dumps_documents()` to a sibling temp file,
    `fsync`, `os.replace`. Preserves the existing file's permission bits if it exists.
  - `StagingIndex` - built once from loaded documents; O(1) `has_hash`, `has_asin`, `has_leanpub(book_id, slug)`.
    Replaces `store.py`'s per-lookup file re-read.
- Pure JSON helpers only: no `pymongo`, no network.

## Files

- Create (or rename to) `src/bookscraper/backends/local/staging.py`; modify `src/bookscraper/backends/local/__init__.py`.
- Create `tests/unit/backends/local/test_staging.py`.

## Tests

`test_missing_file_ok_returns_empty`, `test_missing_file_not_ok_raises`, `test_corrupt_json_raises_with_position`,
`test_top_level_object_raises`, `test_non_object_entry_raises`, `test_bom_accepted`,
`test_write_is_atomic_on_failure` (monkeypatch `os.replace` to raise → original file unchanged),
`test_write_preserves_permissions`, `test_index_lookups` - all using `tmp_path`.

## Success criteria

- [x] No code path turns a corrupt staging file into an empty list.
- [x] A failed write leaves the previous file byte-identical.
