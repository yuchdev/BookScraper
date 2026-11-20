# 04 - Merge into an existing staging file

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/README.md)
**Status:** ✅ Complete
**Depends on:** [02-dedup-source-refactor.md](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/02-dedup-source-refactor.md)
**Role:** Python Expert

## Context

A reviewer may run `scrape-urls` again while a staging file is half-reviewed. The new run must add its results
without touching anything the reviewer already changed, approved, or rejected.

## Requirements

- `append_to_staging(path, new_documents) -> AppendSummary(appended, duplicates)`:
  - re-load the file **at write time** (not the snapshot from run start) to pick up edits made while the scrape ran;
  - skip any new document whose freshly computed `hash` already exists in the file (counts as duplicate);
  - append the rest in scrape order, each stamped with `_staging = {"status": "pending", "scraped_at": <UTC ISO>,
    "source": "scrape-urls" | "search"}`;
  - write atomically via `write_staging`; existing documents remain byte-for-byte identical in content and order.
- If the file changed on disk between the start-of-run load and the write and is now invalid JSON, abort the write
  with a `StagingFileError` and dump the scraped results to `books.unsaved-<timestamp>.json` so nothing is lost.
- Remove the old `store.save_books()` call sites.

## Files

- Modify `src/bookscraper/backends/local/staging.py`, `src/bookscraper/commands/scrape_urls.py`,
  `src/bookscraper/commands/search.py`.
- Extend `tests/unit/backends/local/test_staging.py`.

## Tests

`test_append_preserves_existing_documents_exactly`, `test_append_skips_hash_already_in_file`,
`test_append_stamps_staging_meta`, `test_append_picks_up_concurrent_edit`,
`test_append_to_corrupted_file_writes_unsaved_dump`.

## Success criteria

- [x] A reviewer's edits and `_staging.status` values survive any number of subsequent scrape runs.
