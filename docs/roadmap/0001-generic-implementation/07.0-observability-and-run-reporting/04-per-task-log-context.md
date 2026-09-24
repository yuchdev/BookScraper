# 04 - Per-task log context

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/07.0-observability-and-run-reporting/README.md)
**Status:** ⬜ Not started
**Depends on:** [02-logger-hierarchy.md](/docs/roadmap/0001-generic-implementation/07.0-observability-and-run-reporting/02-logger-hierarchy.md)
**Role:** Python Expert

## Requirements

- `book_utils.scrape_context: contextvars.ContextVar[str]` (default `"-"`). `scrape_book`,
  `get_leanpub_book_details`, and each search provider set it to `<site>:<short-id>` (ASIN, slug, or the last path
  segment) for the duration of the call. asyncio tasks inherit context, so concurrent scrapes stay separated.
- A `ContextFilter` injects `record.ctx` on every record. The file formatter becomes
  `%(asctime)s %(levelname)s [%(ctx)s] %(name)s: %(message)s`.
- `print_log` does **not** add the context to console output (the console stays readable). It is file-only.

## Files

- Modify `src/bookscraper/book_utils.py`, `src/bookscraper/scraping/scrape_details.py`,
  `src/bookscraper/scraping/search_providers.py` (or `search_utils.py` before task 03.3).
- Tests: `tests/mock/test_book_utils.py`, `tests/mock/scraping/test_scrape_details.py`.

## Tests

- `test_context_var_isolated_between_concurrent_tasks`
- `test_file_log_lines_carry_scrape_context`
- `test_console_output_unchanged_by_context`

## Success criteria

- [ ] `grep "amazon:1098131029" <run log>` isolates one book's full history from a 10-way concurrent run.
