# 0003 - LLM Extraction Fallback for Drifted Single Fields

> **Status:** Accepted
>
> **Date:** 2026-10-23
>
> **Supersedes:** _(none)_
>
> **Superseded by:** _(none)_

## Context

[ADR 0002](/docs/adr/0002-ai-assisted-schema-detection.md) drew a firm line: the LLM may *propose*
CSS selectors, but it never touches stored data - a human applies a selector by hand, and the
scraper's data path stays fully deterministic. That line is the project's core data-quality
guarantee, because a wrong selector corrupts silently rather than crashing.

The cost of that line is a data gap. When a site's markup drifts, a *needed* selector starts
matching nothing (or, worse, an empty element) and the scraper writes a record missing a field -
ISBN, description, publication date - until a human notices, runs `detect-schema`, and ships a fix.
The 04.0/08 drift check narrows the window but does not close it: the live Amazon `DESCRIPTION`
selector currently matches an *empty* element, so the drift check reports `ok` while the scraper
gets nothing. The record is persisted incomplete, and the dedup `hash` locks it in - a later
correct scrape of the same book is discarded as a duplicate, so the gap becomes permanent without
manual intervention.

The [Scrapegraph-ai evaluation](/docs/reviews/2026-10-04-scrapegraph-ai-evaluation.md) rejected the
dependency but flagged two ideas worth borrowing: an LLM extraction fallback for a single drifted
field, and Markdown conversion to shrink prompts. This ADR records the trust decision behind the
first: **deliberately allowing Claude-sourced *values* into stored records, under strict
conditions.** That is a new trust boundary - the inverse of ADR 0002's "AI out of the data path" -
and it needs its own record before any code is written.

The forces to balance:

- **A wrong AI value corrupts data just as silently as a wrong selector.** The fallback must be
  *more* trustworthy than a blank field, not merely more complete. Every value needs a
  deterministic gate it cannot talk its way past.
- **The fallback runs on live production pages during scraping,** not on a developer's chosen sample
  pages as `detect-schema` does, and its output is **stored**, not shown to a human for review. The
  page is untrusted input that steers a stored value - a strictly larger attack surface than 04.0's,
  which is why this task carries its own threat model
  ([`docs/security/2026-10-04-schema-detection-prompt-injection.md`](/docs/security/2026-10-04-schema-detection-prompt-injection.md)
  is extended for the new sink).
- **Identity must stay deterministic.** The dedup `hash` is `SHA-256(title|authors|year)`. An
  AI-guessed `title` could merge two different books or split one into two, so `title` cannot be
  eligible for the fallback no matter how convenient.
- **Cost is now per-page, not per-maintenance-session.** `detect-schema` runs occasionally by a
  developer; a scrape run touches thousands of pages. An unbounded fallback over a site-wide drift
  is a cost-exhaustion event. It needs hard per-run ceilings and must be off by default.
- **The project already has a hardened AI stack** (04.0's pruner, `<page_html>` fence,
  `sanitize_untrusted()`, token budget, model resolution, replay/record). A parallel stack would be
  a second injection defense that can drift from the first. Reuse is the safer path.

## Decision

Add a narrow, **opt-in** LLM extraction fallback that fills a **single eligible field** with Claude
only after the deterministic path has exhausted itself, validates the answer deterministically
before storing it, and marks every record it touched. It reuses the 04.0 AI stack rather than
building a parallel one (extracted into `scraping/ai_common.py` by subtask 02).

This ADR **amends [ADR 0002](/docs/adr/0002-ai-assisted-schema-detection.md) by cross-reference
only; it does not supersede it.** ADR 0002's propose-only rule for *selectors* still holds in full -
the fallback never writes `parameters.py` and never auto-repairs a selector. What changes is a
separate, additive decision: AI-sourced *values* may enter stored records through the gated path
below.

### Eligible fields

The fallback may fill, and only fill, these record fields:

- `DESCRIPTION`
- `PUBLICATION_DATE`
- `ISBN13`
- `ISBN10`
- `AUTHORS`

**`title` is deliberately excluded.** A missing title stays a `PARSE_ERROR` (03.0/03) exactly as
today. The title feeds the dedup `hash`; an AI-guessed title could silently merge or split records,
and no downstream check could recover from that. This exclusion is a hard constraint, not a default
- it is the reason the threat model's "poisoning the dedup hash" vector is closed by construction.

**`AUTHORS` and the hash (accepted trade-off).** `authors` also feeds `hash_book`, so an AI-filled author list
can split a record from its deterministic duplicate (threat model EXT-3). `AUTHORS` stays eligible, unlike
`title`, because the value must pass the evidence and normalizer gates, is marked in `ai_extracted_fields`, and is
re-scrapeable; the residual risk is a duplicate record, not a silent merge, and the run report flags it.

### Trigger

The fallback fires for an eligible field only when that field is **still empty after the full
deterministic path has run**, including any `*_ALT` fallback selector (e.g. `DESCRIPTION_ALT`).
Selectors stay primary; the LLM fills a single gap they left, never competes with a value they
produced. Leanpub's API detail path is not hooked - it has no CSS selectors to drift.

### Validation contract

No AI value is stored unless it passes every deterministic gate below. Each gate drops the field on
failure (logging a reason code); nothing raises, and a dropped field is simply absent from the
result - the record is saved with deterministic data only.

1. **Shape.** The response is strict JSON keyed by eligible field name, each entry
   `{"value": ..., "evidence": ...}`. `value: null` is the explicit, prompt-preferred "not on this
   page" answer. Unknown field keys and non-string `value`/`evidence` are dropped.
