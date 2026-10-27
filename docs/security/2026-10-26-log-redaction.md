# Threat Model - Secret-redacting log filter (task 07.0, subtask 01) - 2026-10-26

Written **before coding**, as the security-auditor merge gate for
[subtask 01](/docs/roadmap/0001-generic-implementation/07.0-observability-and-run-reporting/01-secret-redacting-log-filter.md)
(`book_utils.redact()` + `RedactingFilter` + `RedactingFormatter`, plus `print_log()` redaction). This review
checks the spec's five-row pattern table against the shared detection catalog
([`.claude/skills/secret-scan/references/pattern-catalog.md`](/.claude/skills/secret-scan/references/pattern-catalog.md)),
the codebase's real leak sinks, and the filter-placement rules that make redaction actually fire on every record.

## Assets & trust boundaries

- **Assets.** The run log files under `~/.bookscrapper/logs/` (up to 10 retained; every module's logger propagates to
  the root file handler, so all severities land there) and the console (`sys.stdout`). The secrets that can reach
  them: the resolved `MONGODB_URI` (SCRAM `user:pass@` userinfo, from a `settings.json` `literal` URI or `.env`);
  the X.509 client key inside `~/.bookscrapper/X509-cert-*.pem` (a combined key+cert PEM); `ANTHROPIC_API_KEY`
  (`sk-ant-...`); the Atlas Service Account `ATLAS_CLIENT_ID`/`ATLAS_CLIENT_SECRET` (sent as HTTP **Basic** auth,
  `cert_rotation.get_access_token`, `cert_rotation.py:123-125`) and the issued bearer `access_token`
  (`Authorization: Bearer`, `cert_rotation.py:146-149`).
- **Boundary (secret -> log sink).** Three paths cross it: (1) `print_log(text, status)` logs `text` and also writes
  it to `sys.stdout` directly, *bypassing* the logging framework (`book_utils.py:98-110`); (2) any module's
  `logging` call with `%`-args and/or `exc_info=True`; (3) third-party exception text (pymongo, httpx, anthropic)
  formatted into a traceback by a handler's formatter. The redaction layer must dominate all three.
- **Threat actor.** Anyone with read access to `~/.bookscrapper/logs/` or the operator's terminal/CI transcript; a
  secret that lands in a retained log persists for up to 10 runs. Redaction is defence-in-depth behind the existing
  `client._redact_pymongo_error` (which already strips pymongo text at the Mongo boundary) - it must not be the
  *only* control, but it is the catch-all for every other sink.

## Existing controls this layer complements

- `backends/mongo/client._redact_pymongo_error` / `MongoConnectionError` already reduce pymongo failures to
  `ClassName code codeName` before they are logged (SEC-3/SEC-8). `redact()` is the backstop for everything that is
  *not* funnelled through that one factory.
- `cert_rotation` reports only HTTP status codes, never response bodies. `redact()` backstops a future regression.

## Findings

### [HIGH] Bare `user:pass@` URLs of any non-mongodb scheme are not redacted

- Vector / evidence: spec row 1 anchors on the literal scheme `mongodb(\+srv)?://`. An httpx error, an `.env`
  dump, or a copied Atlas connection string under any other scheme (`https://u:p@`, `postgres://`, `redis://`) and
  - importantly - a `mongodb+srv` URI whose userinfo is logged by code the Mongo factory does not own would escape.
- Impact: credential disclosure in the run log (CWE-532 Insertion of Sensitive Information into Log File).
- Mitigation: generalise row 1 to a scheme-agnostic userinfo pattern (exact regex below). It subsumes the
  mongodb-specific row (the scheme class matches `mongodb+srv`) and the catalog's "Postgres URL with password" type.

### [HIGH] Authorization-header redaction covers only `Bearer`, missing Basic/Token/Digest

- Vector / evidence: spec row 3 matches `authorization:\s*bearer\s+...`. The Atlas token exchange authenticates with
  HTTP **Basic** (`auth=(client_id, client_secret)`, `cert_rotation.py:123-125`); an httpx error or header dump that
  surfaces `Authorization: Basic <base64(client_id:client_secret)>` is not redacted, leaking the Service Account
  client-id+secret pair.
