# 0002 - AI-Assisted CSS-Selector Schema Detection

> **Status:** Accepted
>
> **Date:** 2026-09-23
>
> **Supersedes:** _(none)_
>
> **Superseded by:** _(none)_

## Context

Every field `bookscraper` extracts from a book detail page (title, authors, ISBN-10/13,
publication date, description, tags) is located by a CSS selector stored in
`site_constants[site]` in `src/bookscraper/scraping/parameters.py`. Those selectors are
the scraper's single most fragile surface: each of the four sites (Amazon, Packtpub,
Leanpub, O'Reilly) ships markup changes on its own schedule, and when a site's markup
drifts, a selector silently starts matching nothing (or the wrong element) and the
scraped data quietly degrades - a scrape that "succeeds" but writes `N/A` ISBNs and empty
descriptions.

Today, refreshing those selectors is a manual, ad hoc loop a developer runs with
Playwright plus informal AI assistance: open a sample page in an inspectable browser,
hunt for each field's element, ask an AI to propose a robust selector, validate it by
hand against the live DOM, and paste the result into `parameters.py`. The artifacts of
that loop are visible in the file itself - comments like
`# Usually inside #detailBullets_feature_div...` and `# Needs investigation, might be in a
meta tag...`. The loop is real, repeated, and undocumented, which makes it slow, easy to
do inconsistently, and impossible to run quickly across all four sites to check for
drift.

The forces to balance:

- **Selector correctness has direct data-quality consequences.** A wrong selector does
  not crash; it corrupts. Any automation must be *more* trustworthy than the manual loop,
  not merely faster.
- **The manual loop's judgement step (does this selector actually match, on more than one
  page?) is exactly where errors hide** and must be preserved, not abstracted away.
- **This is dev-maintenance tooling, not a runtime scraping path.** It runs occasionally,
  by a developer, with an API key - not in a scheduled scrape.
- **The project already has a strong "automation proposes, a human/deterministic step
  decides" convention** (the Atlas cert-rotation flow only rewrites `settings.json` for a
  value it deterministically owns and otherwise just prints a reminder; the config
  resolver fails loudly rather than guessing). New tooling should extend that convention,
  not introduce a new "the tool edits your source for you" pattern.

## Decision

Add a **`bookscraper detect-schema` subcommand** backed by a new
`scraping/schema_detection.py` module that automates the manual loop while preserving its
judgement step and never crossing the line into editing the schema.

The pipeline, for a given `--site` and one or more `--url` sample detail pages:

1. **Fetch + prune** - load each URL with Playwright, reusing
   `scrape_details.route_handler` so only the document is fetched, then reduce the HTML to
   an LLM-sized snapshot (`prune_html`: strip `<script>`/`<style>`/`<svg>`/comments,
   collapse whitespace, truncate to a byte budget).
2. **Infer** - send one sample's pruned HTML to Claude via the official `anthropic` SDK,
   asking for a JSON object keyed by the *existing* field names in `site_constants[site]`,
   each with a proposed selector and a one-line rationale (`build_prompt` /
   `call_anthropic` / `validate_ai_response`).
3. **Validate, don't trust** - re-query every proposed selector against *each* sample
   page's live DOM and record per-URL match/count/snippet, flagging any field that matches
   on some sample URLs but not others (`validate_across_samples`). This is the manual
   loop's judgement step, mechanized and made cross-URL by default.
4. **Propose, never apply** - emit a diff-style report (stdout, optional `--output`)
   comparing proposals to current values (`format_diff_report`). The tool **never writes
   to `parameters.py`**; a human applies changes by hand.

Supporting decisions:

- **Never auto-apply (hard constraint).** The module has no code path that writes
  `parameters.py`. This mirrors the cert-rotation flow's restraint and reflects that a
  wrong selector corrupts data silently - the human review is the safety mechanism, not a
  formality.
- **Fail loudly, no partial output.** A missing `ANTHROPIC_API_KEY`, a page-load failure,
  or a malformed/non-JSON AI response each raise `SchemaDetectionError`, surfaced by
  `commands/detect_schema.py` as a `print_log` error and a non-zero exit - matching the
  config resolver's "fail loudly rather than silently guess" philosophy.
- **`ANTHROPIC_API_KEY` is read directly from the environment** (`os.environ`, like
  `rotate_cert.py` reads its `ATLAS_*` vars), **not** through
  `backends/mongo/config.py`'s `settings.json` mechanism, which is Mongo-auth-specific.
  This is a standalone dev tool, not a runtime auth path.
