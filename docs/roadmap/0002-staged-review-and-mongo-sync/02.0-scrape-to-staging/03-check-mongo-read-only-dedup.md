# 03 - Opt-in read-only Mongo dedup (`--check-mongo`)

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/README.md)
**Status:** ✅ Complete
**Depends on:** [02-dedup-source-refactor.md](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/02-dedup-source-refactor.md)
**Role:** Python Expert

## Context

Without Mongo, a scrape run cannot know that a book was pushed months ago and is no longer in the staging file,
so it would scrape it again (and `push` would later skip it as a duplicate). That costs time and politeness budget
but is not a correctness problem, so the check is opt-in and must never be able to write.

## Requirements

- `MongoDedupSource(collection)` wrapping `database.check_book_exists_in_db`, `check_amazon_asin_exists_in_db`, and
  `deduplicate.leanpub_prescrape_deduplicate` - and nothing else from `database.py`.
- With `--check-mongo`, `resolve_dedup_source` connects via `config.resolve_mongo_connection()` exactly as today;
  a failed connection exits 1 (explicit flag, explicit failure - no silent downgrade to staging-only).
- Pass the collection through `collection.with_options(read_preference=ReadPreference.SECONDARY_PREFERRED)` and
  never call `ensure_unique_index_on_hash` from this path (index management belongs to `push`).
- Fold in 0001 01.0/06 (secondary lookup indexes) only if it has not landed; otherwise just reference it.

## Files

- Modify `src/bookscraper/backends/storage.py` (or `backends/dedup.py`), possibly
  `src/bookscraper/backends/mongo/database.py` (split connection setup from index creation).
- Extend `tests/mock/backends/test_dedup.py`.

## Tests

`test_mongo_source_lookups_delegate`, `test_check_mongo_connection_failure_exits`,
`test_mongo_source_never_writes` (a fake collection whose `insert_*` / `update_*` / `delete_*` / `create_index`
raise `AssertionError`; run a full mocked `scrape-urls` with `--check-mongo`).

## Success criteria

- [x] With `--check-mongo`, a full mocked run makes zero write calls to the collection.
- [x] Without it, zero calls of any kind.
