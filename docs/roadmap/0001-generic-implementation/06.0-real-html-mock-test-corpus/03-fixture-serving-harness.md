# 03 - Chromium fixture-serving harness

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/README.md)
**Status:** ⬜ Not started
**Depends on:** [02-corpus-layout-and-policy.md](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/02-corpus-layout-and-policy.md)
**Role:** Testing Expert

## Requirements

- New test package `tests/mock/scraping/html/` (it counts toward coverage, since it's still offline mock-tier)
  with a `conftest.py` providing:
  - `chromium_browser` (session-scoped, async): launches real headless Chromium once per session. If the browser
    isn't installed, the whole package **skips** with the hint `uv run playwright install chromium`, not a failure.
  - `corpus_router(browser, mapping: dict[str, Path])`: wraps `browser.new_page` / `new_context`, so every page
    created during the test installs a `route("**/*")` that
    - fulfills a request whose URL (normalized: scheme+host+path, query ignored unless the mapping key has one)
      is in `mapping`, using the fixture's bytes with `content-type: text/html; charset=utf-8`;
    - fulfills `/robots.txt` from `tests/fixtures/robots/<site>.txt` if present;
    - **aborts everything else** and records it. At teardown, any recorded unexpected request fails the test with
      the URL list.
  - `httpx_corpus(mapping)`: an `httpx.MockTransport` serving JSON fixtures, patched into `httpx.AsyncClient` for
    the Leanpub paths. Same abort-and-fail rule.
- Register a pytest marker `chromium` in `pyproject.toml`. It is **included** in the default run (the tests are
  offline and fast). CI installs Chromium (task 06.2 subtask 03).
- Production code must run **unmodified**. The harness intercepts at the Playwright and httpx boundaries only.
  `route_handler`'s `route.continue_()` must still be honored for documents: install the corpus route **after**
  the production `page.route("**/*", route_handler)`, so it takes precedence (Playwright runs the most recently
  added matching route first), and verify this in a test.

## Files

- Create `tests/mock/scraping/html/__init__.py`, `tests/mock/scraping/html/conftest.py`.
- Modify `pyproject.toml` (marker).
- Create `tests/mock/scraping/html/test_harness.py`.

## Tests

- `test_harness_serves_mapped_url`
- `test_harness_fails_test_on_unmapped_request`
- `test_harness_takes_precedence_over_production_route_handler`
- `test_harness_skips_cleanly_without_chromium` (simulate a missing executable)
- `test_httpx_corpus_serves_json_and_rejects_unknown`

## Success criteria

- [ ] One session-scoped browser launch for the whole package (measured: package runtime < 30 s locally).
