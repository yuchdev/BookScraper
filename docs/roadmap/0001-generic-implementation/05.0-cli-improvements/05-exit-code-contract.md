# 05 - Exit-code contract & interrupt handling

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/README.md)
**Status:** ⬜ Not started
**Role:** Python Expert

## Requirements

- New `src/bookscraper/exitcodes.py`:

  | Constant             | Value | Meaning                                                                 |
  |----------------------|-------|-------------------------------------------------------------------------|
  | `OK`                 | 0     | Completed; nothing failed                                               |
  | `FATAL`              | 1     | Could not run / aborted: config error, pre-flight failure, unhandled exception |
  | `USAGE`              | 2     | argparse error (argparse's own convention - documented, not re-implemented) |
  | `PARTIAL`            | 3     | Completed, but ≥ 1 item failed (scrape `FAILED`/`TIMEOUT`/`PARSE_ERROR`/`BLOCKED`, or `SaveSummary.errors > 0`); also `rotate-cert --check` "expiring soon" |
  | `DRIFT`              | 4     | `detect-schema`: a current selector matches no sample (task 04.0 subtask 08) |
  | `INTERRUPTED`        | 130   | Ctrl+C                                                                  |

- Each `commands/*.run()` **returns** an exit code instead of calling `sys.exit` mid-function (the
  `scrape-urls` no-URLs path, `resolve_store_backend`'s exits, etc.). `resolve_store_backend` raises a new
  `BackendUnavailableError`, which `main()` maps to `FATAL`.
- `main_sync()` wraps `asyncio.run(main())` and calls `sys.exit(code)`. `KeyboardInterrupt` → a one-line
  `print_log("Interrupted.", "warning")` and exit `130`, with no traceback. The backend still gets `close()`d:
  commands close it in `finally`.
- `DUPLICATE` and filtered-out items are **not** failures (the run did what it should).

## Files

- Create `src/bookscraper/exitcodes.py`.
- Modify `src/bookscraper/main.py`, every `src/bookscraper/commands/*.py`, `src/bookscraper/backends/storage.py`.
- Tests: `tests/mock/test_main.py`, `tests/mock/commands/*`, `tests/mock/backends/test_storage.py`.

## Tests

- `test_main_maps_backend_unavailable_to_fatal`
- `test_keyboard_interrupt_exits_130_without_traceback`
- `test_backend_closed_on_interrupt`
- `test_scrape_urls_returns_partial_when_any_failed`
- `test_scrape_urls_returns_ok_when_only_duplicates`
- `test_no_valid_urls_returns_ok_without_sys_exit_in_command`
- `test_no_sys_exit_calls_in_commands_package` (AST guard)

## Success criteria

- [ ] `grep -rn "sys.exit" src/bookscraper/commands src/bookscraper/backends` returns nothing.
- [ ] Exit codes are documented in `--help` epilog and the CLI reference (subtask 08).
