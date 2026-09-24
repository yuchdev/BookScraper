# 04 - Apply SCRAPE_FILTERS

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/README.md)
**Status:** ⬜ Not started
**Depends on:** [02-amazon-search-provider.md](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/02-amazon-search-provider.md), [03-packtpub-and-oreilly-search.md](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/03-packtpub-and-oreilly-search.md)
**Role:** Python Expert

## Context

```python
SCRAPE_FILTERS = {
    "enable_rating_filter": True,
    "min_rating": 3.5,
    "include_new_books_without_rating": True,
    "min_isbns_required": 1,
}
```

This is dead config today: grep finds no reader.

## Requirements

- `scraping/filters.py` (pure):
  - `passes_search_filters(c: SearchCandidate, filters: dict) -> tuple[bool, Optional[str]]`, applied **before**
    the detail scrape. Rating rule: if enabled and `c.rating is not None` and `c.rating < min_rating` → reject
    `"rating 3.1 < 3.5"`. `c.rating is None` → keep iff `include_new_books_without_rating`.
  - `passes_detail_filters(doc: dict, filters: dict) -> tuple[bool, Optional[str]]`, applied **after** the detail
    scrape. `min_isbns_required`: count non-null `isbn10`/`isbn13`, reject below the threshold. Leanpub books
    rarely have ISBNs, so add per-site overrides:
    `SCRAPE_FILTERS["per_site"] = {"leanpub": {"min_isbns_required": 0}}`.
- Validate `SCRAPE_FILTERS` shape at import time in the unit sanity test (unknown keys → test failure).
- Filtered-out books are counted per reason in the run summary and written to `filtered_books.csv` (new,
  gitignored diagnostic). They are never saved to the store.
- CLI `--no-filters` (task 05.0) disables both stages.

## Files

- Create `src/bookscraper/scraping/filters.py`.
- Modify `src/bookscraper/scraping/parameters.py`, `src/bookscraper/commands/search.py`, `.gitignore`.
- Create `tests/unit/scraping/test_filters.py`; modify `tests/unit/scraping/test_parameters.py`,
  `tests/mock/commands/test_search.py`.

## Tests

- `test_rating_below_threshold_rejected_with_reason`
- `test_unrated_kept_when_include_new_books_true`
- `test_unrated_rejected_when_include_new_books_false`
- `test_rating_filter_disabled_keeps_everything`
- `test_min_isbns_required_counts_non_null_isbns`
- `test_per_site_override_applies_to_leanpub`
- `test_filtered_books_written_to_csv_and_not_saved`
- `test_scrape_filters_shape_is_valid`

## Success criteria

- [ ] Every key in `SCRAPE_FILTERS` has at least one reader and one test.
