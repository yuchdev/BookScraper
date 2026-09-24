# 06 - Secondary lookup indexes

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/README.md)
**Status:** ⬜ Not started
**Role:** Python Expert

## Context

Pre-scrape deduplication runs one `find_one` per candidate: `{"asin": ...}` for Amazon
(`check_amazon_asin_exists_in_db`) and `{"$or": [{"book_id": ...}, {"slug": ...}]}` for Leanpub
(`deduplicate.leanpub_prescrape_deduplicate`). Only `hash` is indexed (`ensure_unique_index_on_hash`), so every
lookup is a full collection scan. A `search` run checks hundreds of candidates, so that's hundreds of scans.

## Requirements

- Generalize `ensure_unique_index_on_hash()` into `ensure_indexes(books_collection: Collection) -> None` in
  `backends/mongo/database.py`. It creates (idempotently, checking `index_information()` first as today):

  | Index name              | Keys            | Options                                      |
  |-------------------------|-----------------|----------------------------------------------|
  | `hash_unique_index`     | `hash` asc      | `unique=True` (unchanged name - do not rebuild) |
  | `asin_lookup_index`     | `asin` asc      | `sparse=True` (only Amazon docs carry `asin`) |
  | `book_id_lookup_index`  | `book_id` asc   | `sparse=True`                                |
  | `slug_lookup_index`     | `slug` asc      | `sparse=True`                                |

- Keep `ensure_unique_index_on_hash` as a thin alias that calls `ensure_indexes`, so existing imports and tests
  keep working. Mark it deprecated in its docstring.
- A failure creating one secondary index logs a warning and continues. A failure creating the unique `hash` index
  keeps today's behavior.
- No index is **unique** except `hash`: two editions can legitimately share a slug across sites.

## Files

- Modify `src/bookscraper/backends/mongo/database.py`.
- Modify `tests/mock/backends/mongo/test_database.py`.

## Tests

- `test_ensure_indexes_creates_all_four_when_absent`
- `test_ensure_indexes_is_idempotent_when_all_present`
- `test_secondary_index_failure_warns_and_continues`
- `test_secondary_indexes_are_sparse_and_non_unique`
- existing `test_creates_index_when_absent` / `test_skips_create_when_present` still pass via the alias.

## Success criteria

- [ ] On a live cluster, `db.books.getIndexes()` lists all four names after one run (manual check, noted in the PR).
- [ ] `explain()` of an `asin` lookup shows `IXSCAN`, not `COLLSCAN` (manual, noted in the PR).
