# Threat Model - LLM extraction fallback (task 04.1, subtask 01) - 2026-10-23

Written **before coding**, as the security-auditor merge gate for task 04.1
([README](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/README.md),
[subtask 01](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/01-adr-and-threat-model.md)).
It **extends** [2026-10-04-schema-detection-prompt-injection.md](/docs/security/2026-10-04-schema-detection-prompt-injection.md)
and its pre-code baseline [2026-10-02-ai-schema-detection.md](/docs/security/2026-10-02-ai-schema-detection.md)
(AIS-1..16) rather than restating them. Findings are numbered **EXT-1..EXT-10** and each HIGH maps to a
requirement in subtask 03 (field-extraction engine) or subtask 05 (scraper integration & provenance); the
mapping table at the end satisfies subtask 01's success criterion.

The paired ADR (`docs/adr/0003-llm-extraction-fallback.md`) is written in parallel by the architect; this
document does not duplicate it and must be read beside it.

## What changed versus 04.0 (the new trust decision)

`detect-schema` (04.0) keeps AI **out of the data path**: the model proposes CSS selectors, a human applies
them, and the only sink for model output is a sandboxed `page.locator(...)` query (AIS-3/AIS-4). Task 04.1
deliberately crosses that line under strict conditions. Two properties flip, and both widen the blast radius:

1. **The input is a live production page, not a dev's chosen sample.** The fallback fires during a normal
   `scrape-urls` / `search` run on whatever page the target serves right now, so the "hostile page" actor is
   no longer hypothetical.
2. **The output is stored, not merely shown to a human.** A value that survives validation is written into a
   `BookRecord` document in MongoDB or `books.json`, with no human in the loop before it lands. 04.0's core
   safety net - a human reviewer plus mandatory cross-sample DOM validation - is **gone** here. The deterministic
   validators (evidence check, value-derivable check, 03.1 normalizers, `sanitize_untrusted`) are the *only*
   gate, so each one is load-bearing.

## Assets & trust boundaries

- **Assets:** the `ANTHROPIC_API_KEY` (paid credential, read from `os.environ`/`.env` only when `--ai-fallback`
  is set); the Anthropic account's spend budget; the integrity of the stored corpus (`bookscraper_db.books` /
  `books.json`), especially the dedup `hash` (SHA-256 of normalized title+authors+year, `book_utils.hash_book`,
  `book_utils.py:367`); scraped author names (personal data) and third-party descriptions; the per-run log files
  under `~/.bookscrapper/logs/`; any committed `tests/fixtures/ai_extraction/*.replay.json` (public git history).
- **Boundary A (page -> prompt):** inherited Boundary 1 from AIS. `page.content()`
  (`scrape_details.scrape_book`, hook point per subtask 05) is pruned by the shared `ai_common` pruner and
  spliced into the `<page_html>` fence. The page controls all visible text and attributes; this is the
  prompt-injection boundary. Reuse of the hardened fence/escape/`sanitize_untrusted` stack (subtask 02) is
  mandatory - a parallel copy is itself a finding (EXT-5).
- **Boundary B (model output -> stored record):** NEW and the crux of this task. The model's returned
  `{field: {value, evidence}}` passes the subtask-03 validators and, if it survives, is merged into a
  `BookRecord` and persisted. There is no `page.locator` sandbox and no human here - the validators are the
  boundary.
- **Boundary C (stored value / usage -> downstream sinks):** the stored AI value later flows into a MongoDB
  document, a `books.json` entry, diagnostic CSVs (`failed_books.csv` etc.), the run-summary `print_log` line,
  and the 07.0 run report. Each is a place a hostile string or a leaked secret could surface.
- **Boundary D (offline/replay guarantee):** tests and goldens must make zero live Anthropic calls (replay
  only); with `--ai-fallback` off, no key is read and behavior is byte-identical to today.
- **Trust posture:** the page is hostile; the model output is untrusted (it is derived from the hostile page);
  there is **no human reviewer** on the stored-value path, so the deterministic validators plus provenance
  marking plus off-by-default are the entire safety net.

