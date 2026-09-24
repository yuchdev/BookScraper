# 05 - Model default & token budget

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/README.md)
**Status:** ⬜ Not started
**Role:** Python Expert

## Context

- `DEFAULT_MODEL = "claude-sonnet-4-5"` is pinned to a previous generation.
- Each sample URL triggers a separate, full-price request carrying the same ~1 KB instruction block, and nothing
  bounds input size beyond the 60 000-character snapshot budget (≈15-20k tokens per page).
- There is no visibility into what a run cost.

## Requirements

- **Load the `claude-api` skill first** and take the current model ID, token-counting API, and prompt-caching
  syntax from it, not from memory.
- Update `DEFAULT_MODEL` to the current Sonnet-tier model ID from the skill (at the time of writing,
  `claude-sonnet-5`). Keep the `ANTHROPIC_MODEL` override, and add a `--model` CLI flag (`dest="model"`), with
  precedence flag > env > default.
- **Pre-flight token budget:** call the SDK's token-counting endpoint on the built prompt. If it exceeds
  `MAX_INPUT_TOKENS = 40_000` (a module constant, overridable with `--max-input-tokens`), re-prune with a smaller
  `max_chars`, down to `10_000` characters. If it still doesn't fit → `SchemaDetectionError`. Don't send it.
- **Prompt caching:** split the prompt into a static instruction block (site, field list, output contract), marked
  cacheable, and the per-page snapshot, so multi-URL runs reuse the prefix.
- **Usage reporting:** after the run, print one line: model, input / cached / output tokens (from each response's
  `usage`), and the number of requests. No price table: prices change, and a stale figure would be misleading.
- Model-ID validation: reject an empty string. Otherwise pass the ID through and let the API reject an unknown one
  (surfaced as `SchemaDetectionError` with the SDK's error type, but not the body).

## Files

- Modify `src/bookscraper/scraping/schema_detection.py`, `src/bookscraper/cli.py`,
  `src/bookscraper/commands/detect_schema.py`.
- Modify `docs/scraping/schema-detection.md` (model, budget, caching, usage line).
- Tests: `tests/unit/scraping/test_schema_detection.py`, `tests/mock/scraping/test_schema_detection.py`,
  `tests/unit/test_cli.py`.

## Tests

- `test_model_precedence_flag_env_default`
- `test_over_budget_prompt_is_repruned_then_sent`
- `test_still_over_budget_after_min_prune_raises_without_sending`
- `test_instruction_block_marked_cacheable_and_snapshot_not`
- `test_usage_line_sums_tokens_across_requests`
- `test_empty_model_id_rejected`

## Success criteria

- [ ] A three-URL run makes three requests, and requests 2-3 report cached input tokens > 0 (mocked `usage`).
- [ ] `grep -n "claude-sonnet-4-5" src/` returns nothing.
