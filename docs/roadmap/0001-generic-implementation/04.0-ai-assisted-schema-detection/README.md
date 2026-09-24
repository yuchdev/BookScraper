# Task 04.0 - AI-Assisted Schema Detection

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** 🔶 In progress
**Category:** feature | **Priority:** P1
**Design:** [ADR 0002](/docs/adr/0002-ai-assisted-schema-detection.md) · [docs/scraping/schema-detection.md](/docs/scraping/schema-detection.md)

## Scope

`bookscraper detect-schema --site <site> --url <u> [--url <u2> …] [--output f]` automates the manual
selector-maintenance loop:
1. Load each sample detail page with Playwright (reusing `scrape_details.route_handler`).
2. Prune each page to an LLM-sized snapshot.
3. Ask Claude through the `anthropic` SDK for a selector plus rationale per existing `site_constants[site]`
   detail field.
4. Re-query every proposal against **each** sample page's live DOM, flagging inconsistent matches.
5. Print a diff-style report.

It proposes and never writes `parameters.py`, and it fails loudly with `SchemaDetectionError`.

Subtasks 01-04 record the delivered pipeline. Subtasks 05-09 make it cheaper, testable offline, usable for search
pages, CI-friendly, and safe against hostile page content.

## Subtasks

| #  | Document                                                                 | Status         | Blocks |
|----|--------------------------------------------------------------------------|----------------|--------|
| 01 | [HTML pruning & prompt construction](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/01-html-pruning-and-prompt.md) | ✅ Complete    | 02     |
| 02 | [Anthropic client & response validation](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/02-anthropic-client-and-validation.md) | ✅ Complete | 03 |
| 03 | [Cross-sample DOM validation](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/03-cross-sample-dom-validation.md)   | ✅ Complete    | 04     |
| 04 | [Diff report & `detect-schema` command](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/04-diff-report-and-command.md) | ✅ Complete | 05-09 |
| 05 | [Model default & token budget](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/05-model-default-and-token-budget.md) | ⬜ Not started | -    |
| 06 | [Search-page selector support](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/06-search-page-support.md)          | ⬜ Not started | -      |
| 07 | [Offline mode & golden tests](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/07-offline-mode-and-golden-tests.md)  | ⬜ Not started | 08     |
| 08 | [JSON report & drift exit code](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/08-json-report-and-drift-check.md)  | ⬜ Not started | -      |
| 09 | [Prompt-injection hardening](/docs/roadmap/0001-generic-implementation/04.0-ai-assisted-schema-detection/09-prompt-injection-hardening.md)      | ⬜ Not started | -      |

## Key constraints

- **Propose, never auto-apply.** No subtask may add a `--apply` or write into `src/`.
- **Fail loudly.** A missing key, a load failure, or a malformed AI response → `SchemaDetectionError` + exit 1,
  never partial output.
- **Tests never call the real API.** The `anthropic` client is always mocked, and subtask 07 adds recorded responses.
- Implementers load the `claude-api` skill before touching model IDs, token counting, or caching (subtask 05).
- `schema_detection.py` imports only from `scraping/` + `book_utils.py`.