### Framing note that bounds every value finding

The evidence check ("`evidence` must be a verbatim substring of the page's visible text") proves the value
came **from the page**, not from model hallucination - but the page is hostile, so a crafted page can plant a
checksum-valid fake ISBN or a spam description *in its own visible text* and the evidence check will pass. This
is **not new exposure**: a deterministic selector scraping that same `<meta>`/visible element would store the
same lie. The AI fallback's residual delta over deterministic scraping is narrow and is what the validators
must contain: (a) injected *instructions* steering which page string becomes the value, and (b) a value that is
not actually derivable from any verbatim quote. The `value-derivable-from-evidence` check plus the per-field
normalizer bound (a) and (b) to "the page's visible text said this, in a well-formed shape," i.e. the same
trust level as a deterministic source - and every such value is marked in `ai_extracted_fields` for re-scrape.
That bound is why nothing here is CRITICAL.

## Findings

### [HIGH] EXT-1 Injected page text steering a stored field value (fake ISBN / spam description)
- Vector / evidence: Boundary B. A hostile page embeds attacker instructions in visible text/attributes
  (OWASP LLM01) to make the model emit an attacker-chosen `value` with a matching `evidence` quote the page
  also planted; the value is then stored in the record.
- Impact: a fake ISBN13/ISBN10/publication date, or a spam/defacement description, persisted in the corpus and
  served to every downstream consumer. Bounded by the framing note above (page-says-it + provenance), not RCE.
- Mitigation (maps to **subtask 03** validation contract - all five checks are merge-gate conditions, none may
  be dropped): (1) shape check drops unknown keys and non-string value/evidence; (2) `evidence` must be a
  verbatim, whitespace-normalized, case-sensitive substring of the page's **visible text**; (3) the value must
  be **derivable from the evidence string** (ISBN/date must parse *from the evidence*, description must be a
  page-text substring); (4) the 03.1 normalizers (ISBN checksum, date parse to `YYYY[-MM[-DD]]`, authors list
  shape, description <=10k chars); (5) `sanitize_untrusted()` on every stored string. Any failure drops the
  field with a reason code and stores deterministic data only. CWE-1427, CWE-20, OWASP LLM01.

### [HIGH] EXT-2 Dedup-hash poisoning via an AI-guessed title
- Vector / evidence: `hash_book(title, authors, year)` (`book_utils.py:367`) feeds the unique-by-`hash`
  dedup. An AI-sourced title would change the hash and could silently merge or split records.
- Impact: silent corpus corruption - two distinct books collapsed, or one book duplicated/orphaned - with no
  deterministic way to recover the correct identity.
- Mitigation (maps to **subtask 03** + ADR 0003 eligible-fields decision): `title` is **never eligible**; a
  missing title stays `PARSE_ERROR` (03.0/03). Guard tests: `test_injected_instruction_in_page_cannot_add_title_field`
  (subtask 03) and `test_title_missing_still_parse_error_with_fallback_on` (subtask 05). CWE-345.

### [HIGH] EXT-3 AI-sourced AUTHORS also feed the dedup hash - the title exclusion does not cover them
- Vector / evidence: `AUTHORS` **is** on the eligible list, yet `hash_book` includes authors (author
  de-duplication is part of the hash input - see CLAUDE.md migration note). A drifted author selector filled
  by the fallback with a partial/variant author set yields a hash that never matches the hash a correct
  deterministic scrape would produce, so dedup-by-`hash` keeps the AI record and the later good scrape is
  discarded as a "duplicate" - a permanent silent split, the same failure class that excludes `title`.
- Impact: same as EXT-2 but via authors; higher likelihood because authors legitimately vary (order, initials,
  "et al.") so a plausible-but-wrong AI author list passes the normalizer.
