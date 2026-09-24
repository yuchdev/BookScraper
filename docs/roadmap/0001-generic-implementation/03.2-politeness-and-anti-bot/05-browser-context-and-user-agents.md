# 05 - Browser context & user-agent hygiene

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/README.md)
**Status:** ⬜ Not started
**Role:** Scraping Expert

## Context

- `scrape_book()` sets the UA with `page.set_extra_http_headers({"User-Agent": ua})`. That changes the header
  but not `navigator.userAgent`, the client hints, or the `Sec-CH-UA` headers, so the page sees two contradictory
  identities.
- `USER_AGENTS` lists Chrome 91-95 / Firefox 89-93 strings from 2021. A current Chromium advertising Chrome 91 is
  itself anomalous.
- Pages are created on the shared default context, so cookies leak across concurrent scrapes.

## Requirements

- Create one `BrowserContext` per scrape attempt:
  `browser.new_context(user_agent=ua, locale="en-US", viewport={"width": 1366, "height": 900})`, and close it in
  the same `finally` as the page (extends task 03.0 subtask 01). Remove `set_extra_http_headers({"User-Agent": ...})`.
- Replace the hard-coded list with `USER_AGENT_MODE = "native"` (default) or `"rotate"`:
  - `"native"`: don't override the UA at all. Playwright's bundled Chromium UA is internally consistent. This is
    the honest default.
  - `"rotate"`: pick from a refreshed `USER_AGENTS` list whose entries match the **installed** Chromium major
    version (derived at runtime from `browser.version`), so the header and the engine agree.
- `schema_detection.fetch_pruned_html` uses the same context factory, so its snapshots match what the scraper sees.
- Put the factory in `scraping/browser.py`: `async def new_scrape_context(browser) -> BrowserContext`.

## Files

- Create `src/bookscraper/scraping/browser.py`.
- Modify `src/bookscraper/scraping/parameters.py`, `scrape_details.py`, `search_utils.py`, `schema_detection.py`.
- Extend `tests/mock/scraping/playwright_fakes.py` with `FakeBrowser.new_context` / `FakeContext`.
- Tests: `tests/mock/scraping/test_browser.py`, updates to existing scraping mock tests.

## Tests

- `test_native_mode_does_not_override_user_agent`
- `test_rotate_mode_uses_ua_matching_installed_chromium_major`
- `test_context_closed_with_page_on_every_path`
- `test_no_set_extra_http_headers_user_agent_calls`
- `test_schema_detection_uses_shared_context_factory`

## Success criteria

- [ ] `grep -n '"User-Agent"' src/bookscraper/scraping/` returns nothing.
