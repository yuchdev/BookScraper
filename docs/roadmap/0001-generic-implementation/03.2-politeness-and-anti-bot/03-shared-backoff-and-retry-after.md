# 03 - Shared backoff & Retry-After

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-per-domain-rate-limiter.md](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/01-per-domain-rate-limiter.md)
**Role:** Python Expert

## Requirements

- `scraping/politeness.py::retry_async`:

  ```python
  async def retry_async(
      fn: Callable[[], Awaitable[T]],
      *,
      attempts: int = 3,
      base_delay: float = 2.0,
      max_delay: float = 60.0,
      is_retryable: Callable[[BaseException], bool],
      retry_after: Callable[[BaseException], Optional[float]] = lambda e: None,
      sleep=asyncio.sleep,
      rng: random.Random = random.Random(),
  ) -> T
  ```

  Full-jitter exponential backoff: `delay = rng.uniform(0, min(max_delay, base_delay * 2**(n-1)))`. If
  `retry_after(e)` returns a value, use `min(max_delay, value)` instead. The last exception re-raises unchanged.
- `httpx_retry_after(e) -> Optional[float]`: reads the `Retry-After` header (delta-seconds **or** HTTP-date) from
  an `httpx.HTTPStatusError` with status 429 or 503.
- `is_transient(e) -> bool`: the classification defined in task 03.0 subtask 03 (moved here so there's one
  definition), **plus** `httpx.HTTPStatusError` with status 429/502/503/504.
- Replace:
  - `scrape_book()`'s hand-rolled `for attempt in range(1, retries + 1)` + `random.uniform(2, 4)`.
  - Leanpub search / detail single-shot requests (they currently fail on the first 429).

## Files

- Modify `src/bookscraper/scraping/politeness.py`, `scrape_details.py`, `search_utils.py`.
- Tests: `tests/unit/scraping/test_politeness.py`, `tests/mock/scraping/test_scrape_details.py`,
  `tests/mock/scraping/test_search_utils.py`.

## Tests

- `test_retry_async_succeeds_after_transient_failures`
- `test_retry_async_does_not_retry_non_retryable`
- `test_retry_async_backoff_is_capped_and_jittered` (seeded rng, recorded sleeps)
- `test_retry_after_seconds_header_respected`
- `test_retry_after_http_date_header_respected`
- `test_leanpub_search_retries_on_429_then_succeeds`

## Success criteria

- [ ] `grep -n "random.uniform(2, 4)" src/` returns nothing.
