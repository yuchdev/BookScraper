# 04 - Markdown snapshot spike

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/README.md)
**Status:** ✅ Complete (measured, not adopted)
**Depends on:** [03-field-extraction-engine.md](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/03-field-extraction-engine.md)
**Role:** Python Expert

## Context

Scrapegraph-ai converts pages to Markdown (`utils/convert_to_md.py`) before prompting, which can shrink prompts
compared with our HTML pruning. This idea fits **value** extraction only. `detect-schema` proposes CSS selectors and
needs the `id`/`class`/attribute structure that Markdown throws away, so it stays on HTML whatever this spike finds.

## Requirements

- A time-boxed measurement, not a feature. Write `scripts/measure_snapshot_formats.py`, which compares two snapshot
  formats on every non-edge-case detail fixture in `tests/fixtures/`:
  - the `ai_common` pruned HTML;
  - a Markdown rendering.

  For each format it reports:
  - characters, and tokens via `count_tokens` (optional `--count-tokens`; needs a key, off by default);
  - whether each eligible field's ground-truth value (from the `.expected.json` goldens) is still present
    verbatim, which is the evidence-check precondition from subtask 03.
- Markdown conversion: try the stdlib-only route first, reusing the `html.parser` approach of
  `scraping/sanitize.py`. A third-party converter (e.g. `markdownify`) may be measured in a throwaway venv only. It
  must not be added to `pyproject.toml` by this subtask.
- **Decision rule**, recorded in status.md *Notes & decisions*. Adopt Markdown for `ai_extraction.py` only if:
  - token reduction is **≥ 30%** on median;
  - **no** ground-truth value is lost on any fixture;
  - any new dependency passes `/dep-audit`.

  Otherwise close the subtask as "measured, not adopted". That is a valid outcome.
- If adopted, the snapshot format is a parameter of the subtask 03 prompt builder, and the evidence check still
  runs against the page's visible text, never against the Markdown.

## Files

- Create `scripts/measure_snapshot_formats.py` (out of coverage scope, like the other `scripts/`).
- Modify `docs/roadmap/0001-generic-implementation/status.md` (result + decision).
- If adopted: modify `src/bookscraper/scraping/ai_extraction.py` and `tests/unit/scraping/test_ai_extraction.py`.

## Tests

- If adopted: `test_markdown_snapshot_preserves_ground_truth_values` (all fixtures, offline).

## Success criteria

- [ ] A measured table (per site: chars, tokens if counted, values preserved) is in status.md with the decision.
