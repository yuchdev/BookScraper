# 01 - StorageBackend contract

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/README.md)
**Status:** ✅ Complete
**Role:** Architect → Python Expert

## Context

Before this task, the scrapers wrote to MongoDB directly and optionally dumped a throwaway CSV nobody read back.
Callers (`commands/scrape_urls.py`, `commands/search.py`, `scraping/scrape_details.py`) now need a single abstract
seam they can depend on without knowing which store is behind it.

## Requirements

- An abstract base class `StorageBackend` in `src/bookscraper/backends/storage.py` with exactly these methods:

  | Method                                                                  | Returns | Semantics                                                      |
  |-------------------------------------------------------------------------|---------|----------------------------------------------------------------|
  | `save_books(books: list[dict]) -> None` *(abstract)*                    | `None`  | Insert each book; silently skip any whose `hash` already exists |
  | `book_exists_by_hash(book_hash: str) -> bool` *(abstract)*              | `bool`  | Exact match on `hash`; falsy input → `False`                   |
  | `book_exists_by_asin(asin: str) -> bool` *(abstract)*                   | `bool`  | Exact match on `asin`; falsy input → `False`                   |
  | `leanpub_book_exists(book_id, book_slug) -> bool` *(abstract)*          | `bool`  | Match on `book_id` **or** `slug`; both falsy → `False`         |
  | `close() -> None` *(concrete no-op)*                                    | `None`  | Release connections; default does nothing                      |

- `backends/__init__.py` re-exports `StorageBackend`, `MongoBackend`, `JsonBackend`, `resolve_store_backend`.
- `scraping/scrape_details.py` imports `StorageBackend` only as a type (`from ..backends import StorageBackend`).

## Files

- Create `src/bookscraper/backends/storage.py` - `StorageBackend`.
- Modify `src/bookscraper/backends/__init__.py` - re-exports.

## Tests

- Covered through the concrete backends in `tests/mock/backends/test_storage.py`
  (`test_close_is_noop_default` exercises the concrete default).

## Success criteria

- [x] `StorageBackend()` cannot be instantiated (abstract).
- [x] No module under `backends/` imports from `scraping/` or `commands/`.
