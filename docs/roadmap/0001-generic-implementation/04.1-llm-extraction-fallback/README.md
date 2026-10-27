# Task 04.1 - LLM Extraction Fallback

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** ⬜ Not started
**Category:** feature | **Priority:** P2
**Origin:** "What is worth borrowing" in the
[Scrapegraph-ai evaluation](/docs/reviews/2026-10-04-scrapegraph-ai-evaluation.md). We borrow the ideas only, not
the dependency.

## Scope

Today a drifted selector costs us data until a human runs `detect-schema` and applies a fix by hand. The drift
check (04.0/08) also misses some cases: the live Amazon `DESCRIPTION` matches an *empty* element, so it reports
`ok` while the scraper gets nothing. This task adds a narrow, **opt-in** safety net:

1. After the deterministic selectors have run, if an **eligible** field is still empty, extract only that field
   with Claude from the already-loaded page.
2. Validate the answer deterministically. The evidence must appear in the page text, and the value must pass the
   03.1 normalizers. Anything that fails is discarded, never stored.
3. Mark every record that used the fallback (`ai_extracted_fields`), so AI-sourced values can always be told apart
   and re-scraped once the selector is fixed.

It reuses the hardened 04.0 stack instead of building a parallel one: the pruner, the `<page_html>` fence plus
untrusted-data framing, `sanitize_untrusted()`, the token budget, model resolution, and replay/record.

The second borrowed idea, **Markdown conversion before prompting**, is a measured spike (subtask 04). It applies
only to this value-extraction prompt. `detect-schema` must keep sending HTML, because it proposes CSS selectors and
needs the `id`/`class`/attribute structure that Markdown discards.

## Out of scope

- Whole-page LLM extraction ("prompt instead of selectors"). Selectors stay primary, and the fallback fills single
  gaps only.
- Auto-repairing selectors. The fallback never writes `parameters.py`; ADR 0002's propose-only rule still holds.
- Proxy rotation, stealth browsers, and any other Scrapegraph-ai feature the evaluation rejected.

## Subtasks

| #  | Document                                                                 | Status         | Blocks |
|----|--------------------------------------------------------------------------|----------------|--------|
| 01 | [ADR & threat model](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/01-adr-and-threat-model.md) | ✅ Complete | 02-05 |
| 02 | [Shared AI prompt infrastructure](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/02-shared-ai-prompt-infrastructure.md) | ✅ Complete | 03 |
| 03 | [Field extraction engine](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/03-field-extraction-engine.md) | ✅ Complete | 05 |
| 04 | [Markdown snapshot spike](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/04-markdown-snapshot-spike.md) | ✅ Complete (measured, not adopted) | -  |
| 05 | [Scraper integration & provenance](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/05-scraper-integration-and-provenance.md) | ✅ Complete (summary drops aggregate, not by reason) | - |

## Dependencies

- **03.0** (subtasks 02-04): `ScrapeResult`, the `_text()` helper and the `DESCRIPTION_ALT` fallback define when a
  field is truly "empty after the deterministic path".
- **03.1** (subtasks 01, 02-04): `BookRecord` carries `ai_extracted_fields`, and the ISBN/date normalizers are the
  value validators.
- **04.0**: the prompt/replay/budget stack being reused.
- **05.0** (subtask 01): the shared scrape-runtime parent parser that hosts the new flags.

## Key constraints

- **Off by default.** No run makes an Anthropic call unless `--ai-fallback` is passed. Without it, behavior and
  goldens are byte-identical, and no `ANTHROPIC_API_KEY` is read.
- **Deterministic validation gates every value.** No value is stored unless its evidence is found in the page text
  and it passes the 03.1 normalizer for its field.
- **Provenance is mandatory.** Every AI-filled field is listed in the record's `ai_extracted_fields`.
- **Hard cost ceilings.** One request per page at most (all missing eligible fields batched), a per-run page cap,
  one retry, and the 04.0 pre-flight token budget.
- **Tests never call the real API.** Offline replays only, as in 04.0/07.
- Implementers load the `claude-api` skill before touching model IDs, token counting, or caching.
