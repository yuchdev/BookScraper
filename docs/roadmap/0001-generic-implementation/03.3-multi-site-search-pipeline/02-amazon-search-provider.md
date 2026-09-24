# 02 - Amazon search provider

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-search-provider-registry.md](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/01-search-provider-registry.md)
**Role:** Scraping Expert

## Requirements

- `AmazonPlaywrightProvider` wrapping `get_search_results_via_playwright()`, refactored to:
  - Paginate `1..max_pages` **inclusive** (fixes the `range(1, SEARCH_MAXIMUM_PAGES)` off-by-one). Stop early on
    the no-results selector, on a missing or disabled next button, or on a bot wall (task 03.2 subtask 04).
  - Emit `SearchCandidate`s with `source_id` = ASIN (`data-asin`), the unwrapped detail URL for sponsored
    `/sspa/click?...url=` links (existing logic, now unit-tested), and `rating` / `rating_count` parsed from the
    card when present.
  - `already_stored` → `backend.book_exists_by_asin(asin)`.
- Clamp `max_pages` to `site_constants["amazon"]["SEARCH_MAXIMUM_PAGES"]`, raised to `75` to reflect the
  documented Amazon limit, and log when clamping happens.
- Candidates without an ASIN are kept (URL-deduped later) but logged at debug level.

## Files

- Modify `src/bookscraper/scraping/search_utils.py`, `src/bookscraper/scraping/search_providers.py`,
  `src/bookscraper/scraping/parameters.py`.
- Fixtures (task 06.0): `tests/fixtures/html/amazon/search_python_p1.html`, `…_p2.html`,
  `…_last_page.html`, `…_no_results.html`, `…_sponsored.html`.
- Tests: `tests/mock/scraping/test_search_utils.py` (fixture-driven), `tests/mock/scraping/test_search_providers.py`.

## Tests

- `test_amazon_search_fetches_exactly_max_pages`
- `test_amazon_search_stops_on_disabled_next_button`
- `test_amazon_search_no_results_page_returns_empty`
- `test_amazon_sponsored_link_unwrapped_to_dp_url`
- `test_amazon_candidate_carries_asin_and_rating`
- `test_amazon_already_stored_uses_asin_lookup`
- `test_amazon_max_pages_clamped_with_log`

## Success criteria

- [ ] `search --site amazon --query Python --max-search-pages 2 --store-backend json --dry-run` lists
      candidates from exactly two pages (manual live smoke run, output excerpt in the PR).
