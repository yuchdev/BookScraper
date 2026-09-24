# 03 - Cross-sample DOM validation

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/README.md)
**Status:** ✅ Complete
**Role:** Scraping Expert

## Requirements

- `fetch_pruned_html(url, page, max_chars)`: navigates with `route_handler` blocking and returns the pruned
  `page.content()`. Timeout / load error → `SchemaDetectionError`.
- `validate_across_samples(proposals, pages)`: for every proposal, counts matches on **each** sample page. A
  selector that matches on some pages but not others is flagged *inconsistent*. A `null` selector is a miss. An
  invalid selector counts as a miss without aborting the run.

## Tests

`tests/mock/scraping/test_schema_detection.py`: `test_fetch_pruned_html_returns_pruned_snapshot`,
`test_fetch_pruned_html_raises_on_timeout`, `test_fetch_pruned_html_raises_on_generic_load_error`,
`test_validate_across_samples_consistent_match`, `test_validate_across_samples_flags_inconsistency`,
`test_validate_across_samples_null_selector_is_miss`, `test_validate_across_samples_bad_selector_does_not_abort`.

## Success criteria

- [x] The human judgement step from the manual loop ("does it match on more than one page?") is automated, not
      skipped.