- **Import direction stays one-way.** `commands/detect_schema.py` →
  `scraping/schema_detection.py` → `scraping/parameters.py` (read as the diff baseline).
  No `backends/` dependency (no storage is involved); `schema_detection.py` never imports
  from `commands/`.

## Alternatives Considered

| Alternative | Pros | Cons | Reason rejected |
|-------------|------|------|-----------------|
| Keep the manual loop, only document it | Zero new code/deps; no API key needed | Still slow and inconsistent; no fast "check all 4 sites for drift" path; the error-prone validation step stays manual | Rejected - Part 1 (documentation) is delivered regardless, but the repeated cost is exactly what warrants automating |
| Auto-apply proposals directly into `parameters.py` | Fewest keystrokes for the developer | A wrong selector silently corrupts scraped data; removes the human judgement that catches false positives; breaks the project's "propose, don't decide" convention | Rejected - violates the hard "propose, never apply" constraint; the data-quality risk is unacceptable |
| Non-AI heuristic selector generator (e.g. pick shortest unique CSS path) | No API key, no model dependency, deterministic | Produces exactly the fragile auto-generated class chains the manual loop deliberately avoids; no rationale; no sense of which attributes are stable | Rejected - would regress selector quality below the current hand-tuned baseline |
| A separate standalone script under `scripts/` | Keeps it out of the main CLI | Diverges from the established subcommand pattern (`scrape-urls`/`search`/`rotate-cert`); loses shared `--log-severity`/logging wiring; `scripts/` is already the graveyard of stale template scripts | Rejected - a first-class subcommand is consistent and discoverable |
| AI-assisted detection as a `detect-schema` subcommand that proposes and validates but never writes (chosen) | Fast, repeatable across all sites; preserves + mechanizes the validation step; fits the "propose, don't decide" and "fail loudly" conventions | Adds the `anthropic` dependency + an `ANTHROPIC_API_KEY`; LLM output must be validated and shape-checked, not trusted | **Accepted** |

## Consequences

### Positive

- Refreshing a site's selectors becomes one command instead of a manual browser session,
  and running it across all four sites to detect drift is now cheap.
- The most error-prone part of the manual loop (validating a selector against *multiple*
  live pages, not one) is mechanized and cross-URL by default - inconsistency is flagged,
  never silently resolved.
- The tool cannot corrupt the schema: it has no write path to `parameters.py`, so a bad AI
  proposal is at worst a report line a human declines to apply.
- Consistent with the codebase's existing conventions (subcommand shape, direct-`os.environ`
  read for a dev-tool credential, fail-loud error handling, one-way imports), so there is
  nothing new to learn to maintain it.

### Negative

- Adds `anthropic` as a direct runtime dependency and an `ANTHROPIC_API_KEY` a developer
  must supply to use the subcommand (the rest of the CLI does not need it).
- The proposal quality depends on the model and the pruned-HTML budget; a page whose
  relevant markup falls past the truncation point may yield a weaker proposal. Mitigated by
  the always-on live-DOM validation, which surfaces a non-matching selector rather than
  letting it through.
- One more surface that talks to an external paid API, gated behind a subcommand a
  scheduled scrape never invokes.

## Validation / Rollout

- Unit tests (`tests/unit/scraping/test_schema_detection.py`) cover the pure logic:
  HTML pruning/truncation, code-fence stripping, AI-response shape validation, detail-field
  selection, and diff-report formatting (including the inconsistency-flagging path).
- Mock tests (`tests/mock/scraping/test_schema_detection.py`,
  `tests/mock/commands/test_detect_schema.py`) cover the pipeline with Playwright faked and
  the `anthropic` client mocked - happy path, missing `ANTHROPIC_API_KEY`, malformed AI
  JSON, a selector that validates on some sample URLs but not others (must be flagged), and
  a page-load failure. The suite never makes a real Anthropic API call.
- Acceptance check for this decision: `detect_schema` must have no code path that writes
  `parameters.py`, and every failure mode must raise `SchemaDetectionError` (no partial
  output). If a future change adds an auto-apply path, this ADR is no longer being followed.

## Links

- **Supporting docs:** [`docs/scraping/schema-detection.md`](/docs/scraping/schema-detection.md) - the manual loop (Part 1) and the automated tool (Part 2), with usage examples and manual-vs-tool guidance.
- **Implementation:** `src/bookscraper/scraping/schema_detection.py`, `src/bookscraper/commands/detect_schema.py`, and the `detect-schema` subparser in `src/bookscraper/cli.py`.
