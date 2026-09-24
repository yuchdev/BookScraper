# 01 - Per-domain rate limiter

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/README.md)
**Status:** ⬜ Not started
**Role:** Scraping Expert

## Requirements

- `scraping/politeness.py`:

  ```python
  class RateLimiter:
      def __init__(self, rate_per_sec: float, burst: int = 1, *, clock=time.monotonic, sleep=asyncio.sleep): ...
      async def acquire(self) -> None: ...        # token bucket; never busy-waits

  class DomainRateLimiters:
      def __init__(self, config: dict[str, tuple[float, int]], default: tuple[float, int]): ...
      def for_url(self, url: str) -> RateLimiter: ...   # keyed on registrable host (strip "www.")
  ```

- New `RATE_LIMITS` in `parameters.py`: `{"amazon.com": (0.5, 1), "packtpub.com": (1.0, 2),
  "oreilly.com": (1.0, 2), "leanpub.com": (2.0, 4)}`, `DEFAULT_RATE_LIMIT = (0.5, 1)`. Values are requests/second
  and burst.
- Integration points (each awaits `limiters.for_url(url).acquire()` immediately before the request):
  `scrape_book()` before `page.goto`; `get_leanpub_book_details()` before `client.get`;
  `get_leanpub_search_results_via_api()` before each page; `get_search_results_via_playwright()` before each
  `goto`.
- One `DomainRateLimiters` instance per run, created in the command and passed down (an optional parameter, with
  a default of a module-level instance so existing call sites keep working during the transition).
- Remove the ad hoc `asyncio.sleep(random.uniform(0.5, 1.5))` in the Leanpub search loop (the limiter replaces it).
  **Keep** the inter-batch pause until subtask 06 replaces batching.

## Files

- Create `src/bookscraper/scraping/politeness.py`.
- Modify `src/bookscraper/scraping/parameters.py`, `scrape_details.py`, `search_utils.py`,
  `src/bookscraper/commands/scrape_urls.py`, `src/bookscraper/commands/search.py`.
- Create `tests/unit/scraping/test_politeness.py`; modify the relevant mock tests.

## Tests

- `test_rate_limiter_spaces_requests_at_configured_rate` (fake clock: 5 acquires at 0.5 rps → ≥ 8 s simulated)
- `test_rate_limiter_allows_burst_then_throttles`
- `test_domain_limiters_share_limiter_for_www_and_bare_host`
- `test_unknown_domain_uses_default`
- `test_scrape_book_acquires_before_goto` (call-order recording)
- `test_leanpub_search_no_longer_sleeps_randomly`

## Success criteria

- [ ] A 20-URL Amazon `scrape-urls` run issues navigations no faster than 0.5/s (verified with the fake clock in a
      mock test).
