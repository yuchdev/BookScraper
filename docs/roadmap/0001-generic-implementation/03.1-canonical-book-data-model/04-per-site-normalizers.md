# 04 - Per-site normalizers

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/README.md)
**Status:** ⬜ Not started
**Depends on:** [02-isbn-normalization.md](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/02-isbn-normalization.md), [03-date-normalization.md](/docs/roadmap/0001-generic-implementation/03.1-canonical-book-data-model/03-date-normalization.md)
**Role:** Python Expert

## Requirements

- New module `src/bookscraper/scraping/normalize.py`:
  - `from_playwright(raw: dict, site: str, url: str) -> BookRecord` - maps `scrape_book`'s raw dict.
  - `from_leanpub_api(attributes: dict, book_id: str, authors: list[str], categories: list[str]) -> BookRecord`.
  - Shared private helpers: `_clean_authors` (strip, drop empty, de-duplicate case-insensitively, keep first
    spelling), `_clean_tags` (same, cap 50), `_now_utc_iso()` (injectable clock for tests).
- **Hash inputs:** `hash_book(record.title, record.authors, record.publication_year)`. That's identical to today
  for Playwright sites. For Leanpub, `publication_year` comes from `last_published_at`, exactly as today's hash
  input, so hashes are unchanged. Add a test that pins three known historical hashes.
- `scrape_book()` returns `ScrapeResult(book=record.to_document())`; `get_leanpub_book_details()` likewise.
  Nothing else in `scraping/` builds book dicts by hand.
- Leanpub `url` = `f"{BASE_URL}/{slug}"`. The API URL is no longer stored.
- `site` for Leanpub becomes `"leanpub"` (was `"leanpub.com"`). Subtask 06 migrates old documents.

## Files

- Create `src/bookscraper/scraping/normalize.py`.
- Modify `src/bookscraper/scraping/scrape_details.py`.
- Create `tests/unit/scraping/test_normalize.py`; update the 06.0 golden files (reviewed diff).

## Tests

- `test_from_playwright_maps_all_fields`
- `test_from_playwright_na_isbn_becomes_none`
- `test_from_leanpub_maps_about_the_book_to_description_and_categories_to_tags`
- `test_leanpub_site_is_bare_key_and_url_is_human_facing`
- `test_authors_deduplicated_case_insensitively`
- `test_hash_unchanged_for_known_historical_records` (three pinned `(title, authors, year) → hash` triples)

## Success criteria

- [ ] Every document produced by any scraper passes `BookRecord.from_document()`.
