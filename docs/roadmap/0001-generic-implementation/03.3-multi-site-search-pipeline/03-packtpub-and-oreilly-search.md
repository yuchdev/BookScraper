# 03 - Packtpub & O'Reilly search providers

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-search-provider-registry.md](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/01-search-provider-registry.md)
**Role:** Scraping Expert

## Context

The `SEARCH_*` selectors for Packtpub and O'Reilly carry comments like `# Or similar` and `# Example`. They were
never validated. Both sites render search results client-side, and may expose a JSON endpoint that is cheaper and
more stable than DOM scraping.

## Requirements

1. **Investigate first** (time-boxed, 1 day per site), recorded in `docs/scraping/search-sources.md`:
   - Does the search page call a public JSON endpoint (visible in the Playwright network log)? Record the URL
     pattern, parameters, pagination, and whether `robots.txt` allows it.
   - If yes → implement an httpx provider like Leanpub (`needs_browser=False`).
   - If no → Playwright provider. Validate every `SEARCH_*` selector with `detect-schema` (requires task 04.0
     subtask 06's search-page support) against at least two query result pages.
2. `PacktpubProvider` / `OreillyProvider` emitting `SearchCandidate`s with title, authors, detail URL, rating
   where available, and ISBN-13 when the listing exposes it (O'Reilly URLs embed it:
   `/library/view/<slug>/<isbn13>/`).
3. `already_stored`: no site-native ID exists, so check by `url` via a new contract method
   `StorageBackend.book_exists_by_url(url) -> bool`, implemented in both backends, with a sparse index in Mongo
   (extends task 01.0 subtask 06) and parity scenarios (extends task 01.0 subtask 08).
4. Update `parameters.py` with the verified selectors or endpoints and delete the speculative comments.

## Files

- Create `docs/scraping/search-sources.md`.
- Modify `src/bookscraper/scraping/search_providers.py`, `search_utils.py`, `parameters.py`,
  `src/bookscraper/backends/storage.py`, `backends/mongo/database.py`, `backends/local/store.py`.
- Fixtures: `tests/fixtures/{html,json}/{packtpub,oreilly}/search_*.{html,json}`.
- Tests: `tests/mock/scraping/test_search_providers.py`, `tests/mock/backends/test_backend_parity.py`.

## Tests

- `test_packtpub_search_parses_fixture_page`
- `test_oreilly_search_parses_fixture_page`
- `test_oreilly_candidate_extracts_isbn13_from_url`
- `test_pagination_stops_at_last_page` (per provider)
- `test_book_exists_by_url_parity` (both backends)

## Success criteria

- [ ] `docs/scraping/search-sources.md` documents the chosen source for each site, with evidence.
- [ ] `SITES_TO_SCRAPE` default becomes `["leanpub", "amazon", "packtpub", "oreilly"]`.
