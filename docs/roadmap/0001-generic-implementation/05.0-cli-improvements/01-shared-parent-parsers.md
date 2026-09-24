# 01 - Shared parent parsers

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/README.md)
**Status:** ⬜ Not started
**Role:** Python Expert

## Requirements

- In `cli.py`, build reusable `argparse.ArgumentParser(add_help=False)` parents:
  - `_logging_parent()` → `--log-severity` (identical choices, default, help, `dest`).
  - `_store_parent()` → `--store-backend` (required, choices from a single constant `STORE_BACKENDS = ("mongo",
    "json")`, which `backends/storage.py` also uses, so the two lists can't drift).
  - `_scrape_runtime_parent()` → filled by subtasks 03-04 (browser, concurrency, dry run, output dir).
- Each subparser declares `parents=[...]` instead of repeating definitions.
- Behavior must be **byte-identical** for existing invocations: every existing `tests/unit/test_cli.py` test passes
  unchanged.
- Add `formatter_class=argparse.ArgumentDefaultsHelpFormatter` (or explicit `(default: …)` text, whichever
  `--help` renders more cleanly) consistently on all subparsers.

## Files

- Modify `src/bookscraper/cli.py`, `src/bookscraper/backends/storage.py` (import `STORE_BACKENDS` or define it
  there and import into `cli.py`; pick the direction that doesn't make `cli.py` import heavy modules).
- Modify `tests/unit/test_cli.py`.

## Tests

- all existing `tests/unit/test_cli.py` tests (unchanged)
- `test_log_severity_defined_once_in_source` (AST/grep guard: exactly one `"--log-severity"` literal in `cli.py`)
- `test_store_backend_choices_match_resolver`

## Success criteria

- [ ] `grep -c '"--log-severity"' src/bookscraper/cli.py` == 1.
