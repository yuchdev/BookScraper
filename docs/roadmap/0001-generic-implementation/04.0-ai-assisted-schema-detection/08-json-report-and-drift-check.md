# 08 - JSON report & drift exit code

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/README.md)
**Status:** ⬜ Not started
**Depends on:** [07-offline-mode-and-golden-tests.md](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/07-offline-mode-and-golden-tests.md)
**Role:** Python Expert

## Requirements

- `--format {text,json}` (default `text`). JSON schema:
  ```json
  {"site": "amazon", "page_type": "detail", "model": "...", "samples": ["..."],
   "fields": [{"name": "ISBN13", "current": "...", "proposed": "...", "changed": true,
               "rationale": "...", "matches": {"<sample>": 1}, "consistent": true,
               "current_matches": {"<sample>": 0}}],
   "summary": {"changed": 2, "inconsistent": 1, "current_broken": 1}}
  ```
  `current_matches` is new: it re-queries the **current** selector too, so the report shows which existing
  selectors have already broken.
- `--check-current-only`: skip the AI call entirely and only evaluate current selectors against the samples. It is
  cheap, keyless, and the basis for a scheduled drift job (task 06.2 subtask 05).
- Exit codes: `0` everything consistent and no current selector broken; `4` at least one current selector
  matches on no sample (**drift**); `1` any error. Document them in `--help` and in the docs.

## Files

- Modify `src/bookscraper/scraping/schema_detection.py`, `src/bookscraper/cli.py`,
  `src/bookscraper/commands/detect_schema.py`, `docs/scraping/schema-detection.md`.
- Tests: `tests/unit/scraping/test_schema_detection.py`, `tests/mock/commands/test_detect_schema.py`,
  `tests/unit/test_cli.py`.

## Tests

- `test_json_report_matches_schema`
- `test_current_matches_reported_for_each_sample`
- `test_check_current_only_makes_no_api_call`
- `test_exit_code_four_on_broken_current_selector`
- `test_exit_code_zero_when_all_current_selectors_match`

## Success criteria

- [ ] `detect-schema --check-current-only --html-file …` can gate CI.
