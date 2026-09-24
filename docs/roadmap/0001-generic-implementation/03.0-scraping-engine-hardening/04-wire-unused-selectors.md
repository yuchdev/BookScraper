# 04 - Wire unused selectors (tags, fallbacks, expanders)

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/README.md)
**Status:** ⬜ Not started
**Depends on:** [03-missing-field-resilience.md](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/03-missing-field-resilience.md)
**Role:** Scraping Expert

## Context

`parameters.py` defines selectors that `scrape_book()` never reads:

| Key               | Defined for              | Intended use                                                  |
|-------------------|--------------------------|---------------------------------------------------------------|
| `TAGS`            | all four sites           | category / topic tags - the project summary lists tags as a scraped field |
| `DESCRIPTION_ALT` | amazon, oreilly          | fallback when `DESCRIPTION` matches nothing                   |
| `DETAILS_BUTTON`  | amazon                   | expander to click before reading ISBN/date blocks             |
| `READ_MORE_LINK`  | packtpub (`""`), oreilly | already used, but an empty-string value should mean "none"    |

## Requirements

- **Tags:** `book_details["tags"] = _texts(page, TAGS)`: every match, stripped, empty strings dropped,
  de-duplicated **preserving order**, capped at 50. A missing selector or no matches → `[]`.
- **Description fallback:** if `DESCRIPTION` yields `None` or empty and `DESCRIPTION_ALT` is set, use it.
- **Details expander:** if `DETAILS_BUTTON` is set and visible, click it (timeout `OPTIONAL_FIELD_TIMEOUT_MS`)
  **before** the ISBN and publication-date reads. Failure to click → debug log, continue.
- Treat `""` and `None` selector values identically ("not configured") everywhere via one helper
  `_selector(selectors, key) -> Optional[str]`.
- Add a unit sanity test that fails when a *detail-page* key exists in `site_constants` but no code path reads
  it: a static list `CONSUMED_DETAIL_KEYS` in `scrape_details.py`, compared with
  `schema_detection.detail_selector_fields()`. This stops dead config from coming back.

## Files

- Modify `src/bookscraper/scraping/scrape_details.py`.
- Modify `tests/mock/scraping/test_scrape_details.py`, `tests/unit/scraping/test_parameters.py`.

## Tests

- `test_tags_extracted_deduplicated_in_order`
- `test_tags_missing_selector_yields_empty_list`
- `test_description_alt_used_when_primary_empty`
- `test_details_button_clicked_before_isbn_read` (fake page records call order)
- `test_empty_string_selector_treated_as_unconfigured`
- `test_every_detail_selector_key_is_consumed`

## Success criteria

- [ ] 06.0 golden records for Amazon and O'Reilly now carry non-empty `tags`.
- [ ] `test_every_detail_selector_key_is_consumed` passes with no allow-list entries except documented ones
      (`AUTHORS_META`, `PUBLICATION_DATE_META`, `ASIN_DETAIL_PAGE_SELECTOR`, each with a one-line reason).
