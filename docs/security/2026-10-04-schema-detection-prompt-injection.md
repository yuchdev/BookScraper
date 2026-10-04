# Threat Model - detect-schema prompt-injection hardening (task 04.0, subtask 09) - 2026-10-04

Required subtask-09 artifact (spec: "`security-auditor` writes
`docs/security/<date>-schema-detection-prompt-injection.md` - threat model, residual risk"; success
criterion: "Security-auditor verdict has no CRITICAL findings"). This is the **as-built** review:
the 2026-10-02 model (`docs/security/2026-10-02-ai-schema-detection.md`, AIS-1..16) was written
before coding as the merge-gate; this one verifies the delivered code against those requirements and
records residual risk. Read the two together - this does not restate AIS-1..16, it records whether
each is satisfied in the shipped `src/bookscraper/scraping/schema_detection.py` and
`src/bookscraper/commands/detect_schema.py`.

Scope: working tree (`git diff HEAD`) + committed 04.0 range (`git diff 09c2534..HEAD`).

## Assets & trust boundaries

Unchanged from the 2026-10-02 model. The prompt-injection boundary is **Boundary 1** (hostile page
HTML -> `prune_html` -> LLM prompt) and **Boundary 2** (model-proposed selector -> Playwright
locator query). The safety net the whole design leans on is propose-only (AIS-3) + mandatory
cross-sample live-DOM validation (AIS-2) + a human reviewer; every finding below is bounded by that
net plus the AIS-7 per-eval timeout.

## As-built verification

Adversarial probes were run from a scratch script (not committed) exercising `reject_selector`,
`_neutralize_fence`/`prune_html`, and `sanitize_untrusted` directly; Playwright engine behaviour was
confirmed from Playwright docs (chromium binary not installed on this host, so the locator leg was
reasoned, not executed).

## Re-verification of the follow-up fixes (commit `8429241`, 2026-10-04)

Re-ran the scratch probes against the committed code. **All three follow-ups are closed and no new
bypass was introduced.**

- **PI-1 CLOSED.** `reject_selector` now rejects `internal:` anywhere (`internal:text="x"`,
  `internal:attr=[name=/.*/]`, `internal:role=`, `internal:testid=`, mid-string `div >> internal:...`,
  uppercase `INTERNAL:` - all rejected), the engine-prefix regex is generalized to
  `^\s*[A-Za-z_-][\w-]*(:[\w-]+)*\s*=` (colon-qualified engines caught), and `_REGEX_LITERAL_RE`
  now `.search`es for a `/regex/` **anywhere** after quoted spans are blanked (`div /ab+c/ span`,
  `[data-x] /re/i` rejected). No RCE was ever possible; this removes the remaining non-CSS-engine path.
- **PI-2 CLOSED.** `_FENCE_TAG_RE = <\s*/?\s*page_html\b[^>]*>` now neutralizes the attribute-bearing
  close/open (`</page_html foo="x">`, `<page_html data-x>`, `< /page_html bar >` all HTML-escaped).
- **PI-3 CLOSED.** `_UNTRUSTED_CONTROL_RE` now also strips U+2028/U+2029 (and U+0085 NEL, already in
  the C1 range) - confirmed `a\u2028b` -> `ab`, `c\u2029d` -> `cd`.
- **Card multiplier bounded.** `MAX_CARDS_PER_SAMPLE = 50` is applied as `cards[:50]` in
  `validate_search_across_samples` before any per-card query, so the search worst case is now
  `2 x N x 5 x 50 x 2 x 10 s` with a hard 50-card ceiling instead of a page-controlled count.

Regression check for the new, broader regexes - **no over-rejection of legitimate CSS**:
`a[href^="mailto:"]`, `a[href^="tel:"]`, `a[href="https://x/y/z"]`, `[href="/a/b/"]`, `div:not(.ad)`,
`li:nth-child(2)`, `svg|rect`, `p::first-line`, `q:lang(en)`, `a:hover[data-x="y"]`, `:is(h1, h2)`,
`:where(.a,.b)`, `a[title="say \"hi\""]`, and a slashy quoted attr `a[href="x"][data-y="/p/"]` all
**ALLOW**. The engine-prefix regex fires only on a leading `ident(:ident)*=` form (e.g.
`-moz-anything=x`), never on a pseudo-class-then-attribute selector.

