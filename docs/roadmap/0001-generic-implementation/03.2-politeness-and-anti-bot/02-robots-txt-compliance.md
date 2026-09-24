# 02 - robots.txt compliance

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/README.md)
**Status:** ⬜ Not started
**Role:** Scraping Expert

## Requirements

- `scraping/politeness.py::RobotsCache`:
  - `async def allowed(self, url: str, user_agent: str) -> bool` - fetches `https://<host>/robots.txt` **once per
    host per run** with the shared httpx client (10 s timeout), and parses it with stdlib `urllib.robotparser`.
  - Fetch failure / 5xx → **disallow** for that host for the rest of the run, with a warning (fail closed).
    404 → allow everything (the RFC 9309 convention).
  - Honors `Crawl-delay` when present, by lowering that host's `RateLimiter` rate to `1 / crawl_delay` if that's
    slower than configured.
- A disallowed URL → `ScrapeResult(status=SKIPPED_ROBOTS)`. Add that member to `ScrapeStatus` (task 03.0 subtask
  02). It is counted in the summary and written to `failed_books.csv`.
- **Opt-out:** `--ignore-robots` flag on `scrape-urls` and `search` (task 05.0), off by default. When set, print a
  one-time warning and record it in the run report (task 07.0 subtask 03).
- The Leanpub JSON API paths (`/api/v1/...`) go through the same check. If `robots.txt` disallows them, the check
  wins.

## Files

- Modify `src/bookscraper/scraping/politeness.py`, `scrape_details.py`, `search_utils.py`,
  `src/bookscraper/scraping/results.py`, `src/bookscraper/cli.py`, both commands.
- Tests: `tests/unit/scraping/test_politeness.py`, the mock command and scraping tests.
- Fixtures: `tests/fixtures/robots/{amazon,leanpub}.txt` (captured by task 06.0's capture script).

## Tests

- `test_robots_disallowed_path_returns_false`
- `test_robots_fetched_once_per_host`
- `test_robots_fetch_error_fails_closed`
- `test_robots_404_allows_all`
- `test_crawl_delay_lowers_rate_limit`
- `test_disallowed_url_yields_skipped_robots_result`
- `test_ignore_robots_flag_bypasses_check_and_warns_once`

## Success criteria

- [ ] With the default flags, no request is ever sent to a path the captured `robots.txt` disallows (mock-verified).
