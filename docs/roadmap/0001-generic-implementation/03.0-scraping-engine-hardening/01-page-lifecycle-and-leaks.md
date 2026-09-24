# 01 - Page lifecycle & leak fixes

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/README.md)
**Status:** ⬜ Not started
**Role:** Python Expert

## Context

In `scrape_book()`, `page = await browser.new_page()` runs at the top of every attempt, **before** the
`if site == "leanpub": return await get_leanpub_book_details(url)` branch, which never touches the page and never
closes it. The timeout handler also closes the page and then closes it again in its `else` branch. And the
fall-through after the loop references `page`, which may belong to an already-closed attempt.

## Requirements

- Dispatch Leanpub **before** any Playwright resource is created. `scrape_book()` calls
  `get_leanpub_book_details()` directly and never opens a page for `site == "leanpub"`.
- Each attempt owns exactly one page in a `try/finally` (or an `async with` helper `_attempt_page(browser)` built on
  `contextlib.asynccontextmanager`), so the page is closed **exactly once** on every exit path: success, duplicate,
  404, timeout, or any other exception.
- Remove every inline `await page.close()` from the branches.
- Remove the unreachable post-loop `if page: await page.close()` block. The function ends with an explicit failure
  return once the loop is exhausted.
- `page.close()` failures (e.g. the browser already crashed) are logged at debug level and swallowed inside the
  `finally`, so they never mask the original exception.

## Files

- Modify `src/bookscraper/scraping/scrape_details.py`.
- Modify `tests/mock/scraping/test_scrape_details.py`; extend `tests/mock/scraping/playwright_fakes.py` with a
  close-call counter on the fake page if it doesn't already have one.

## Tests

- `test_leanpub_dispatch_opens_no_playwright_page`
- `test_page_closed_exactly_once_on_success`
- `test_page_closed_exactly_once_on_duplicate`
- `test_page_closed_exactly_once_on_404`
- `test_page_closed_exactly_once_per_attempt_on_repeated_timeout` (3 attempts → 3 closes, never 4+)
- `test_close_failure_does_not_mask_original_error`

## Success criteria

- [ ] For every fake-page test, `new_page` call count == `close` call count.
- [ ] `grep -c "page.close()" src/bookscraper/scraping/scrape_details.py` ≤ 1.