One negligible, **safe** quirk (not a finding): the quoted-span blanking uses a non-escape-aware
`"[^"]*"` span, so a contrived selector embedding backslash-escaped quotes around slashes
(`a[t="\"/re/\""]`) desyncs and is over-rejected as `regex-form selector`. This errs only toward
rejection (an availability quirk on a selector no site would use, downgraded with an honest note),
and it **cannot hide** an engine/regex form from detection - the `internal:`, engine-prefix, and
pseudo-engine rules are independent of quote-blanking - so it introduces no security bypass.

### [MEDIUM - RESOLVED in `8429241`] PI-1 (AIS-6) `internal:` Playwright engine prefix evaded `reject_selector`
- Vector / evidence: `reject_selector` (`schema_detection.py:362-406`). `_ENGINE_PREFIX_RE`
  (`:178`, `^\s*[A-Za-z_][\w-]*\s*=`) stops at the first non-`[\w-]` char, so `internal:text="x"`,
  `internal:attr=[name=/.*/]`, `internal:role=`, `internal:label=`, `internal:control=`,
  `internal:testid=` do **not** match an engine prefix (the `:` breaks the pattern before `=`), and
  none except `internal:has-text` contain a `_PLAYWRIGHT_PSEUDO` substring. Confirmed allowed by the
  scratch probe: `internal:text="x"` and `internal:attr=[name=/.*/]` both return `None` (accepted).
  `internal:*` are real Playwright selector engines parsed from user-supplied `page.locator()`
  strings, and `internal:attr=[name=/.../]` carries an attacker-chosen **regex** - the exact
  engine-prefix + ReDoS class AIS-6 exists to block. The 2026-10-02 model's claim that AIS-6 lets
  "only plain CSS" through is therefore slightly overstated.
- Impact: a successfully-injected model could propose an `internal:` selector that runs as a non-CSS
  engine (divergence from the "it's just CSS" assumption the reviewer and `parameters.py` make) and,
  via `internal:attr=[.../regex/.../]`, drive a regex evaluation. No RCE - the only sink is a locator
  query.
- Why not CRITICAL/HIGH - bounded on every axis: (1) AIS-7's `SELECTOR_EVAL_TIMEOUT_S` (10 s per
  `count()`/`text_content()`/`all()`, `schema_detection.py:1192`) turns any ReDoS into a per-call
  miss, never a hang; (2) propose-only (AIS-3) - never written to `parameters.py`; (3) the proposed
  string is rendered verbatim (sanitized) in the report, so a reviewer sees `internal:...`, plainly
  not CSS; (4) the instruction block tells the model to emit plain CSS, so this only fires under an
  injection; (5) cross-sample DOM validation still gates "apply".
- Mitigation (hand to `python-expert`; regression to `testing-expert`): in `reject_selector` reject
  any selector containing `internal:` (and, defensively, any `\bword:...=` engine-ish form), and/or
  reject a `/.../ ` regex **anywhere** in the string rather than only as a `fullmatch`. CWE-20,
  CWE-1333 (ReDoS).

### [LOW - RESOLVED in `8429241`] PI-2 (AIS-1) Fence close-tag with attributes was not neutralized
- Vector / evidence: `_FENCE_TAG_RE` (`schema_detection.py:154`, `<\s*/?\s*page_html\s*>`) requires
  `page_html` immediately followed by `\s*>`. A snapshot token `</page_html foo="x">` is left
  verbatim (confirmed by the scratch probe: "attr close" stays unescaped). It is not the literal
  closing fence `</page_html>`, so it cannot close the real fence, but a model *might* read
  `</page_html ...>` as a close.
- Impact: at worst a weak fence-confusion attempt; bounded by propose-only + DOM validation + the
  AIS-13/15 notice. Note: from a live page `page.content()` serializes stray `<page_html>` text as
  entities, so this only reaches the snapshot verbatim via `--html-file` raw bytes.
- Mitigation (optional, LOW): broaden `_FENCE_TAG_RE` to `<\s*/?\s*page_html\b[^>]*>` so any
  attribute-bearing fence tag is neutralized too. CWE-116.

