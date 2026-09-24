# 01 - HTML pruning & prompt construction

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/README.md)
**Status:** ✅ Complete
**Role:** Python Expert

## Requirements

- `DEFAULT_HTML_BUDGET = 60000`; `_NON_SELECTOR_KEYS = {"BASE_URL", "404_PAGE_TITLE"}`.
- `detail_selector_fields(entry) -> list[str]`: drops `SEARCH_*`, keys ending `_URL`/`_API`, and the sentinels.
  Keeps `*_META` and `None`-valued keys (the point may be to discover a selector for them).
- `prune_html(html, max_chars)`: strips comments and the contents of `script`/`style`/`svg`/`noscript`, collapses
  whitespace, truncates. Pure string work.
- `build_prompt(site, field_names, pruned_html)`: asks for one JSON object keyed by exactly the given field names,
  each value `{"selector": str|null, "rationale": str}`, with no prose and no fences.

## Tests

`tests/unit/scraping/test_schema_detection.py`: `test_detail_selector_fields_excludes_search_url_api_and_sentinels`,
`test_detail_selector_fields_on_real_leanpub_entry`, `test_prune_html_removes_script_style_svg_and_comments`,
`test_prune_html_collapses_whitespace`, `test_prune_html_truncates_to_budget`, `test_prune_html_empty_input`,
`test_build_prompt_includes_site_fields_and_html`.

## Success criteria

- [x] Pruning and prompt building are pure and unit-tested without mocks.
