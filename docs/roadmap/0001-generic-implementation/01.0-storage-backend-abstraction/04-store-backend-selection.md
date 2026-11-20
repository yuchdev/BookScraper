# 04 - `--store-backend` selection & pre-flight

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/README.md)
**Status:** ✅ Complete
**Depends on:** [02-json-local-store.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/02-json-local-store.md), [03-mongo-backend-wrapper.md](/docs/roadmap/0001-generic-implementation/01.0-storage-backend-abstraction/03-mongo-backend-wrapper.md)
**Role:** Python Expert

> **Historical note (reverted by 0002):** `--store-backend` was removed outright by milestone 0002; passing it is
> now an argparse exit-2 error. `scrape-urls` and `search` instead use `--staging-file` (default
> `staging/books.json`) and `resolve_dedup_source(staging_file, check_mongo)` for dedup. The write path moved to
> `bookscraper push`. See [0002 02.0/01](/docs/roadmap/0002-staged-review-and-mongo-sync/02.0-scrape-to-staging/01-cli-staging-file-flag.md).
> Requirements below are historical context.

## Context

The old interactive `(C)/(M)/(B)/(E)` prompt made it impossible to tell from a command line which store a run
touched. The choice is now a required flag.

## Requirements

- `cli.py`: `--store-backend` with `dest="store_backend"`, `choices=["mongo", "json"]`, `required=True` on both
  `scrape-urls` and `search`; omitting it is an argparse error (exit code 2).
- `resolve_store_backend(name) -> StorageBackend`:
  - `"mongo"` → `database.get_mongo_collection()`; `None` → `print_log` error + `sys.exit(1)`.
  - `"json"` → `check_local_write_permission()`; `False` → `print_log` error + `sys.exit(1)`.
  - anything else → error + `sys.exit(1)` without running any check.
- `commands/scrape_urls.run()` and `commands/search.run()` call it exactly once, up front, and `close()` the
  backend at the end of the run.

## Files

- Modify `src/bookscraper/cli.py`, `src/bookscraper/backends/storage.py`, `src/bookscraper/commands/scrape_urls.py`,
  `src/bookscraper/commands/search.py`.

## Tests

`tests/unit/test_cli.py`: `test_scrape_urls_requires_store_backend`, `test_search_requires_store_backend`,
`test_invalid_store_backend_choice_rejected`.
`tests/mock/backends/test_storage.py`: `test_mongo_with_live_collection_returns_mongo_backend`,
`test_mongo_with_no_collection_exits`, `test_json_with_write_permission_returns_json_backend`,
`test_json_without_write_permission_exits`, `test_unknown_backend_exits_without_running_checks`.

## Success criteria

- [x] There is no code path that picks a backend implicitly.
