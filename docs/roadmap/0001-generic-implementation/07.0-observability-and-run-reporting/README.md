# Task 07.0 - Observability & Run Reporting

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** ⬜ Not started
**Category:** feature | **Priority:** P2
**Depends on:** [Task 03.0](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/README.md)

## Scope

Logging works (a per-run file under `~/.bookscrapper/logs/`, the 10 most recent kept, the root-logger handler),
but:

- Nothing stops a secret from reaching a log line. For example, a pymongo exception string or an `httpx` error can
  embed a `mongodb+srv://user:pass@…` URI or a bearer token, and `print_log` writes the same text to the console
  and the file.
- Logger names are ad hoc: `"scrape_details"`, `"bookscraper_app"`, `__name__`. Third-party loggers (pymongo,
  httpx, anthropic) propagate to the same root handler at the same level, so `--log-severity debug` drowns in
  library noise.
- A run's outcome exists only as console prose. No automation (cron, CI, a future dashboard) can read "how many
  books, from where, how many failed, why" without scraping the log.

## Subtasks

| #  | Document                                                          | Status         | Blocks |
|----|-------------------------------------------------------------------|----------------|--------|
| 01 | [Secret-redacting log filter](/docs/roadmap/0001-generic-implementation/07.0-observability-and-run-reporting/01-secret-redacting-log-filter.md) | ⬜ Not started | -  |
| 02 | [Logger hierarchy & library noise](/docs/roadmap/0001-generic-implementation/07.0-observability-and-run-reporting/02-logger-hierarchy.md)  | ⬜ Not started | 04     |
| 03 | [Machine-readable run report](/docs/roadmap/0001-generic-implementation/07.0-observability-and-run-reporting/03-run-report.md)             | ⬜ Not started | -      |
| 04 | [Per-task log context](/docs/roadmap/0001-generic-implementation/07.0-observability-and-run-reporting/04-per-task-log-context.md)          | ⬜ Not started | -      |

## Key constraints

- `print_log()` stays the console API. Its behavior changes only by gaining redaction.
- `configure_logging()`'s idempotence (safe to call twice per run) must be preserved.
- Security-sensitive: subtask 01 gets a `security-auditor` review of its pattern list.
