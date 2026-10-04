# 01 - ADR & threat model

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/README.md)
**Status:** ⬜ Not started
**Role:** Architect + Security Auditor

## Context

[ADR 0002](/docs/adr/0002-ai-assisted-schema-detection.md) keeps AI out of the data path: Claude proposes
selectors, and a human applies them. This task deliberately lets Claude-sourced *values* into stored records,
under strict conditions. That is a new trust decision, and it needs its own record before any code is written.

## Requirements

- **ADR 0003** `docs/adr/0003-llm-extraction-fallback.md` (MADR, from `docs/adr/template.md`), status `Proposed`
  until the user accepts it. It must decide and justify:
  - **Eligible fields.** Proposed: `DESCRIPTION`, `PUBLICATION_DATE`, `ISBN13`, `ISBN10`, `AUTHORS`.
    **Not** `title`. A missing title stays `PARSE_ERROR` (03.0/03), because the title feeds the dedup `hash`, and
    an AI-guessed title could merge or split records silently.
  - **Trigger.** The field is empty after the full deterministic path, including any `*_ALT` selector.
  - **Validation contract.** Verbatim evidence in page text, plus the 03.1 normalizer (see subtask 03).
  - **Provenance.** The `ai_extracted_fields: list[str]` record field, and how a later deterministic re-scrape
    replaces AI values.
  - **Cost and opt-in.** Off by default, plus the per-run caps (subtask 05).
  - **Alternatives considered.** Adopting Scrapegraph-ai (rejected, see the evaluation), whole-page LLM extraction,
    auto-applying `detect-schema` proposals, and doing nothing (wait for a human to fix the selector).
  - It amends ADR 0002 by cross-reference only; it does not supersede it.
- **Threat model** `docs/security/<date>-llm-extraction-fallback.md` (security-auditor). It extends
  [2026-10-04-schema-detection-prompt-injection.md](/docs/security/2026-10-04-schema-detection-prompt-injection.md)
  for the new sink. The fallback runs on **live production pages during scraping**, not on a dev's chosen samples,
  and its output is **stored**, not only shown to a human. In scope:
  - injected text steering a stored value (e.g. a fake ISBN or a spam description);
  - poisoning the dedup `hash` (which is why `title` is excluded);
  - cost exhaustion from many pages missing fields;
  - leaking the key or page HTML into logs, records, or the run report.
- `docs/adr/README.md` inventory row and `docs/security/README.md` index row.

## Files

- Create `docs/adr/0003-llm-extraction-fallback.md` and `docs/security/<date>-llm-extraction-fallback.md`.
- Modify `docs/adr/README.md` and `docs/security/README.md`.

## Tests

None (documentation). `/link-check docs/` must pass.

## Success criteria

- [ ] The ADR is accepted by the user before subtask 02 starts.
- [ ] The threat model has no open CRITICAL finding, and every HIGH finding maps to a requirement in
      subtasks 03 or 05.