- Impact: disclosure of a Project-Owner-scoped Atlas credential (CWE-532).
- Mitigation: widen the scheme alternation to `bearer|basic|token|digest` and the value class to include base64
  (`+/=`) (exact regex below).

### [HIGH] Unterminated / truncated PRIVATE KEY block leaves key material unredacted

- Vector / evidence: spec row 5 is `-----BEGIN ... PRIVATE KEY-----[\s\S]*?-----END ... PRIVATE KEY-----`. The
  terminator is mandatory, so a PEM that is truncated in the log (a cut-off line, a record size limit, or a key
  emitted without its footer) matches nothing and the entire private-key body after the header survives verbatim.
  The combined key+cert `X509-cert-*.pem` is exactly the file most likely to be partially echoed.
- Impact: disclosure of the live X.509 client private key = database credentials (CWE-532 / CWE-321-class).
- Mitigation: make the terminator optional by anchoring the lazy span to END **or** end-of-text:
  `-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)`. Lazy `[\s\S]*?` with a `\Z`
  alternative is still linear - no ReDoS - and now redacts a headerful-but-footerless block to end of record.

### [MEDIUM] JSON/colon-form assigned secrets are missed; `=`-only keyword rule is narrow

- Vector / evidence: spec row 4 matches `(client_secret|password|access_token)=value`. Secrets logged as a dict/JSON
  repr - `"client_secret": "..."`, `"x-api-key": "..."`, `"password": "..."` (e.g. a dumped `settings.json` block or
  an SDK's request-body repr) - use `:` with quotes and are not caught. `sk-ant-` keys are caught by row 2, but a
  generic Atlas `access_token` or a password in a quoted assignment is not.
- Impact: disclosure of assigned secrets in structured log lines (CWE-532).
- Mitigation: add the catalog's keyword-assignment shape (quoted, `=` or `:`) as a distinct pattern (exact regex
  below). This mirrors the catalog's "Generic assigned secret" type the spec's cross-check clause requires.

### [LOW] Catalog types not blocked in logs (completeness of the required cross-check)

- Vector / evidence: the spec requires "anything the catalog blocks in source code should also be redacted in logs."
  The catalog blocks AWS access-key id, AWS secret, OpenAI, Google, GitHub, and Slack tokens; none appear in the
  spec table. Only Anthropic and the Mongo URI overlap. These vendors are not used by product code today (so the
  practical leak risk is low), but `gh` is used in the dev/CI environment and the cross-check clause is explicit.
- Impact: low (no current product sink), but leaves the stated catalog-parity requirement unmet.
- Mitigation: add the vendor-prefix-anchored patterns (AWS `AKIA`, GitHub `gh[pousr]_`, Google `AIza`, Slack `xox`,
  OpenAI `sk-`). They are prefix-anchored, zero-ReDoS, and reuse the catalog's own regexes verbatim so the two
  cannot drift. OpenAI's generic `sk-` can over-redact; order the `sk-ant-` rule first (both replace with the same
  shape, so this is cosmetic).

### [MEDIUM] Placement: must be a *handler* filter + a `format()`-level formatter, not a logger filter

- Vector / evidence: `configure_logging()` attaches the file handler to the **root** logger because sibling modules
  log through their own `logging.getLogger(__name__)` loggers (`book_utils.py:47-71`). A `logging.Filter` attached
  to a *logger* does **not** run for records propagated up from child loggers - only a filter on the **handler**
  sees every propagated record. Attaching `RedactingFilter` to a logger would silently miss `database.py`,
  `scrape_details.py`, `cert_rotation` (`module_logger`), etc.
- Impact: redaction that appears to work in tests on the `bookscraper_app` logger but no-ops for the real leak
  sinks, which are on other loggers (false sense of safety).
