# 02 - Mongo bulk writer

**Parent task:** [README.md](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/README.md)
**Status:** ✅ Complete
**Depends on:** [01-push-planner.md](/docs/roadmap/0002-staged-review-and-mongo-sync/03.0-push-to-mongo/01-push-planner.md)
**Role:** Security Auditor → Python Expert

## Context

`database.save_books_to_mongodb()` does one `insert_one` per book, keeps going after a `None` collection, and returns
nothing (0001 01.0/05). The push writer replaces it with a bulk, outcome-reporting implementation.

## Requirements

- `src/bookscraper/backends/mongo/push_writer.py`:
  - `fetch_existing(collection, prepared_docs) -> (existing_hashes, existing_by_id)` - two batched queries
    (`{"hash": {"$in": [...]}}`, projection `{"hash": 1}`; `{"_id": {"$in": [ObjectId(...)]}}`, full documents),
    chunked to ≤1000 values per `$in`.
  - `apply_push_plan(collection, plan, prepared_docs) -> list[ItemOutcome]`: one `bulk_write(ordered=False)` of
    `InsertOne` for every `insert` and `ReplaceOne({"_id": oid}, doc)` for every `update` (replace, not `$set`, so a
    reviewer's deleted field is really removed). Map `BulkWriteError.details["writeErrors"]` back to item indices;
    a `DuplicateKeyError` from a concurrent writer becomes `duplicate`, not `error`.
  - Call `ensure_unique_index_on_hash` before writing.
- Convert `_id` strings to `ObjectId` here and only here (the staging module stays `pymongo`-free).
- Security review: confirm that 01.0/04's key validation makes operator injection impossible through `ReplaceOne`
  documents; note in the threat model.
- Deprecate `database.save_books_to_mongodb` (remove it if 02.0/02 left no callers).

## Files

- Create `src/bookscraper/backends/mongo/push_writer.py`.
- Modify `src/bookscraper/backends/mongo/database.py`.
- Create `tests/mock/backends/mongo/test_push_writer.py` (fake collection / `mongomock`, per 0001 01.0/08's
  dependency decision).

## Tests

`test_inserts_and_replaces_in_one_bulk_write`, `test_in_queries_chunked`, `test_bulk_write_error_mapped_to_items`,
`test_concurrent_duplicate_becomes_duplicate`, `test_replace_removes_deleted_field`,
`test_unique_index_ensured_first`, `test_no_write_when_plan_empty`.

## Success criteria

- [x] One network round-trip for all writes in a run (plus the two lookup queries).
- [x] Every plan item ends with exactly one outcome.
