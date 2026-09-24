# 01 - Search provider registry

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/README.md)
**Status:** ⬜ Not started
**Role:** Architect → Python Expert

## Requirements

- `scraping/search_providers.py`:

  ```python
  @dataclass(frozen=True)
  class SearchCandidate:
      site: str
      title: str
      url: str                         # detail URL (human-facing)
      detail_ref: str                  # what the detail scraper needs (URL, or Leanpub API URL)
      authors: list[str]
      source_id: Optional[str] = None  # ASIN / Leanpub book_id
      slug: Optional[str] = None
      rating: Optional[float] = None
      rating_count: Optional[int] = None
      query: str = ""
      page: int = 1

  class SearchProvider(Protocol):
      site: str
      needs_browser: bool
      async def search(self, query: str, max_pages: int, ctx: "SearchContext") -> list[SearchCandidate]: ...
      def already_stored(self, c: SearchCandidate, backend: StorageBackend) -> bool: ...

  PROVIDERS: dict[str, SearchProvider]
  ```

  `SearchContext` bundles the shared httpx client, the lazily launched Playwright browser (launched only if some
  selected provider has `needs_browser=True`), the rate limiters, and the robots cache.
- Port Leanpub into `LeanpubApiProvider` (wraps `get_leanpub_search_results_via_api`; `already_stored` →
  `backend.leanpub_book_exists`). Behavior must be identical, as proven by the existing
  `tests/mock/commands/test_search.py` cases passing.
- `commands/search.run()` becomes a site-agnostic loop over `PROVIDERS[s] for s in sites`. Unknown site keys
  → an argparse-level error (task 05.0's `--site` uses `choices=sorted(PROVIDERS)`).
- `max_pages` comes from `args.max_search_pages` (finally wiring the flag).

## Files

- Create `src/bookscraper/scraping/search_providers.py`.
- Modify `src/bookscraper/commands/search.py`, `src/bookscraper/scraping/search_utils.py`.
- Create `tests/mock/scraping/test_search_providers.py`; modify `tests/mock/commands/test_search.py`.

## Tests

- `test_registry_contains_every_site_in_site_constants`
- `test_leanpub_provider_matches_legacy_behavior`
- `test_browser_not_launched_when_only_api_providers_selected`
- `test_max_search_pages_flag_reaches_provider`
- `test_search_run_has_no_site_specific_branches` (AST: no string literal `"leanpub"` in `commands/search.py`)

## Success criteria

- [ ] `commands/search.py` contains no per-site `if`.