2. **Verbatim evidence.** `evidence` must be a verbatim, whitespace-normalized, case-sensitive
   substring of the page's **visible text**. An absent quote is treated as a hallucination and
   dropped. (If the Markdown spike, subtask 04, is adopted, the evidence check still runs against the
   page's visible text, never against the Markdown.)
3. **Value derivable from evidence.** The value must be derivable from its own evidence string - a
   normalized ISBN or date must parse *from the evidence*, and a description must be a substring of
   the page text - so the model cannot quote one thing and store another.
4. **03.1 normalizer.** The value must pass the canonical normalizer for its field: ISBN-10/13
   checksum, date parse into `YYYY[-MM[-DD]]`, authors as a bounded list of non-empty strings,
   description length bound. These are the same validators a deterministic scrape uses, so an
   AI-filled field is indistinguishable in shape from a deterministic one.
5. **Sanitizing.** `sanitize_untrusted()` (04.0/09) runs on every stored string, stripping control
   and line-separator characters before persistence.

### Provenance

Every field the fallback merges is recorded in a new record field
`ai_extracted_fields: list[str]` (sorted record-field names, `[]` by default), written by **both**
storage backends. This makes AI-sourced values always distinguishable from deterministic ones -
for auditing, for the run summary, and for re-scraping.

A later **deterministic** scrape never overwrites good data with AI data, and AI data is never
preferred over deterministic data. Because dedup keeps the first record by `hash`, the mechanism for
replacement is operational, not in-path: the run report flags AI-filled records so a human (or a
scheduled job, once the drifted selector is fixed) can re-scrape them and let the fresh deterministic
record replace the AI-filled one. The field is reserved in the schema-2 `BookRecord` by 03.1/01, so
adding it is not a schema-version bump of its own (see subtask 05).

### Cost and opt-in

- **Off by default.** No run makes an Anthropic call unless `--ai-fallback` is passed. Without the
  flag, behavior and goldens are byte-identical and `ANTHROPIC_API_KEY` is never read.
- **Fail loudly at start-up, soft at runtime.** With `--ai-fallback`, a missing `ANTHROPIC_API_KEY`
  or an empty model ID fails *before any scraping* (`print_log` error, exit `1`). But during a run, a
  malformed response, refusal, or `max_tokens` stop logs a warning and yields `{}` - one hostile or
  pathological page must not abort a scrape of thousands. This asymmetry is deliberate and differs
  from `detect-schema`, which fails the whole run on a bad response.
- **Hard ceilings.** At most **one request per page** (all missing eligible fields batched into one
  prompt), a per-run request cap (`--ai-fallback-max-pages N`, default `20`, enforced by an
  `asyncio`-safe shared counter so the concurrent batch cannot overshoot), one retry on a transient
  error, and the 04.0 pre-flight token budget.
- **No secrets or PII leak.** `ANTHROPIC_API_KEY`, raw prompts, and page HTML never reach a log line,
  a stored record, or the run report; SDK errors report only the exception class and HTTP status.
  The end-of-run summary carries counts and summed token usage - no prices, no key.
- **Tests never call the real API.** Offline replays only, as in 04.0/07.

## Alternatives Considered

| Alternative | Pros | Cons | Reason rejected |
|-------------|------|------|-----------------|
| Adopt Scrapegraph-ai | Prebuilt LLM-extraction graphs; Markdown conversion built in | LLM extracts *every* field on *every* page (non-deterministic data selectors return for free); telemetry on by default uploads page content to a third party; large LangChain dependency tree; stale Claude model table silently truncating to an 8,192-token window; stealth/evasion features conflict with the 03.2 no-evasion policy | Rejected - solves the inverse problem and is unsafe by default; see the [evaluation](/docs/reviews/2026-10-04-scrapegraph-ai-evaluation.md). We borrow the *idea*, not the dependency. |
| Whole-page LLM extraction ("prompt instead of selectors") | One prompt replaces per-site selector maintenance | Non-deterministic output for data CSS selectors return reproducibly and for free; per-page LLM cost on every field of every page; no deterministic ground truth to validate the answer against; makes the LLM the primary data path, the exact inversion ADR 0002 forbids | Rejected - selectors stay primary; the fallback fills single gaps only, with page text as the validation ground truth |
| Auto-apply `detect-schema` proposals | Closes the drift gap without any runtime LLM call | A wrong selector silently corrupts every record for that field until a human notices; removes ADR 0002's human-review safety mechanism; violates the "propose, never apply" hard constraint | Rejected - directly contradicts ADR 0002; the data-quality risk is unacceptable, and this ADR explicitly does not reopen it |
| Do nothing - wait for a human to fix the selector | Zero new code, cost, or trust boundary; the existing `detect-schema` + drift check already surface the problem | The data gap persists for the whole detection-to-fix window; dedup by `hash` makes an incomplete record permanent (a later correct scrape is dropped as a duplicate); the empty-element Amazon `DESCRIPTION` case is not even flagged by the drift check | Rejected - the recurring, sometimes-silent data loss is exactly what warrants a bounded safety net; "do nothing" keeps shipping incomplete records |
| **Gated single-field LLM fallback, opt-in, deterministically validated, provenance-marked (chosen)** | Recovers a drifted field without a human in the loop; every value passes verbatim-evidence + normalizer gates it cannot bypass; `title` excluded so dedup identity stays deterministic; off by default with hard per-run caps; reuses the hardened 04.0 stack; AI values always distinguishable and re-scrapeable | Lets AI-sourced values into stored records (a new trust boundary); adds a runtime path that talks to a paid API and reads page HTML into a prompt; cost and injection surface that must be bounded and threat-modeled | **Accepted** |

## Consequences

### Positive

- A drifted single field - including the silent empty-element case the drift check misses - is
  recovered automatically when the fallback is enabled, instead of being lost until a human ships a
  selector fix.
- Every AI-sourced value passes a deterministic gate (verbatim evidence in the page text, value
  derivable from that evidence, the 03.1 normalizer) it cannot talk its way past, so a hallucinated
  or injected value is dropped, not stored.
- The dedup `hash` stays fully deterministic: `title` is never AI-filled, so records can never be
  silently merged or split by a guessed identity.
- AI-sourced values are always distinguishable (`ai_extracted_fields`) and re-scrapeable, so the
  fallback is a bridge to a deterministic fix, never a permanent substitute for one.
- One injection defense, not two: the fallback reuses 04.0's pruner, fence, sanitizer, and budget,
  so the hardening cannot drift between the two AI surfaces.
- ADR 0002 is intact - selectors are still propose-only, `parameters.py` is never auto-written.

### Negative

- It opens a new trust boundary: AI-sourced values now enter stored records. The mitigation is the
  deterministic validation contract and the dedicated threat model, but the boundary exists.
- It adds a runtime path that talks to a paid API and reads live, untrusted page HTML into a prompt -
  a cost and prompt-injection surface that must stay bounded (per-run cap, off by default, one
  request per page) and that the threat model owns.
- The quality of a recovered value depends on the model and the pruned-snapshot budget; a field
  whose evidence falls past the truncation point simply is not recovered (dropped, never guessed).
- Operators gain one more flag and one more failure mode to understand (`--ai-fallback`,
  `--ai-fallback-max-pages`, the start-up key/model check, the soft runtime error policy).

## Validation / Rollout

- **Acceptance gate:** this ADR must be accepted by the user before subtask 02 starts; the threat
  model must have no open CRITICAL finding, and every HIGH finding must map to a requirement in
  subtask 03 or 05.
- **Off-by-default proof:** with the flag off, the full test suite and every 04.0 golden are
  byte-identical, and no `ANTHROPIC_API_KEY` is read (`test_fallback_off_by_default_makes_no_call_and_reads_no_key`,
  `test_existing_goldens_unchanged_without_flag`).
- **Validation gates:** named unit tests in subtask 03 cover a valid ISBN with verbatim evidence
  kept, a hallucinated value dropped, a checksum failure dropped, a value not derivable from its
  evidence dropped, an unknown field key dropped, and an injected instruction unable to add a
  `title` field.
- **Identity protection:** `test_title_missing_still_parse_error_with_fallback_on` (subtask 05)
  proves `title` is never AI-filled even with the flag on.
- **Cost ceilings:** `test_cap_stops_requests_after_n_pages_across_concurrent_batch` and
  `test_one_request_for_multiple_missing_fields` prove the per-run cap and one-request-per-page rule.
- **No-leak:** `test_api_key_and_html_never_logged` (caplog sweep) and
  `test_run_summary_line_has_counts_and_no_key`.
- **End-to-end:** the replay golden `test_ai_fallback_goldens.py` recovers a drifted Amazon
  `DESCRIPTION` and records `ai_extracted_fields == ["description"]`, offline via replay.
- **Rollout:** ship behind `--ai-fallback` (default off). The first operational use is recovering the
  known empty-element Amazon `DESCRIPTION` drift; the drift check and `detect-schema` remain the
  primary, preferred path to a permanent deterministic fix.

## Links

- **Amends (by cross-reference, does not supersede):** [ADR 0002 - AI-Assisted CSS-Selector Schema Detection](/docs/adr/0002-ai-assisted-schema-detection.md)
- **Roadmap task:** [04.1 - LLM Extraction Fallback](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/README.md) (subtasks [01](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/01-adr-and-threat-model.md), [02](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/02-shared-ai-prompt-infrastructure.md), [03](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/03-field-extraction-engine.md), [04](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/04-markdown-snapshot-spike.md), [05](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/05-scraper-integration-and-provenance.md))
- **Threat model:** [`docs/security/2026-10-04-schema-detection-prompt-injection.md`](/docs/security/2026-10-04-schema-detection-prompt-injection.md) (extended by this task for the new stored-value sink; the dedicated `llm-extraction-fallback` threat model is authored by the security-auditor under subtask 01)
- **Origin:** [Scrapegraph-ai evaluation](/docs/reviews/2026-10-04-scrapegraph-ai-evaluation.md) - "What is worth borrowing"