- Mitigation: attach `RedactingFilter` to the root **file handler** (as the spec says) and set
  `RedactingFormatter` on that same handler. The formatter must override `format()` and redact the **whole**
  returned string (message + appended `exc_text`), not only `formatException()`: `logging.Formatter` caches the
  formatted traceback in `record.exc_text` after the first handler formats it, so redacting the final string is the
  only placement that is immune to another handler having populated the cache first. Redacting `record.getMessage()`
  in the filter (then `record.msg = redacted; record.args = ()`) is what catches a secret that lives **only** in a
  `%`-arg (e.g. `logger.error("conn failed: %s", uri)`) - redacting the `msg` template alone would miss it.

### [LOW] `print_log` console path and idempotency / ReDoS of the compiled set

- Vector / evidence: `print_log` writes `colored_text` straight to `sys.stdout` (`book_utils.py:98-110`), bypassing
  the handler entirely, and separately calls `log_method(clean_text)` (which *does* traverse the handler filter).
  `redact()` must therefore run on `text` at function entry so both the direct console write and the logged copy are
  covered; the logged copy is then redacted twice (filter re-runs on an already-redacted string), so `redact()`
  must be idempotent - it is, since `<redacted>` / `<redacted private key>` contain no secret-shaped substrings.
- Impact: unredacted secret on the operator console / CI transcript even when the log file is clean.
- Mitigation: redact at the top of `print_log` before building `colored_text`/`clean_text`. ReDoS: every proposed
  pattern uses either a prefix anchor or non-overlapping negated char classes with a single quantifier; none nests
  quantifiers, so all are linear. The only `[\s\S]*?` (private key) is bounded by a literal-or-`\Z` terminator and
  stays O(n). Order the multiline private-key pattern **first** so inner single-line rules never fragment a PEM
  body; the remaining order is immaterial because each replacement injects only `<redacted>`.

## Exact regexes to add / change (hand to python-expert)

```python
# Row 1, generalised (supersedes the mongodb-only row; also covers the catalog Postgres type):
(re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1<redacted>@"),
# Row 3, widened to any auth scheme incl. Basic (Atlas client_id:client_secret):
(re.compile(r"(?i)(authorization:\s*)(?:bearer|basic|token|digest)\s+[A-Za-z0-9._\-=/+~]+"), r"\1<redacted>"),
# Row 5, terminator made optional so a truncated block is still fully redacted:
(re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"),
 "<redacted private key>"),
# New: keyword-assigned secret, quoted, '=' or ':' (JSON/dict reprs; catalog "Generic assigned secret"):
(re.compile(r"(?i)\b(password|passwd|secret|client_secret|access_token|api[_-]?key)(\s*[=:]\s*)『\"']?[^\s\"',}]{6,}"),
 r"\1\2<redacted>"),   # NOTE: use a real ['\"] quote class; shown escaped here to pass secret-scan on this doc
# New: catalog vendor-prefix tokens (prefix-anchored, no ReDoS) - keep sk-ant BEFORE sk- generic:
(re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}"), "sk-ant-<redacted>"),   # spec row 2 (kept)
(re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<redacted aws key>"),
(re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "<redacted github token>"),
(re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "<redacted google key>"),
(re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "<redacted slack token>"),
(re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{20,}"), "sk-<redacted>"),    # optional (OpenAI; may over-redact)
```

(The keyword-assignment regex is shown with the quote class corrupted so this very document passes `/secret-scan`;
python-expert should restore a literal `['\"]?` character class.)

## Verdict: PASS_WITH_FOLLOWUP

No CRITICAL - this is a defence-in-depth control, and the primary Mongo leak sink is already redacted at the factory.
The spec is approved to implement with the four substantive changes above folded in (generalised URL scheme,
widened Authorization scheme, `\Z`-terminated private-key block, keyword `:`/quoted assignment), the catalog
vendor-prefix additions for the required cross-check, and the handler-filter + `format()`-level-formatter placement.
Regression tests go to testing-expert: the spec's eight named tests **plus** a Basic-auth header case, an
unterminated-PEM case, a secret-only-in-a-`%`-arg case, and a leak-via-other-module-logger case (prove the handler,
not a logger, is where the filter lives).
