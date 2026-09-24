# 05 - Search-page & API JSON fixtures

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-capture-and-sanitize-script.md](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/01-capture-and-sanitize-script.md)
**Role:** Testing Expert

## Requirements

- Capture:
  - Amazon search: `search_python_p1`, `search_python_p2`, `search_python_last_page` (next button disabled),
    `search_no_results`, and one page containing a sponsored `/sspa/click` card (or the sponsored card extracted
    into `search_sponsored`).
  - Leanpub search API: `search_python_p1.json` (a full 100-item page, trimmed to ≤ 20 items **with the `page_size`
    logic preserved** by also capturing `search_python_last.json` holding < 100 items), `search_empty.json`.
  - `robots/<site>.txt` for all four sites.
- Golden tests in `tests/mock/scraping/html/test_search_goldens.py`:
  - `get_search_results_via_playwright(browser, "amazon", "python")` against the served pages. Expected candidate
    lists are stored as `search_python.expected.json`.
  - `get_leanpub_search_results_via_api("python")` with `httpx_corpus`, including pagination termination and the
    `SimpleAuthor` lookup.
- These tests cover the currently uncovered `search_utils.py` ranges (lines ~246-408: pagination, sponsored links,
  no-results).

## Files

- Create fixtures under `tests/fixtures/html/amazon/`, `tests/fixtures/json/leanpub/`, `tests/fixtures/robots/`.
- Create `tests/mock/scraping/html/test_search_goldens.py`.

## Tests

- `test_amazon_search_golden`
- `test_amazon_search_stops_on_last_page`
- `test_amazon_search_no_results_returns_empty`
- `test_amazon_sponsored_card_unwrapped`
- `test_leanpub_search_api_golden_paginates_until_short_page`
- `test_leanpub_search_api_empty_result`

## Success criteria

- [ ] `search_utils.py` line coverage ≥ 90%.
