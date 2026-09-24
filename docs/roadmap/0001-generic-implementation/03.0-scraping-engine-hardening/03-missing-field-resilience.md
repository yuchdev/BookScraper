# 03 - Missing-field resilience & retry policy

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/README.md)
**Status:** ⬜ Not started
**Depends on:** [02-typed-scrape-result.md](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/02-typed-scrape-result.md)
**Role:** Python Expert

## Context

- `book_details` starts as `{"url", "site"}`, and other keys are added only on specific branches. The hash line
  then reads `book_details["title"]` and `book_details["publication_year"]` unconditionally. If `BOOK_TITLE` is
  unset, or the publication-date block raises (its `except` only logs), the result is a `KeyError`. The generic
  `except Exception` then **retries a deterministic bug three times**, sleeping 2-4 s between tries.
- The title read (`page.locator(...).first.text_content()`) has no explicit timeout, so a missing title element
  blocks for Playwright's 30 s default on every attempt.
- `pub_date_text.split("T")` raises `AttributeError` when the meta tag exists but has no `content`.

## Requirements

- Initialize every output key up front with a neutral value: `title=None`, `authors=[]`,
  `publication_date=None`, `publication_year=None`, `isbn10="N/A"`, `isbn13="N/A"`, `description=None`, `tags=[]`.
  (Keep the `"N/A"` ISBN sentinel for now; 03.1 replaces it.)
- Module constants in `scrape_details.py` (overridable later by 05.0's CLI):
  `NAVIGATION_TIMEOUT_MS = 60_000`, `FIELD_TIMEOUT_MS = 10_000`, `OPTIONAL_FIELD_TIMEOUT_MS = 2_000`,
  `MAX_ATTEMPTS = 3`. Every locator read passes an explicit timeout: required fields (title) use
  `FIELD_TIMEOUT_MS`, optional ones use `OPTIONAL_FIELD_TIMEOUT_MS`.
- **Required-field rule:** a missing or empty `title` after the page loads → `ScrapeResult(PARSE_ERROR,
  error="title not found")`, with **no retry**.
- **Retry only transient failures:** `playwright.async_api.TimeoutError`, `playwright.async_api.Error` whose message
  indicates navigation or network failure (`net::ERR_`, `Target closed`, `Navigation failed`), and `httpx`
  transport errors. `KeyError`, `AttributeError`, `ValueError`, `TypeError` are bugs → `FAILED` immediately, logged
  with `exc_info=True`.
- Every per-field extraction goes through one helper:
  `async def _text(page, selector, *, timeout_ms, first=True) -> Optional[str]`, which returns stripped text or
  `None` on timeout or absence and never raises for a missing element. It replaces the scattered try/except blocks.

## Files

- Modify `src/bookscraper/scraping/scrape_details.py`.
- Modify `tests/mock/scraping/test_scrape_details.py`.

## Tests

- `test_missing_title_selector_returns_parse_error_without_retry` (assert `new_page` called once)
- `test_empty_title_text_returns_parse_error`
- `test_publication_date_exception_still_returns_book_with_none_year`
- `test_meta_date_without_content_attribute_is_none_not_crash`
- `test_timeout_is_retried_up_to_max_attempts`
- `test_keyerror_like_bug_is_not_retried`
- `test_every_locator_read_passes_explicit_timeout` (fake page records the `timeout` kwargs)
- `test_text_helper_returns_none_on_timeout`

## Success criteria

- [ ] A page with a title but no other fields yields `SUCCESS` with neutral values, never `FAILED`.
- [ ] 06.0 goldens: `packtpub` / `oreilly` fixtures that previously errored now produce records. The golden diff
      is reviewed.
