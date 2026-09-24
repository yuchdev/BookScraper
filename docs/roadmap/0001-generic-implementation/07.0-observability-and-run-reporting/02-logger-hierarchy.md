# 02 - Logger hierarchy & library noise

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/07.0-observability-and-run-reporting/README.md)
**Status:** ⬜ Not started
**Role:** Python Expert

## Requirements

- Every module uses `logging.getLogger(__name__)`, which yields `bookscraper.<subpackage>.<module>`. Replace
  `getLogger("scrape_details")`, `getLogger("bookscraper_app")`, and any other literal names.
- `configure_logging(level)`:
  - Keeps the file handler on the **root** logger (so nothing is lost), with the root at `WARNING`.
  - Sets `logging.getLogger("bookscraper").setLevel(level)`. The app's own messages follow `--log-severity`.
  - Third-party loggers (`pymongo`, `httpx`, `httpcore`, `anthropic`, `asyncio`) stay at `WARNING`, unless the new
    env var `BOOKSCRAPER_LIB_LOG_LEVEL` overrides them (e.g. `DEBUG` when diagnosing a TLS handshake).
- Update the CLAUDE.md *Logging* section. It currently explains the root-logger choice by the ad hoc names this
  subtask removes.

## Files

- Modify `src/bookscraper/book_utils.py` and every module with a literal logger name
  (`scraping/scrape_details.py`, `book_utils.py`'s own logger, `commands/search.py`, …).
- Modify `CLAUDE.md`.
- Tests: `tests/mock/test_book_utils.py`, `tests/unit/test_logger_names.py`.

## Tests

- `test_all_modules_use_dunder_name_logger` (AST guard over `src/bookscraper/`)
- `test_app_logger_follows_log_severity`
- `test_library_loggers_default_to_warning`
- `test_lib_log_level_env_override`
- `test_configure_logging_still_idempotent`

## Success criteria

- [ ] `--log-severity debug` output contains no `httpcore` / `pymongo` debug lines unless explicitly requested.
