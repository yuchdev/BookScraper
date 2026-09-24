# 07 - Offline mode & golden tests

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/README.md)
**Status:** ⬜ Not started
**Depends on:** [Task 06.0](/docs/roadmap/0001-generic-implementation/06.0-real-html-mock-test-corpus/README.md)
**Role:** Testing Expert → Python Expert

## Context

Today every test fakes the page DOM with `FakeLocator` matches written by hand. They prove the orchestration, not
that the pruner keeps the elements a real Amazon page needs, or that the validator's match counting works on real
markup.

## Requirements

- New repeatable flag `--html-file PATH` (`action="append"`, `dest="html_file"`), mutually exclusive with `--url`
  (argparse group; one of the two is required). Each file is loaded into a real headless Chromium page with
  `page.set_content()` (no network, `route_handler` blocks everything else), so validation runs against a genuine
  DOM.
- New optional `--replay PATH`: a JSON file of recorded AI responses keyed by `sha256(prompt)`. When given, no API
  call is made and `ANTHROPIC_API_KEY` is not required. A prompt missing from the file → `SchemaDetectionError`
  ("no recorded response"). A matching `--record PATH` writes responses after a live run, for maintainers
  refreshing the recordings.
- Golden tests (under the Chromium marker from task 06.0 subtask 03): for each site with ≥ 2 detail fixtures, run
  `detect_schema(site, html_files=…, replay=…)` and compare the report text with
  `tests/fixtures/schema_detection/<site>.report.txt` (normalizing the timestamp).
- `prune_html` regression check: for every detail fixture, every *current* `site_constants` selector that matches
  the raw fixture must still match the pruned snapshot, loaded back through `set_content`. That catches a pruner
  that strips load-bearing markup.

## Files

- Modify `src/bookscraper/scraping/schema_detection.py`, `src/bookscraper/cli.py`,
  `src/bookscraper/commands/detect_schema.py`.
- Create `tests/fixtures/schema_detection/{site}.replay.json`, `{site}.report.txt`.
- Create `tests/mock/scraping/html/test_schema_detection_goldens.py`.
- Modify `tests/unit/test_cli.py`, `docs/scraping/schema-detection.md`.

## Tests

- `test_html_file_and_url_are_mutually_exclusive`
- `test_replay_mode_requires_no_api_key`
- `test_replay_missing_prompt_fails_loudly`
- `test_record_mode_writes_prompt_hash_keyed_responses`
- `test_golden_report_matches_for_<site>` (parametrized over sites with fixtures)
- `test_pruning_preserves_every_currently_matching_selector` (parametrized over fixtures)

## Success criteria

- [ ] The whole `detect-schema` pipeline runs in CI with no network and no API key.