### [LOW - RESOLVED in `8429241`] PI-3 (AIS-13) Unicode line/paragraph separators not stripped
- Vector / evidence: `_UNTRUSTED_CONTROL_RE` (`schema_detection.py:145`, `[\x00-\x1f\x7f-\x9f]`)
  strips C0/C1/DEL (including ESC `\x1b`, verified) but not U+2028/U+2029 (confirmed: `line sep`
  passes through). In the text report these can act as line breaks in some terminals; `json.dumps`
  emits them literally (valid JSON, but trips some lenient JS parsers).
- Impact: cosmetic report-spoofing only; no escape, no key/PII disclosure.
- Mitigation (optional, LOW): add `  ` to the strip class. CWE-116.

## Per-AIS as-built status

- AIS-1 (fence + un-spoofable): PASS. Snapshot fenced `<page_html>...</page_html>`
  (`build_snapshot_block:336`); instruction block declares the fence untrusted data
  (`build_instruction_block:300-303`); `_neutralize_fence:318` HTML-escapes every bare fence tag -
  case/whitespace/decoy-open/post-prune-split variants all neutralized; entity-encoded
  `&lt;/page_html&gt;` is inert (not decoded by `prune_html`). Residual: PI-2 (attr-bearing close).
- AIS-2 (output untrusted, DOM-validated): PASS. Every proposal re-queried by
  `validate_across_samples`/`validate_search_across_samples`; rationale carried as untrusted text,
  never gates apply.
- AIS-3 (no write to `parameters.py`/`src/`): PASS. Only runtime file writes are the caller's
  `--output` report (`detect_schema.py:73`) and `--record` (`write_record_file:1168`); no `--apply`,
  no write under `src/`. (`parameters.py` edits in the committed range are hand-authored dev changes,
  not tool output.)
- AIS-4 (only sink = locator `count()`/`text_content()`/`all()`): PASS. Grep confirms no
  `page.evaluate`, `eval`, `exec`, `subprocess`, or shell in the module; proposed selectors reach
  only `page.locator(...).count()/.first.text_content()` and the search `locator(card).all()`
  (`:1216-1220`, `:1269-1272`, `:1318`).
- AIS-5 (downgrade, never raise): PASS. Length>300, universal `*`/`html`/`body`/`:root` (incl.
  whitespace/case), `javascript:`, `<`, newline/CR all downgrade to `selector=None`, rationale
  `"rejected: <rule>"` (`validate_ai_response:445-451`). Single `>` child combinator allowed per the
  2026-10-04 amendment - confirmed. CSS-escape (`\3c`) / unicode-lookalike `>>` pass the string check
  but have no sink impact (invalid CSS -> query raises -> recorded miss).
- AIS-6 (reject non-CSS engines/pseudo/regex): PASS WITH FOLLOWUP. `xpath=/text=/css=/id=/
  data-testid=/role=` prefixes (incl. leading-ws, uppercase, `space =`), `>>`, pseudo-engines
  (`:has-text/:has(/:visible/:nth-match/:text(/:text-is(/:light(`, incl. `:is()/:where()` nesting and
  uppercase), and bare `/regex/` all rejected - verified. PI-1 `internal:` engine prefix now also rejected (`8429241`).
- AIS-7 (bounded eval): PASS. `_bounded_eval` wraps every `count()/text_content()/all()` in
  `asyncio.wait_for(..., 10.0)`; timeout -> recorded miss, never a hang. Worst-case figure below.
- AIS-8 (key never logged/printed/reported): PASS. `ANTHROPIC_API_KEY` read once into
  `anthropic.Anthropic(api_key=...)` (`:1035`); SDK errors surfaced as class name + HTTP status only
  (`_sdk_error_summary:948`), never the body; usage lines and text/JSON reports and `--record` carry
  no key.
- AIS-9 (offline keyless/network-free): PASS. `check_current_only` skips model/key/client entirely;
  replay mode builds no client and reads no key (`detect_schema:1437-1451`); `--html-file` loads via
  `set_content` with `_block_all_requests` aborting every request (`:1127`). Note by design: replay +
  live `--url` still fetches pages over the network (Anthropic-free, not network-free); full offline
  = `--html-file` + (`--replay` | `--check-current-only`).
- AIS-10 (token budget / bounded retries): PASS. `_fit_snapshot_to_budget` re-prunes to the 10k
  floor or fails without sending; `MAX_API_RETRIES=1`; `MAX_SAMPLE_URLS=5` hard-caps paid requests
  (enforced pre-launch, applies to `--html-file` too).