- Mitigation (maps to **subtask 03** value checks for authors + **subtask 05** provenance): keep the evidence
  and author-shape checks (list of non-empty strings, each <=200 chars, <=20 entries); and rely on provenance -
  `ai_extracted_fields` lists `authors`, and subtask 05's rule that a later deterministic scrape never
  overwrites good data **and the run report flags AI-filled records for re-scrape** - so an AI-author record is
  explicitly re-visitable rather than frozen. **ADR 0003 must explicitly acknowledge the authors-in-hash risk
  and record the accepted trade-off** (authors stay eligible because they are re-scrapeable and provenance-
  marked, unlike title which gates the whole record). CWE-345.

### [HIGH] EXT-4 Cost / spend exhaustion from many pages missing an eligible field
- Vector / evidence: Boundary A at scale. A drifted selector (or a hostile site that strips a field from every
  page) makes the eligible field empty on *every* page, so the fallback fires a paid Anthropic request per
  page across a large `scrape-urls` batch or a `search` crawl.
- Impact: unbounded Anthropic spend driven by the scrape target, not the operator (CWE-770, CWE-400).
- Mitigation (maps to **subtask 05** flags + **04.0** budget): off by default (`--ai-fallback`); **one request
  per page at most** (all missing eligible fields batched into one prompt, subtask 03); `--ai-fallback-max-pages N`
  (default 20, 1..200) as a hard per-run **request** cap enforced by an `asyncio`-safe shared counter so the
  batch-of-10 concurrency cannot overshoot it; one retry only; and the reused 04.0 `count_tokens` pre-flight /
  re-prune budget (AIS-10). Guard test: `test_cap_stops_requests_after_n_pages_across_concurrent_batch`.

### [HIGH] EXT-5 Injection defenses must be the single hardened 04.0 stack, not a parallel copy
- Vector / evidence: subtask 02 extracts the fence/escape/`sanitize_untrusted`/budget/model-resolution helpers
  into `scraping/ai_common.py`. If subtask 03 re-implements any of them, two injection defenses drift apart and
  one silently regresses (e.g. PI-2 attribute-bearing fence close, PI-3 U+2028/U+2029 stripping fixed only in
  one copy).
- Impact: a prompt-injection breakout (AIS-1) or control-char report/record spoof (AIS-13) re-opens on the
  fallback path only, invisibly.
- Mitigation (maps to **subtask 02** + **subtask 03**): the fallback prompt builder uses `ai_common`'s
  `<page_html>` fence + untrusted-data framing + `_neutralize_fence` + `sanitize_untrusted` verbatim; subtask 02
  is a pure refactor (04.0 goldens and replay keys byte-identical) and its import-guard test keeps `ai_common`
  importing only `scraping/` + `book_utils.py`. No fence/sanitizer logic is defined in `ai_extraction.py`.
  CWE-1427, CWE-116.

### [HIGH] EXT-6 Key / raw prompt / page HTML must never reach a log, a stored record, or the run report
- Vector / evidence: new sinks this task adds - the stored record (Boundary B/C), the end-of-run `print_log`
  summary line (subtask 05), diagnostic CSVs, and committed `*.replay.json` recordings (subtask 05). The key is
  read at start-up when `--ai-fallback` is set.
- Impact: `ANTHROPIC_API_KEY` or unbounded page HTML leaked into `~/.bookscrapper/logs/*.log` (all modules'
  loggers land there via the root handler), into a stored document/CSV, or into public git history - a
  credential/PII disclosure. CWE-532, CWE-209, CWE-200, OWASP A09.
- Mitigation (maps to **subtask 03** + **subtask 05**): the key is read only into the SDK `api_key=` kwarg;
  SDK errors surface the exception class + HTTP status only, never the body (mirrors AIS-8 / rotate-cert);
  `ANTHROPIC_API_KEY`, raw prompts, and page HTML never reach a log line or the returned result (subtask 03);
  the run-summary line carries counts and summed usage only - no prices, no key (subtask 05); replay files are
  `sha256(prompt)` -> response and never contain the key. Guard tests:
  `test_api_key_and_html_never_logged` (caplog sweep, subtask 03) and
  `test_run_summary_line_has_counts_and_no_key` (subtask 05). Any committed `*.replay.json` that embeds page
  HTML falls under the 06.0 corpus `sanitize_html` + `assert_clean` + prose policy (AIS-14).

