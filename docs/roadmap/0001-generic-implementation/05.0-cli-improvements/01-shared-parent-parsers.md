# 01 - Shared parent parsers

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/README.md)
**Status:** ⬜ Not started
**Role:** Python Expert

## Requirements

- In `cli.py`, build reusable `argparse.ArgumentParser(add_help=False)` parents:
  - `_logging_parent()` → `--log-severity` (identical choices, default, help, `dest`).
  - A staging parent that adds `--staging-file` and `--check-mongo` using the existing `_add_staging_file_flag`
    and `_add_check_mongo_flag` helpers (already defined in `cli.py` by milestone 0002). The original scope called
    for `_store_parent()` / `--store-backend` / `STORE_BACKENDS` from `backends/storage.py`; those are gone.
    `backends/storage.py` no longer exists.
  - `_scrape_runtime_parent()` → filled by subtasks 03-04 (browser, concurrency, dry run, output dir).
- Each subparser declares `parents=[...]` instead of repeating definitions.
- Behavior must be **byte-identical** for existing invocations: every existing `tests/unit/test_cli.py` test passes
  unchanged.
- Add `formatter_class=argparse.ArgumentDefaultsHelpFormatter` (or explicit `(default: …)` text, whichever
  `--help` renders more cleanly) consistently on all subparsers.

## Files

- Modify `src/bookscraper/cli.py` (note: `backends/storage.py` no longer exists; `_add_staging_file_flag` and
  `_add_check_mongo_flag` helpers are already in `cli.py`).
- Modify `tests/unit/test_cli.py`.

## Tests

- all existing `tests/unit/test_cli.py` tests (unchanged)
- `test_log_severity_defined_once_in_source` (AST/grep guard: exactly one `"--log-severity"` literal in `cli.py`)
- `test_cli_default_matches_staging_module_constant` (already exists: verifies the CLI default for `--staging-file`
  matches `staging.DEFAULT_STAGING_FILE`; replaces the original `test_store_backend_choices_match_resolver`)

## Success criteria

- [ ] `grep -c '"--log-severity"' src/bookscraper/cli.py` == 1.
