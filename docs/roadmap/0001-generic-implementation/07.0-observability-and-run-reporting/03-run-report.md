# 03 - Machine-readable run report

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/07.0-observability-and-run-reporting/README.md)
**Status:** ⬜ Not started
**Depends on:** [Task 03.0 / 02](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/02-typed-scrape-result.md)
**Role:** Python Expert

## Requirements

- Every `scrape-urls` / `search` / `migrate` run writes `~/.bookscrapper/logs/bookscraper_<ts>_<pid>.report.json`
  (the same stem as the log file, pruned together with it by `_prune_old_logs`), and also writes to `--report PATH`
  when given:

  ```json
  {
    "schema": 1,
    "command": "search",
    "argv": ["search", "--store-backend", "json", "--site", "amazon"],
    "started_at": "…Z", "finished_at": "…Z", "duration_s": 123.4,
    "versions": {"bookscraper": "…", "python": "…", "playwright": "…", "chromium": "…"},
    "store_backend": "json",
    "flags": {"dry_run": false, "ignore_robots": false, "filters_enabled": true},
    "per_site": {"amazon": {"SUCCESS": 12, "DUPLICATE": 3, "BLOCKED": 1, "FILTERED": 4}},
    "save": {"inserted": 12, "duplicates": 0, "errors": 0},
    "exit_code": 3
  }
  ```

- `argv` is redacted with `redact()` (subtask 01), in case a user passes a secret on the command line.
- The writer is a small `RunReport` class in `commands/_reporting.py` (shared with task 03.3 subtask 06). Commands
  record into it, and `main()` finalizes it with the exit code in a `finally`, so interrupted runs still get a
  report (`exit_code: 130`).
- `_prune_old_logs` treats `<stem>.log` and `<stem>.report.json` as one unit.

## Files

- Modify `src/bookscraper/commands/_reporting.py`, `src/bookscraper/main.py`, `src/bookscraper/book_utils.py`
  (pruning), the commands, `src/bookscraper/cli.py` (`--report`).
- Tests: `tests/mock/commands/test_reporting.py`, `tests/mock/test_main.py`, `tests/unit/test_book_utils.py`.

## Tests

- `test_report_written_next_to_log_with_same_stem`
- `test_report_counts_match_scrape_results`
- `test_report_written_on_interrupt_with_130`
- `test_report_argv_redacted`
- `test_prune_removes_log_and_report_together`
- `test_report_flag_writes_additional_copy`

## Success criteria

- [ ] A cron wrapper can decide "alert or not" from `exit_code` plus `per_site` alone.