### [MEDIUM] EXT-7 One hostile page must degrade to deterministic-only, never abort the run and never store garbage
- Vector / evidence: subtask 03 error policy. A malformed/non-JSON response, a refusal, or a `max_tokens` stop
  must not crash the scrape; but the failure must not be "store what we got" either.
- Impact: availability (a single hostile page aborting a whole run) balanced against integrity (storing an
  unvalidated value).
- Mitigation (maps to **subtask 03** + **subtask 05**): on a malformed/refusal/`max_tokens` response, log a
  warning with a reason code and return `{}` - the book is saved with deterministic data only; **configuration**
  errors (missing key with `--ai-fallback`, empty/bad model ID) still fail loudly at **start-up** before any
  scraping (subtask 05, `print_log` error, exit 1). Guard tests:
  `test_malformed_response_returns_empty_not_raise` (03),
  `test_missing_api_key_with_flag_fails_before_scraping` (05). CWE-703, CWE-20.

### [MEDIUM] EXT-8 Stored AI value widens downstream injection surfaces (CSV formula / log injection)
- Vector / evidence: Boundary C. An AI description (<=10k chars) or author string is written to a MongoDB
  document, a `books.json` entry, the `failed_books.csv`/`other_links.csv` diagnostics, and log lines. A cell
  beginning `=`/`+`/`-`/`@` is a CSV-formula-injection vector in a spreadsheet; embedded control/ANSI chars
  spoof a terminal or log (CWE-1236, CWE-117, CWE-150).
- Impact: this is a **pre-existing** project surface (see the README STRIDE draft on unguarded CSV cells and
  `$`/dotted keys) that the AI path *widens* - it does not create NoSQL-operator injection, because the AI value
  is a document **value**, never a key, so `$`/dotted keys are not attacker-reachable here, and the mongo
  backend already pre-validates each document with `bson.BSON.encode`.
- Mitigation (maps to **subtask 03**): `sanitize_untrusted()` strips C0/C1/DEL/ESC and U+2028/U+2029 from every
  stored AI string (EXT-5 reuse), and the normalizers bound length and shape. CSV-formula hardening of the
  diagnostic writers remains a pre-existing project item, not introduced or resolved by 04.1; flag it to the
  team but do not gate 04.1 on it.

### [LOW] EXT-9 Provenance integrity is the only re-scrape signal - it must be written and never silently lost
- Vector / evidence: Boundary B/C. `BookRecord.ai_extracted_fields` (reserved in `models.py:60/146`, schema 2)
  is the sole record that a value was AI-sourced; if it is dropped by one backend or on a re-scrape path, an
  AI value becomes indistinguishable from a deterministic one and is never corrected.
- Impact: repudiation / loss of the audit trail that EXT-1/2/3 lean on for recoverability.
- Mitigation (maps to **subtask 05**): `ai_extracted_fields` (sorted record-field names, `[]` default) is
  written by **both** backends, and dedup-by-`hash` keeps the first record while the run report flags AI-filled
  records for re-scrape (a later deterministic scrape never overwrites good data). Guard test:
  `test_missing_description_filled_and_marked_in_ai_extracted_fields` (subtask 05). CWE-778.

### [LOW] EXT-10 Markdown spike must not weaken the evidence check or smuggle in an unaudited dependency
- Vector / evidence: subtask 04 measures Markdown vs pruned-HTML snapshots. If adopted, a lossy Markdown
  rendering could change what text the model sees, and the evidence check could regress to matching the
  Markdown rather than the page's visible text.
- Impact: a validator bypass (evidence "found" in Markdown that is not in the real page) or an unvetted
  third-party converter in the dependency tree.
- Mitigation (maps to **subtask 04**): the snapshot format is only a prompt-builder parameter; the evidence
  check always runs against the **page's visible text, never the Markdown**; Markdown is adopted only if it
  loses no ground-truth value on any fixture and any new dependency passes `/dep-audit`; stdlib-first, no new
  `pyproject.toml` dependency added by the spike itself. CWE-20.

## Mapping of HIGH findings to subtask requirements (subtask-01 success criterion)

