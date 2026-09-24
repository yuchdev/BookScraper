# 04 - Honest connection logging

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/02.0-mongodb-auth-configuration/README.md)
**Status:** ⬜ Not started
**Role:** Python Expert

## Context

`backends/mongo/database.py::get_mongo_collection()` prints `"Connection to MongoDB Atlas successful."` (via
`print_log(..., "success")` and `module_logger.info`) right after calling `_initialize_mongodb_connection()`, whether
or not that call returned `(None, None)`. A failed run therefore shows a red *critical* line followed by a green
*success* line. CLAUDE.md lists this under *Known inconsistencies*. The `except Exception` branch in
`get_mongo_collection()` is also effectively unreachable, because `_initialize_mongodb_connection()` already catches
everything.

## Requirements

- Emit the success message **only** when `_mongo_collection is not None` after initialization.
- On `None`, emit nothing extra: `_initialize_mongodb_connection()` has already logged the specific cause, and
  `resolve_store_backend()` prints the `FAILED` line. There must be no duplicate or contradictory line.
- Keep the defensive `except Exception` (a future refactor could make init raise) and cover it with a unit test.
- The success message names the database and collection (`bookscraper_db.books`), never the host or URI.
- Remove the stale commented-out `print_log` / `module_logger` lines in `_initialize_mongodb_connection()`.
- Delete the corresponding bullet from CLAUDE.md *Known inconsistencies to watch for*.

## Files

- Modify `src/bookscraper/backends/mongo/database.py` - `get_mongo_collection`, `_initialize_mongodb_connection`.
- Modify `tests/mock/backends/mongo/test_database.py`.
- Modify `CLAUDE.md`.

## Tests

- `test_success_message_only_printed_when_collection_initialized`
- `test_no_success_message_when_initialization_returns_none` (capture `print_log` calls; assert no `"success"` status)
- `test_success_message_names_db_and_collection_not_uri`
- existing `test_initialize_raises_is_caught_and_returns_none` still passes.

## Success criteria

- [ ] A failed connection emits zero lines with status `success`.
- [ ] CLAUDE.md no longer lists this inconsistency.
