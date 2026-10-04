# 02 - Shared AI prompt infrastructure

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-adr-and-threat-model.md](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/01-adr-and-threat-model.md)
**Role:** Python Expert

## Context

`scraping/schema_detection.py` (~1,570 lines) holds the hardened pieces the fallback needs:
- `prune_html` and the budget re-prune loop;
- `build_snapshot_block` plus the `<page_html>` fence escaping (AIS-1, PI-2);
- `sanitize_untrusted` (AIS-13, PI-3);
- model resolution (`--model` > `ANTHROPIC_MODEL` > default);
- client construction with `max_retries=1`, the `count_tokens` pre-flight, and stop-reason checks;
- `load_replay_file` and `sha256(prompt)` record/replay.

Copying them would create two injection defenses that can drift apart.

## Requirements

- Extract the shared pieces, without behavior changes, into `src/bookscraper/scraping/ai_common.py`:
  - pruning;
  - the snapshot fence and escaping;
  - `sanitize_untrusted`;
  - model and key resolution;
  - client factory and token pre-flight;
  - the usage accumulator and usage-line formatter;
  - replay/record I/O.

  `schema_detection.py` re-imports them, and its public names stay importable from their current locations, so
  existing tests and patch targets keep working.
- Generalize the error type: `ai_common.AIRequestError`, with `SchemaDetectionError` as a subclass, so 04.0's
  "fail loudly" contract and messages are unchanged.
- `ai_common.py` imports only from `scraping/` + `book_utils.py`, like `schema_detection.py`.
- **Pure refactor:**
  - all 04.0 goldens (`tests/fixtures/schema_detection/*.report.txt`) and the replay keys are byte-identical;
  - `scripts/generate_schema_replays.py` regenerates the same keys.

## Files

- Create `src/bookscraper/scraping/ai_common.py`.
- Modify `src/bookscraper/scraping/schema_detection.py`.
- Create `tests/unit/scraping/test_ai_common.py`. Move the pure-logic tests whose subject moved; don't duplicate
  them.

## Tests

- all existing `tests/unit/scraping/test_schema_detection.py`, `tests/mock/scraping/test_schema_detection.py` and
  `tests/mock/scraping/html/test_schema_detection_goldens.py` tests, passing unchanged
- `test_schema_detection_error_is_ai_request_error`
- `test_ai_common_imports_only_scraping_and_book_utils` (AST import guard)

## Success criteria

- [ ] `git diff` of `tests/fixtures/schema_detection/` is empty after `pytest --update-goldens`.
- [ ] `schema_detection.py` no longer defines any of the moved helpers.
