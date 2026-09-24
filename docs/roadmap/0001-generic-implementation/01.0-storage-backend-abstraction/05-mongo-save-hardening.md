# 05 - Mongo save hardening

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/README.md)
**Status:** ⬜ Not started
**Depends on:** [03-mongo-backend-wrapper.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/03-mongo-backend-wrapper.md)
**Role:** Python Expert

## Context

`backends/mongo/database.py::save_books_to_mongodb()` has three defects:

1. When `books_collection is None` it logs an error but **does not return**. Each `insert_one` then raises
   `AttributeError` on `None`, which the catch-all `except Exception` swallows, so the run reports *N errors*
   instead of *collection unavailable*.
2. Its docstring promises "Returns: A dictionary containing insertion summary", but it returns `None`. The two
   callers can't tell a 100%-duplicate batch from a total failure.
3. One `insert_one` round-trip per book. For a 500-book `search` run against Atlas that's 500 sequential
   network round-trips.

The JSON store (`local/store.py::save_books`) has the same missing return value.

## Requirements

- Introduce a frozen dataclass `SaveSummary` in `src/bookscraper/backends/storage.py`:

  | Field          | Type  | Meaning                                              |
  |----------------|-------|------------------------------------------------------|
  | `inserted`     | `int` | Documents newly written                               |
  | `duplicates`   | `int` | Documents skipped because their `hash` already exists |
  | `errors`       | `int` | Documents rejected for any other reason               |

  Property `total -> int` = sum of the three.
- `StorageBackend.save_books()` return type changes from `None` to `SaveSummary`. Update both implementations and
  the abstract signature.
- `save_books_to_mongodb()`:
  - Return `SaveSummary(0, 0, len(books))` **immediately** when the collection is `None` (after the existing log).
  - Empty `books` → `SaveSummary(0, 0, 0)` with no DB call.
  - Replace the per-book loop with `insert_many(docs, ordered=False)`. On `BulkWriteError`, walk
    `e.details["writeErrors"]`: code `11000` → duplicate, anything else → error; inserted =
    `e.details["nInserted"]`.
  - Keep `InvalidDocument` handling: pre-validate with `bson.BSON.encode` per document *before* `insert_many` so one
    bad document is counted as an error rather than aborting the batch.
  - Pass **copies** of the documents to pymongo (`[dict(b) for b in books]`), so the caller's dicts don't pick up an
    `_id` of type `ObjectId`. Today `insert_one` adds `_id` to the caller's dict in place, which breaks
    `json.dumps` in any later diagnostic write.
- `local/store.save_books()` returns `SaveSummary` built from its existing counters.
- `commands/scrape_urls.py` and `commands/search.py` log the returned summary and use `summary.errors > 0` as a
  partial-failure signal (consumed by the exit-code contract in task 05.0, subtask 05).

## Files

- Modify `src/bookscraper/backends/storage.py` - `SaveSummary`, abstract signature, both backends.
- Modify `src/bookscraper/backends/mongo/database.py` - `save_books_to_mongodb`.
- Modify `src/bookscraper/backends/local/store.py` - `save_books` return value.
- Modify `src/bookscraper/commands/scrape_urls.py`, `src/bookscraper/commands/search.py` - consume the summary.
- Modify `tests/mock/backends/mongo/test_database.py`, `tests/unit/backends/local/test_store.py`,
  `tests/mock/backends/test_storage.py`.

## Tests

- `test_save_returns_all_errors_and_skips_io_when_collection_is_none`
- `test_save_empty_list_returns_zero_summary_without_db_call`
- `test_save_uses_single_unordered_insert_many`
- `test_bulk_write_error_splits_duplicates_from_other_errors` (details with two `11000` + one `121` code)
- `test_invalid_document_counted_as_error_without_aborting_batch`
- `test_caller_dicts_are_not_mutated_with_object_id`
- `test_json_save_books_returns_summary` (unit, `tmp_path`)
- `test_mongo_and_json_backends_return_save_summary` (mock, `test_storage.py`)

## Success criteria

- [ ] `save_books_to_mongodb(books, None)` performs zero pymongo calls.
- [ ] A 100-book batch performs exactly one `insert_many` call.
- [ ] `database.py` and `store.py` stay at 100% line coverage.
- [ ] CLAUDE.md *Storage backend* section mentions the `SaveSummary` return contract.

## Out of scope

- Upserts / updating existing books (see `update_book_isbn`; untouched here).
