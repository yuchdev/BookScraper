# 06 - Search-page selector support

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/README.md)
**Status:** ⬜ Not started
**Role:** Scraping Expert

## Context

`detail_selector_fields()` explicitly excludes every `SEARCH_*` key, but the search selectors are the least verified
part of `parameters.py`. Task 03.3 subtask 03 needs a tool to validate Packtpub and O'Reilly search selectors.

## Requirements

- New flag `--page-type {detail,search}` (default `detail`, `dest="page_type"`).
- `search_selector_fields(entry) -> list[str]`: `SEARCH_*` keys minus URL/API/numeric/text constants
  (`SEARCH_BASE_URL`, `SEARCH_BASE_API`, `SEARCH_MAXIMUM_PAGES`, `SEARCH_ASIN_ATTRIBUTE`, `SEARCH_NO_RESULTS_TEXT`
  stays because it is a selector).
- A search-page prompt variant explains the **card-relative** semantics: `SEARCH_BOOK_CARD` /
  `SEARCH_RESULT_ITEM_SELECTOR` is page-level, and the other `SEARCH_*` fields are evaluated **inside each card**.
- Cross-sample validation for search pages also reports, per sample, the card count and the fraction of cards on
  which each card-relative selector matches. Below 80% → flagged *partial*.
- The report format and the propose-only rule are unchanged.

## Files

- Modify `src/bookscraper/scraping/schema_detection.py`, `src/bookscraper/cli.py`,
  `src/bookscraper/commands/detect_schema.py`, `docs/scraping/schema-detection.md`.
- Tests: `tests/unit/scraping/test_schema_detection.py`, `tests/mock/scraping/test_schema_detection.py`,
  `tests/unit/test_cli.py`.

## Tests

- `test_search_selector_fields_selects_card_and_card_relative_keys`
- `test_search_prompt_explains_card_relative_semantics`
- `test_search_validation_reports_card_count_and_match_fraction`
- `test_card_relative_selector_below_80_percent_flagged_partial`
- `test_cli_page_type_defaults_to_detail`

## Success criteria

- [ ] `detect-schema --site amazon --page-type search --url <search url>` produces a report against a 06.0 fixture
      in offline mode (subtask 07).
