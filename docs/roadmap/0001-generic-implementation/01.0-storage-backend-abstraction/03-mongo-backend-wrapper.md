# 03 - Mongo backend wrapper

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/README.md)
**Status:** ✅ Complete
**Depends on:** [01-storage-backend-contract.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/01-storage-backend-contract.md)
**Role:** Python Expert

## Context

The MongoDB functions already existed in `backends/mongo/database.py` and `backends/mongo/deduplicate.py`; this
subtask wraps them behind the contract without rewriting them.

## Requirements

- `MongoBackend(collection: Collection)` stores the collection and delegates:
  `save_books` → `database.save_books_to_mongodb`, `book_exists_by_hash` → `database.check_book_exists_in_db`,
  `book_exists_by_asin` → `database.check_amazon_asin_exists_in_db`,
  `leanpub_book_exists` → `deduplicate.leanpub_prescrape_deduplicate`, `close` → `database.close_mongo_connection`.
- The unique `hash` index is ensured by `database.ensure_unique_index_on_hash()` during connection initialization.

## Files

- Modify `src/bookscraper/backends/storage.py` - `MongoBackend`.

## Tests

`tests/mock/backends/test_storage.py`: `test_save_books_delegates_to_database`,
`test_book_exists_by_hash_returns_delegated_value`, `test_book_exists_by_asin_returns_delegated_value`,
`test_leanpub_book_exists_delegates_to_deduplicate`, `test_close_delegates_to_database`.
`tests/mock/backends/mongo/test_database.py`: `test_creates_index_when_absent`, `test_skips_create_when_present`.

## Success criteria

- [x] `MongoBackend` holds no logic of its own beyond delegation.
