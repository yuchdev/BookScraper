# 06 - Bounded concurrency

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-per-domain-rate-limiter.md](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/01-per-domain-rate-limiter.md)
**Role:** Python Expert

## Context

`commands/scrape_urls.py` slices URLs into fixed batches of 10, `gather`s each batch, then sleeps 3-5 s. One slow
page stalls its nine siblings, and the pause is paid even when the next batch is for a different, idle site.
`commands/search.py` `gather`s **all** Leanpub detail requests at once with no bound at all.

## Requirements

- `politeness.bounded_gather(coros_or_factories, *, limit: int) -> list[T]`: an `asyncio.Semaphore`-based sliding
  window that preserves input order in the results. Exceptions are captured per item, like
  `gather(return_exceptions=True)`.
- `scrape_urls`: replace batching and the inter-batch sleep with `bounded_gather(..., limit=concurrency)` (default
  `4`, CLI `--concurrency` in task 05.0). Per-host pacing is now the rate limiter's job.
- `search`: detail scraping goes through `bounded_gather` with the same `concurrency`.
- Progress: every 10 completions (or 10%), print one line `N/M done (ok=a dup=b fail=c)`.

## Files

- Modify `src/bookscraper/scraping/politeness.py`, `src/bookscraper/commands/scrape_urls.py`,
  `src/bookscraper/commands/search.py`.
- Tests: `tests/unit/scraping/test_politeness.py`, `tests/mock/commands/test_scrape_urls.py`,
  `tests/mock/commands/test_search.py`.

## Tests

- `test_bounded_gather_never_exceeds_limit` (instrumented coroutines record peak concurrency)
- `test_bounded_gather_preserves_order`
- `test_bounded_gather_captures_exceptions_per_item`
- `test_scrape_urls_no_longer_sleeps_between_batches`
- `test_search_detail_scrape_is_bounded`

## Success criteria

- [ ] `grep -n "batch_size" src/bookscraper/commands/scrape_urls.py` returns nothing.
