# 06 - Generic detail-scrape dispatch

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-search-provider-registry.md](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/01-search-provider-registry.md)
**Role:** Python Expert

## Requirements

- `commands/search.py` sends every surviving candidate to one dispatcher:
  `scrape_candidate(c: SearchCandidate, ctx) -> ScrapeResult`, which routes to `get_leanpub_book_details(c.detail_ref,
  client)` or `scrape_book(c.detail_ref, browser, c.site, backend)`, all through `bounded_gather` (task 03.2
  subtask 06).
- Remove the misleading `print_log(f"--- Scraping {len(book_data_to_scrape)} Book Data ---")`, which prints the
  number of **keys** in one dict, once per book.
- `commands/search.py` and `commands/scrape_urls.py` share one summary/diagnostics helper
  (`commands/_reporting.py`: status counts, `failed_*.csv` writing, timing). Delete the two duplicated
  `save_failed_urls_to_csv` implementations.
- `storage_backend.save_books()` runs via `asyncio.to_thread` in `search` too (today only `scrape_urls` does
  this; `search` blocks the event loop).

## Files

- Modify `src/bookscraper/commands/search.py`, `src/bookscraper/commands/scrape_urls.py`.
- Create `src/bookscraper/commands/_reporting.py`.
- Tests: `tests/mock/commands/test_search.py`, `tests/mock/commands/test_scrape_urls.py`,
  `tests/mock/commands/test_reporting.py`.

## Tests

- `test_dispatch_routes_leanpub_to_api_and_others_to_playwright`
- `test_search_save_runs_off_event_loop`
- `test_single_failed_csv_writer_used_by_both_commands`
- `test_misleading_key_count_log_removed`

## Success criteria

- [ ] `grep -c "def save_failed_urls_to_csv" -r src/` == 0 (replaced by the shared helper).