- AIS-11 (`--html-file` cap/safe read): PASS. `_read_html_file` enforces 400 KB cap, UTF-8, fails
  loudly on missing/oversized/binary (`:1088-1109`).
- AIS-12 (JSON bounds): PASS. No key, no HTML; snippets bounded to 80 chars at capture; rationale
  truncated to 200; `matches`/`current_matches` are ints only (`format_json_report:724-826`).
- AIS-13 (strip ANSI/control from all untrusted strings): PASS (residual PI-3). `sanitize_untrusted`
  applied to selector (`_fmt_selector`), rationale (`_clean_rationale`), DOM snippets, URLs/labels in
  both text and JSON; ESC stripped, printable remnants kept verbatim - verified.
- AIS-14 (fixture/replay corpus policy): N/A to 09 code; governed by the 06.0 corpus gate
  (`docs/security/2026-09-30-real-html-test-corpus.md`). `write_record_file` carries only a provenance
  note + hash-keyed responses, never the key.
- AIS-15 (untrusted-content footer): PASS. `REPORT_NOTICE` appended to the text report
  (`format_diff_report:575`) and as the JSON `notice` key (`:824`).
- AIS-16 (model-ID validation): PASS. Empty/whitespace model ID rejected (`resolve_model:849`); API
  rejection surfaced as error type, not body.

## Worst-case selector-eval wall time (AIS-7)

Per `count()`/`text_content()`/`all()` call bound = `SELECTOR_EVAL_TIMEOUT_S` = 10 s; each call
degrades to a miss on timeout, so nothing hangs. A detail run validates two passes (AI proposals +
current selectors) x N fields x S<=5 samples x <=2 calls (count + conditional text):

  worst-case detail = 2 x N x 5 x 2 x 10 s = 200N s.

Largest site is amazon (N=11 detail fields) => ~2,200 s (~37 min) absolute worst case - and only
under a deliberate every-field/every-sample pathological-selector attack; a healthy run is seconds.
Judged **acceptable**: it is a local dev/CI tool (not a network service), every call terminates,
AIS-5/6 reject the obvious catastrophic forms, and the sample count is hard-capped at 5.

Caveat to watch (not a finding): the **search** path multiplies by card count C
(`_validate_card_relative_field`, 2 x N x S x C x 2 x 10 s), and C is page-controlled with no explicit
code cap - bounded in practice by real cards on a page (tens), bounded per-call by AIS-7, but there is
no hard ceiling on cards. Add a per-sample card cap if search runs ever go unattended against live
pages.

## Trade-off judgement: AI `:has-text` rejected, current `:has-text` still evaluated

Sound. AI *proposals* using `:has-text`/other pseudo-engines are rejected (AIS-6) because an injected
regex form is a ReDoS vector and diverges from plain CSS; the project's **current** selectors
(`:has-text` on amazon/packtpub/oreilly) are trusted, developer-authored config, so re-querying them
for drift is legitimate - they are not attacker-controlled and are bounded by the same AIS-7 timeout.
The asymmetry (reject untrusted proposals, evaluate trusted current values) is exactly the trust
boundary the design should draw. A human can still apply a `:has-text` selector by hand; a rejected
proposal is shown honestly as `rejected: ...` rather than silently run as non-CSS. Keep it.

## Verdict: PASS (as of commit `8429241`)

No CRITICAL findings and no open findings - subtask 09's success criterion is met and 04.0 may close.
The pre-code gate's MEDIUM/LOW follow-ups (PI-1 `internal:` engine bypass, PI-2 attr-bearing fence
close, PI-3 unicode separators) are all **RESOLVED and re-verified** in `8429241`, and the search
card multiplier is now hard-bounded (`MAX_CARDS_PER_SAMPLE = 50`). The broadened regexes introduce no
over-rejection of legitimate CSS and no new bypass (one safe, negligible escaped-quote over-rejection
quirk noted, not a finding). The propose-only constraint (AIS-3), mandatory cross-sample DOM
validation (AIS-2), the AIS-7 per-eval timeout, and the human reviewer bound every blast radius;
Playwright locators give no RCE. Cross-ref the pre-code model at
`docs/security/2026-10-02-ai-schema-detection.md`.

(Prior verdict before `8429241`: PASS_WITH_FOLLOWUP - the three items below were open residuals.)