| Finding | Severity | Maps to | Named guard test(s) |
|---------|----------|---------|---------------------|
| EXT-1 injected value steering | HIGH | subtask 03 validation contract (checks 1-5); ADR 0003 eligible fields | `test_valid_isbn_with_verbatim_evidence_is_kept`, `test_value_without_evidence_on_page_is_dropped`, `test_isbn_failing_checksum_is_dropped`, `test_value_not_derivable_from_evidence_is_dropped`, `test_unknown_field_key_is_dropped` |
| EXT-2 title hash poisoning | HIGH | subtask 03 (`title` never eligible); subtask 05; ADR 0003 | `test_injected_instruction_in_page_cannot_add_title_field` (03), `test_title_missing_still_parse_error_with_fallback_on` (05) |
| EXT-3 authors-in-hash split | HIGH | subtask 03 (authors evidence + normalizer); subtask 05 provenance/re-scrape; **ADR 0003 must record the trade-off** | `test_missing_description_filled_and_marked_in_ai_extracted_fields` (05, provenance), subtask 03 authors validation tests |
| EXT-4 cost exhaustion | HIGH | subtask 05 (`--ai-fallback` off-by-default, `--ai-fallback-max-pages`, asyncio-safe counter, one request/page, one retry); 04.0 budget | `test_cap_stops_requests_after_n_pages_across_concurrent_batch`, `test_fallback_off_by_default_makes_no_call_and_reads_no_key` (05) |
| EXT-5 single shared defense | HIGH | subtask 02 (`ai_common` refactor, import guard, byte-identical goldens); subtask 03 (reuse fence/sanitizer) | `test_ai_common_imports_only_scraping_and_book_utils`, 04.0 golden byte-identity (02) |
| EXT-6 key/HTML leak | HIGH | subtask 03 (never logged); subtask 05 (run summary, start-up key check) | `test_api_key_and_html_never_logged` (03), `test_run_summary_line_has_counts_and_no_key`, `test_missing_api_key_with_flag_fails_before_scraping` (05) |

MEDIUM/LOW findings EXT-7..EXT-10 map to subtasks 03/04/05 as noted inline; they are not merge-gate conditions
for subtask 01 but must be satisfied by their owning subtask.

## Verdict: PASS_WITH_FOLLOWUP

No CRITICAL finding. No code exists yet, so nothing is presently exploitable; every HIGH is a **merge-gate
condition** for the subtask it maps to (EXT-1/EXT-2/EXT-3/EXT-6 -> subtask 03 and 05, EXT-4 -> subtask 05,
EXT-5 -> subtask 02/03). All six HIGH findings map to a named requirement and guard test in subtask 03 or 05,
satisfying subtask 01's success criterion.

Nothing is CRITICAL because the stored-value blast radius is bounded on every axis: off by default (no call
without `--ai-fallback`); the value must be a verbatim, normalizer-valid quote from the page's own visible text
(equivalent trust to a deterministic selector on the same hostile page); `title` is excluded from eligibility
so the dedup hash cannot be AI-authored outright; every AI value is provenance-marked for re-scrape; there is
no `eval`/shell/`page.evaluate` sink and the AI value is a document value, never a key; and hard per-run cost
caps plus the 04.0 token budget bound spend.

**This verdict flips to BLOCK if the delivered 04.1 code violates any of:** the subtask-03 validation contract
(any of the five checks dropped), `title` eligibility (EXT-2), the single-shared-defense rule (EXT-5, a
re-implemented fence/sanitizer), the off-by-default + per-run cap (EXT-4), or the key/HTML-never-in-any-sink
rule (EXT-6). One **required ADR addition** before subtask 02 starts: ADR 0003 must explicitly record the
**authors-in-hash** trade-off from EXT-3 (why `authors` stays eligible while `title` does not).

Hand the fixes/implementation to `python-expert` and the regression tests (evidence/derivable/normalizer drop
matrix, title-never-eligible, authors-provenance, cap-across-concurrent-batch, key-never-in-output,
malformed-returns-empty) to `testing-expert`.
