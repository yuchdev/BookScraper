# Milestone 0001 - Generic Implementation - Status

Tracks progress against [plan.md](/docs/roadmap/0001-generic-implementation/plan.md). Updated by the
`implement-subtasks` loop with a targeted row edit as each task lands.

## Current status

| Task | Name                               | Status         | Subtasks done | Tests                                                                                                  |
|------|------------------------------------|----------------|---------------|--------------------------------------------------------------------------------------------------------|
| 01.0 | Storage backend abstraction        | ✅ Complete    | 8 / 8         | `tests/unit/backends/local/test_store.py`, `tests/mock/backends/test_storage.py`, `tests/mock/backends/test_backend_parity.py`                       |
| 02.0 | MongoDB auth configuration         | ✅ Complete    | 6 / 6         | `tests/unit/backends/mongo/test_config.py`, `tests/mock/backends/mongo/test_database.py`, `tests/integration/test_connection_settings.py`, `tests/mock/backends/mongo/test_client.py` |
| 02.1 | X.509 certificate rotation         | ✅ Complete (08 blocked) | 9 / 10        | `tests/unit/backends/mongo/test_cert_rotation.py`, `tests/mock/backends/mongo/test_cert_rotation.py`, `tests/mock/commands/test_rotate_cert.py` |
| 03.0 | Scraping engine hardening          | ✅ Complete    | 6 / 6         | `tests/mock/scraping/test_scrape_details.py`, `tests/unit/scraping/test_{results,text}.py`, `tests/unit/test_no_bare_print.py`, `tests/mock/commands/test_{scrape_urls,search}.py` |
| 03.1 | Canonical book data model          | ✅ Complete    | 6 / 6         | `tests/unit/test_{models,isbn,import_direction}.py`, `tests/unit/scraping/test_normalize.py`, `tests/unit/backends/test_migration.py`, `tests/mock/commands/test_migrate.py`, `tests/mock/backends/test_backend_parity.py` |
| 03.2 | Politeness & anti-bot resilience   | ✅ Complete    | 6 / 6         | `tests/unit/scraping/test_politeness.py`, `tests/mock/scraping/test_browser.py`, `tests/mock/scraping/test_{scrape_details,search_utils}.py`, `tests/mock/commands/test_{scrape_urls,search}.py` |
| 03.3 | Multi-site search pipeline         | ⬜ Not started | 0 / 6         | -                                                                                                      |
| 04.0 | AI-assisted schema detection       | ✅ Complete    | 9 / 9         | `tests/unit/scraping/test_schema_detection.py`, `tests/mock/scraping/test_schema_detection.py`, `tests/mock/commands/test_detect_schema.py` |
| 04.1 | LLM extraction fallback            | ⬜ Not started | 0 / 5         | -                                                                                                      |
| 05.0 | CLI improvements                   | ✅ Complete    | 8 / 8         | `tests/unit/test_{cli,runtime,cli_docs}.py`, `tests/mock/commands/test_{scrape_urls,search,rotate_cert,detect_schema,migrate,doctor,exit_contract}.py` |
| 06.0 | Real-HTML mock test corpus         | ✅ Complete    | 7 / 7         | `tests/unit/test_fixture_corpus.py`, `tests/mock/scraping/html/`, `tests/unit/scraping/test_{sanitize,fixture_drift}.py` |
| 06.1 | 90% coverage gate                  | 🔶 In progress | 1 / 6         | whole suite: 344 passed, 95% total (2026-09-24)                                                        |
| 06.2 | Continuous integration             | ⬜ Not started | 0 / 6         | -                                                                                                      |
| 07.0 | Observability & run reporting      | ⬜ Not started | 0 / 4         | -                                                                                                      |
| 08.0 | Repository hygiene & documentation | ⬜ Not started | 0 / 6         | -                                                                                                      |

**Legend:** ✅ Complete · 🔶 In progress / partial · ⬜ Not started

**Totals:** 66 / 99 subtasks complete.

## Task 01.0 - Storage backend abstraction

Delivered before this milestone was written down: the `StorageBackend` contract, `JsonBackend` (persistent
`books.json` with hash uniqueness), `MongoBackend` (delegating to `database.py` / `deduplicate.py`), and the
required `--store-backend` flag with a pre-flight check. Open: `save_books_to_mongodb` continues after a `None`
collection and returns nothing; the JSON store writes non-atomically and re-reads the file per lookup; no
indexes on `asin` / `book_id` / `slug`; no cross-backend parity test. All four are now closed (below).

### Task 01.0 - Storage backend abstraction (✅ 2026-09-29)

**Delivered** (commits `d396fcf`..`69f1448` on `milestone/0001-generic-implementation`):

- **05** `SaveSummary(inserted, duplicates, errors)` returned by both backends. `save_books_to_mongodb` returns
  immediately on a `None` collection, writes with one `insert_many(ordered=False)` over copies of the caller's
  dicts, pre-validates BSON per document, and splits `BulkWriteError` into duplicates (11000) and errors. Both
  scraping commands log the summary.
- **06** `ensure_indexes()` adds sparse, non-unique `asin` / `book_id` / `slug` lookup indexes next to the unique
  `hash` index. `ensure_unique_index_on_hash` is kept as a deprecated alias.
- **07** Shared `book_utils.atomic_write_bytes` (also used by `cert_rotation`, mode `0600`): temp file, fsync,
  replace, temp cleanup on failure, and existing-mode preservation. `books.json.bak` holds one generation, and
  `LocalStoreCorruptError` refuses to overwrite an unparseable store (the json pre-flight exits on it).
  `JsonBackend` keeps an in-memory hash/asin/book_id/slug index, so the file is parsed once per process.
- **08** Parity suite (`tests/mock/backends/test_backend_parity.py`, mongomock 4.3.0 dev dependency;
  [dep audit](/docs/reviews/2026-09-29-dep-audit.md) clean). It found and fixed one divergence: the Mongo path
  stored a hash-less book (the unique index treats a missing field as `null`), where the JSON store rejects it.

**Tests / gate:** 344 → 382 passed (8 integration deselected), 95% total. `database.py`, `store.py` and
`storage.py` are at 100%. `/verify-subtask` PASS ×4. `/pr-review`: feature LGTM and security PASS, so APPROVE;
its three non-blocking suggestions are applied in `69f1448`.

**Reconciliation note:** 08's "deliberately break `store.leanpub_book_exists`" probe no longer reaches the parity
suite, because 07 moved `JsonBackend` lookups onto its in-memory index. Breaking `JsonBackend.leanpub_book_exists`
does fail the suite, and the store function stays covered by its own unit test. All of 05-08 are *consumed*.
The 06 success criteria (`getIndexes()` and `explain()` showing `IXSCAN` on a live cluster) are still manual
checks, pending Atlas access.

## Task 02.0 - MongoDB auth configuration

Delivered: the deterministic `settings.json` resolver (six valid shapes), the legacy env fallback with a
newest-cert glob, and the X.509 mechanism diagnostic. Open: the unconditional "connection successful" log, two
divergent `MongoClient` construction paths, and `settings.json` permissions / namespace / unknown-key validation.
All three are now closed (below).

### Task 02.0 - MongoDB auth configuration (✅ 2026-09-30)

**Delivered** (commits `7303a68`..`35b6b83`; threat model
[2026-09-30-mongodb-auth-configuration.md](/docs/security/2026-09-30-mongodb-auth-configuration.md)):

- **04** `get_mongo_collection()` prints its success line only when the collection initialised, naming just
  `<db>.<collection>`. The CLAUDE.md known-inconsistency bullet is gone.
- **05** New `backends/mongo/client.py` is the only place a `MongoClient` is built: `ServerApi("1")`, certificate
  validation always on, TLS/CA kwargs only when the files are given, and a missing cert file is an error.
  `ping()` and constructor failures re-raise `MongoConnectionError` carrying only the exception class and
  `code`/`codeName` (Atlas 8000 still reads `OperationFailure 8000 AtlasError`). `database.py` and
  `book_utils.check_mongodb_connection` both use it, and the pre-flight now honours `TLS_CA_FILE`.
  `MONGODB_SERVER_SELECTION_TIMEOUT_MS` overrides the 10 s default. This closes the threat model's two HIGH
  findings.
- **06** `load_settings()` warns once per process, on the console and in the run log, when `settings.json` is
  group/other-readable, a symlink, or foreign-owned (the last two are the user-ratified SEC-9 extension; POSIX
  only, never aborts). The optional `database` block is resolved by `resolve_mongo_namespace()`; a present block
  must define both `name` and `collection`, and env vars only apply in legacy mode. Unknown top-level keys raise
  `ConfigError` naming only the keys. Adds `docs/examples/settings.example.json`.

**Tests / gate:** 382 → 422 passed (8 integration deselected; all 8 still collect against the new APIs), 96%
total. `config.py`, `client.py` and `database.py` are at 100%. `/verify-subtask` PASS ×3, `/secret-scan` clean.
`security-auditor` verified the subtask-05 fix and the task close (PASS, no findings above LOW). `/pr-review`:
feature LGTM and security PASS, so APPROVE; four non-blocking suggestions are applied in `35b6b83`.

**Reconciliation note:** all of 04-06 are *consumed*. Two changes go beyond the specs' literal wording and are
recorded here. First, 06's warnings go through `print_log`, not only the logger, because the root logger has no
console handler. Second, `build_mongo_client` also redacts `MongoClient` constructor errors, which can embed the
URI (SEC-8). Deferred as not worth the change now: `resolve_mongo_connection()` and `resolve_mongo_namespace()`
each parse `settings.json`, so it is read twice per connection.

## Task 02.1 - X.509 certificate rotation

Delivered: `bookscraper rotate-cert` issues a cert via the Atlas Admin API using `ATLAS_*` Service Account
credentials, writes it atomically `0600`, and repoints `settings.json` for literal cert references. Open: expiry
inspection (`--check`), retention/pruning, verify-before-repoint, a recorded live re-diagnosis of the unverified
Atlas-8000 claim (so the inert `xfail(strict=False)` markers can be removed or made strict), and an operator
runbook. All are now closed except the live re-diagnosis (below).

### Task 02.1 - X.509 certificate rotation (✅ 2026-09-30, subtask 08 blocked)

**Delivered** (commits `eb88f4e`..`6ea5708`; threat model
[2026-09-30-x509-certificate-rotation.md](/docs/security/2026-09-30-x509-certificate-rotation.md)):

- **06** `read_certificate_expiry()` parses only the first `CERTIFICATE` block of the combined PEM, using the new
  direct dependency `cryptography` 50.0.1 ([dep audit](/docs/reviews/2026-09-30-dep-audit.md) clean; MIT-0 added
  to the license allow list). `rotate-cert --check [--warn-days N]` exits 0/3/1 with no Atlas contact. The mongo
  pre-flight warns when the cert is close to expiry, and a successful rotation prints the new expiry date.
- **09** `verify_certificate()` pings with the new cert through the shared redacted client factory before
  `settings.json` is repointed. On failure there is no repoint, the new PEM is kept, and the command exits 1.
  New flags `--no-verify` and `--verify-attempts`. `rotate_certificate()` now returns
  `(path, lines, verified)`.
- **07** `prune_old_certificates()` keeps the newest `--keep N` (default 2) plus a canonicalised protected set,
  unlinks the matched path rather than a symlink target, and skips entirely on an unresolvable active cert, after
  `--no-verify`, or after a failed verification. `--no-prune` disables it. Prune tests assert `CONFIG_DIR` is not
  the real `~/.bookscrapper`.
- **10** [Operator runbook](/docs/runbooks/x509-certificate-rotation.md), placeholders only, linked from CLAUDE.md
  and `docs/README.md`.

**Tests / gate:** 422 → 488 passed (8 integration deselected, all still collect), 96% total. `cert_rotation.py`,
`rotate_cert.py`, `cli.py` and `storage.py` are at 100%. A full suite run leaves `~/.bookscrapper/` unchanged.
`/verify-subtask` PASS ×4, `/secret-scan` clean, `security-auditor` task close PASS (every HIGH closed).
`/pr-review`: feature LGTM and security PASS, so APPROVE. The fix in `6ea5708` stops pruning from crashing on a
dangling `X509-cert-*.pem` symlink or a file that vanishes mid-prune, both of which happen after a successful
rotation.

**Deferred:** **08 (live X.509 re-diagnosis) is blocked.** This machine has no Atlas credentials, so the
`xfail(strict=False)` markers stay (requirement 4) and the runbook's Atlas-8000 row stays marked UNVERIFIED. It
is still open under the milestone definition of done. Non-blocking review suggestions, not applied:
- `rotate-cert --check` silently ignores rotation-only flags. Revisit with 05.0's CLI work.
- "No superseded certificates to prune" reads the same whether nothing was old enough or everything old was
  protected.

**Reconciliation note:** 06, 07, 09 and 10 are *consumed*. 08 is *open (blocked)*. The one spec departure is in
07: `--no-verify` also suppresses pruning, a more conservative choice than the spec wording, documented in
CLAUDE.md and the runbook.

## Task 03.0 - Scraping engine hardening

### Task 03.0 - Scraping engine hardening (✅ 2026-10-05)

**Delivered** (commits `0a6b71d`, `3ab4457`, `8dbae9a`, `a553807`, `f333bce`, `afc99d7`, review fixes `0762123`):
- **01** each attempt owns one page (`_attempt_page`), closed exactly once; Leanpub opens no page.
- **02** typed `ScrapeResult` / `ScrapeStatus` (`scraping/results.py`) on every path; `failed_books.csv` gains an
  `error` column; `UNKNOWN_ERROR` gone; per-status run summaries.
- **03** neutral field init, explicit per-read timeouts, missing title → `PARSE_ERROR` without retry, only transient
  Playwright/network errors retried (`_is_transient_error`); a missing `BASE_URL` no longer raises. Deviations
  (accepted): `tags=[]` init moved to 04; one classify-then-retry boundary instead of typed `except` clauses.
- **04** `TAGS` (deduped, capped at 50), `DESCRIPTION_ALT` fallback, Amazon `DETAILS_BUTTON` expander, one
  `_selector()` helper; `CONSUMED_DETAIL_KEYS` guard test against dead detail selectors.
- **05** `scraping/text.html_to_text`; one pooled `httpx.AsyncClient` per search run; Leanpub errors report status /
  class name / "invalid JSON" only — never a response body; missing data or title → `PARSE_ERROR`.
- **06** no bare `print()` in `src/` (AST guard test); scrape log lines carry the URL.

**Golden diffs (all reviewed, intended):** 9 packtpub/oreilly pages `FAILED` → real records and their 404s →
`NOT_FOUND`; amazon missing-title/bot-wall → `PARSE_ERROR` (bot-wall detection is 03.2/04); 16 records gain `tags`;
Leanpub null-about → record, Leanpub 404 → `PARSE_ERROR`.

**Review** (`feature-reviewer` REQUEST_CHANGES → fixed; `security-auditor` PASS_WITH_FOLLOWUP, no CRITICAL/HIGH):
fixed in `0762123` — `failed_urls.csv` dropped every row (missing `title` fieldname), CSV formula injection
(`book_utils.csv_safe_cell`), blocking `save_books` on the event loop, null Leanpub slug. **Open LOW follow-ups:**
cap page-controlled text/body sizes (→ 03.1/05 save-boundary validation); strip query strings from URLs and prefer
the exception class name in run-log lines (→ 07.0/01 secret-redacting log filter).

**Coverage:** whole suite 872+ passed, 99% total; `scrape_details.py` 98% (closes the deferred 06.1 item),
`results.py` / `text.py` / `commands/search.py` 100%.

## Task 03.1 - Canonical book data model

### Task 03.1 - Canonical book data model (✅ 2026-10-11)

**Delivered** (commits `3b1df07`, `8e5050f`, `f9fdbf6`, `a45b983`, `651dc19`, `8771f82`, review fixes `2267004`):
- **01** `models.py`: `SCHEMA_VERSION = 2`, frozen `BookRecord` (incl. `tags` and the reserved
  `ai_extracted_fields`), `to_document`/`from_document`; [book-record-schema.md](/docs/scraping/book-record-schema.md).
- **02** `isbn.py`: cleaning, checksum validation, 10→13 conversion, `normalize`, `extract_first` (stdlib only).
- **03** `book_utils.normalize_date`: ISO date at its real precision, locale-independent English month table;
  `extract_year_from_date` keeps every prior result (hash stability).
- **04** `scraping/normalize.py` is the only builder of book documents; scrapers emit v2. Leanpub `site` →
  `leanpub`, human-facing `url`, `book_id` → `source_id`; dedup matches `book_id` OR `source_id` in both backends
  (+ mongo `source_id` index). Every real-book golden hash unchanged.
- **05** `models.validate_document` gates both backends' `save_books` (rejected docs count as
  `SaveSummary.errors`), with size caps on page-controlled text (closes the 03.0 LOW item).
- **06** `bookscraper migrate --store-backend {mongo|json} [--apply]`: dry run by default; guarded per-document
  `ReplaceOne` batches (`schema_version != 2`) or the atomic JSON write; every hash recomputed, changed hashes and
  collisions reported (collisions left at v1); idempotent; transform injected by `commands/migrate.py`, so
  `backends/` never imports `scraping/` (AST guard test).

**Decisions:** hash policy "dedup + migrate" (2026-10-06, user-ratified; see below).

**Review:** `feature-reviewer` LGTM; `security-auditor` PASS_WITH_FOLLOWUP (no CRITICAL/HIGH). Fixed in `2267004`:
redacted handling of a failed bulk write (report still written, exit 1), non-string dedup identifiers ignored
(CWE-943) in all three lookup paths, `MAX_AUTHORS = 50`, sanitized migrate summary (`scraping/safe_text.py`),
report written `0o600`, empty `url` rejected, convergent-hash test. **Open INFO:** `migrate` loads the whole
collection into memory before planning (operator-run tool, acceptable for now).

**Coverage:** 984 passed, 99% total; `models.py`, `isbn.py`, `migration.py`, `migrate.py` 100%.

## Task 03.2 - Politeness & anti-bot resilience

### Task 03.2 - Politeness & anti-bot resilience (✅ 2026-10-11)

**Delivered** (commits `209fb65`, `f4e0b21`, `b8731b2`, `38b8d79`, `0d0097d`, `0da464c`, review fixes `c4276ca`):
- **01** `scraping/politeness.py` (no Playwright/httpx imports, injectable clock): `RateLimiter` token bucket,
  `DomainRateLimiters` keyed by registrable host; per-site `RATE_LIMITS` in `parameters.py`.
- **02** `RobotsCache` fetches each host's robots.txt once per run via an injected fetcher, fails closed on an
  error/5xx, treats 404 as allow-all, honors `Crawl-delay`; disallowed URLs become `SKIPPED_ROBOTS` and are never
  fetched. New `--ignore-robots` flag (warns once).
- **03** `politeness.retry_async` (full-jitter exponential backoff, `Retry-After` override capped at `max_delay`)
  replaces the hand-rolled retry loop and random sleep; HTTP 429/502/503/504 now retried.
- **04** `BOT_WALL` detection rules per site (+ a generic Cloudflare rule) → `ScrapeStatus.BLOCKED`, never retried;
  `politeness.CircuitBreaker` opens per domain after 3 consecutive blocks for 900s; `detect-schema` refuses a
  bot-wall sample page.
- **05** `scraping/browser.py`: one `BrowserContext` per scrape attempt, closed with its page; `USER_AGENT_MODE`
  defaults to `"native"` (no identity override - the honest default); `"rotate"` matches the installed Chromium's
  major version. `set_extra_http_headers("User-Agent")` removed everywhere.
- **06** `politeness.bounded_gather` (order-preserving, per-item exception capture) replaces fixed-size batching
  and the inter-batch sleep in both `scrape-urls` and `search`; default concurrency 4 (05.0 wires a CLI flag).

**Review:** `feature-reviewer` REQUEST_CHANGES → fixed; `security-auditor` PASS_WITH_FOLLOWUP (no CRITICAL/HIGH).
Fixed in `c4276ca`: a per-iteration BrowserContext/Page leak in the Amazon multi-page search loop (05/06
interaction - only the last page's context was closed); `scrape_urls` now builds its own `DomainCircuitBreakers`
per run instead of the module singleton; `result.status` access guarded by `isinstance(ScrapeResult)`; a dead page
after a navigation error is skipped; the robots.txt fetch is now capped at 512 KB, streamed (security LOW). Both
reviews confirmed the "identify and back off, never disguise" boundary holds: UA rotation (opt-in) only ever
matches the real installed browser, the circuit breaker cannot be bypassed by page content, and no response body
or header reaches a log.

**Coverage:** 1048 passed, 99% total; `politeness.py` and `browser.py` fully covered by their dedicated test files.

## Task 05.0 - CLI improvements

### Task 05.0 - CLI improvements (✅ 2026-10-14)

**Delivered** (commits `ba37431`, `d2d855c`, `2312ec2`, `d4359f7`, `6f6da45`, `f27baca`, `dc40f0f`, `f22fd08`,
review fixes `b6e1c53`):
- **01** Shared `_logging_parent()`/`_store_parent()`/`_scrape_runtime_parent()` in `cli.py` replace 4 copies of
  `--log-severity` and 2 of `--store-backend`; `STORE_BACKENDS` shared with `backends/storage.py`.
- **02** `search` targeting: `--site` (repeatable), `-q`/`--query` (repeatable), `--queries-file`,
  `--max-search-pages` (now actually caps Leanpub pagination), `--no-filters` (parsed only - task 03.3/04
  consumes it). `resolve_search_targets()` is the single place defaults apply.
- **03** `--headless`/`--no-headless`, `--concurrency` (1-32), `--nav-timeout`, `--ignore-robots` via new
  `runtime.py` (`RuntimeOptions`); `HEADLESS_BROWSER` flips to `True` (a scheduled/CI run must never open a
  window).
- **04** `--dry-run` (reads for dedup, never saves), `--output-dir` (all diagnostic CSVs), `--json-store`
  (`JsonBackend(path=...)`, usage error with `--store-backend mongo`).
- **05** New `exitcodes.py` (`OK`/`FATAL`/`USAGE`/`PARTIAL`/`DRIFT`/`INTERRUPTED`); every `commands/*.run()`
  returns a code, `main_sync()` is the sole `sys.exit` call site; `resolve_store_backend` raises
  `BackendUnavailableError` instead of exiting; Ctrl+C → one warning line + exit 130, no traceback.
- **06** `--version`/`-V` from installed package metadata; example invocations in the top-level epilog; fixed the
  stale "against MongoDB" search description.
- **07** `bookscraper doctor [--online]`: Python/package version, Chromium executable, `.env` presence,
  settings.json mode, client cert + expiry, JSON store, log dir, `ANTHROPIC_API_KEY`/`ATLAS_*` presence, and
  (online) a MongoDB ping - all offline-safe by default, never printing a secret value or full URI.
- **08** `docs/cli.md` (one section per subcommand) with a guard test asserting every parser flag is documented;
  CLAUDE.md/README trimmed to a short example list linking to it.

**Review:** `feature-reviewer` LGTM; `security-auditor` PASS_WITH_FOLLOWUP (no CRITICAL/HIGH/MEDIUM). Fixed in
`b6e1c53`: `doctor` now reports a clean FAIL instead of a raw traceback on a malformed `settings.json`; the
sys.exit AST guard also catches a stray `raise SystemExit(code != 2)`; `--no-filters` documented as not yet
active; an empty CSV now returns `FATAL` instead of crashing with `EmptyDataError`.

**Coverage:** 1150 passed, 99% total.

## Task 04.0 - AI-assisted schema detection

Delivered per [ADR 0002](/docs/adr/0002-ai-assisted-schema-detection.md): Playwright load → prune → Claude proposal →
cross-sample DOM validation → diff report, propose-only and fail-loudly. Open: current model default plus a token
budget and prompt caching, search-page support, offline `--html-file`/`--replay` mode with golden tests, a JSON
report with a drift exit code, and prompt-injection hardening. All are now closed (below).

### Task 04.0 - AI-assisted schema detection (✅ 2026-10-04)

**Delivered** (commits `6de5f6c`..`19bcc8f`; threat models
[2026-10-02-ai-schema-detection.md](/docs/security/2026-10-02-ai-schema-detection.md) and
[2026-10-04-schema-detection-prompt-injection.md](/docs/security/2026-10-04-schema-detection-prompt-injection.md)):

- **05** `claude-sonnet-5` default (`--model` > `ANTHROPIC_MODEL` > default), `max_tokens` 16000, and
  max_tokens/refusal/context-window stop reasons fail loudly. A count_tokens budget (40k; re-prune to a 10k-char
  floor, else no send), ≤ 5 URLs and 1 retry, one request per URL merged first-non-null, a usage line without
  prices, and redacted SDK errors.
- **06** `--page-type search` with card-relative validation: per-sample card counts and match fractions, under
  80% flagged partial. `NON_SELECTOR_SEARCH_KEYS` is shared with the capture script.
- **07** Offline `--html-file` (all requests aborted, 400 KB cap) and `--replay`/`--record` (no key, no client).
  Goldens cover amazon/packtpub/oreilly detail plus amazon search, from hand-authored `_provenance` replays. The
  prune-regression check is clean on all 10 detail fixtures.
- **08** `--format json` (stable schema, per-field `current_matches` + `status`) and `--check-current-only` (no
  AI, no key). Exit codes 0/4/1, with the ratified drift rule (`_ALT` fallback + `OPTIONAL_SELECTOR_KEYS`).
  ANSI/control characters are stripped from both reports.
- **09** The `<page_html>` fence with breakout neutralization. `reject_selector` downgrades unsafe AI proposals
  (length, universal, `javascript:`, `<`, newline, engine prefixes incl. `internal:`, `>>`, pseudo-engines,
  regex). Evaluation is time-bounded at 10 s/call, search caps at 50 cards per sample, and both reports carry a
  review notice.

**Tests / gate:** 661 → 816 passed with Chromium (765 + 52 skipped without it), 98% total, and
`schema_detection.py` / `detect_schema.py` / `cli.py` at 100%. `/verify-subtask` PASS ×5.
`security-auditor`: no CRITICAL findings; PI-1..3 found, fixed and re-verified (PASS). `/pr-review`: security
PASS; feature REQUEST_CHANGES on one item (invisible U+2028/2029 bytes in the AIS-13 regex), fixed in `19bcc8f`,
so APPROVE.

**Reconciliation note:** 05-09 are *consumed*. Spec amendment: 09's "no `<` / `>`" rule now allows a single `>`
(the CSS child combinator). Orchestrator calls recorded above: one request per URL, and caching left in place
below Sonnet 5's 1024-token minimum.

**Open follow-ups (non-blocking):**
- consider splitting `schema_detection.py` (~1,570 lines) into prompt / validate / report / client / io modules
  before it reaches 2,000 lines;
- drift is count-based, so empty elements pass;
- the CLI has no Chromium executable-path option (→ 05.0/03);
- an `HEADLESS_BROWSER=False` override for unattended runs;
- an end-to-end `--record` → `--replay` round-trip test.

## Task 06.0 - Real-HTML mock test corpus

### Task 06.0 - Real-HTML mock test corpus (✅ 2026-10-02)

**Delivered** (commits `ec37a9c`..`99b1bf3`; threat model
[2026-09-30-real-html-test-corpus.md](/docs/security/2026-09-30-real-html-test-corpus.md); maintainer runbook
[html-fixtures.md](/docs/test/html-fixtures.md)):

- **01** `scraping/sanitize.py`, a stdlib-parser sanitizer with no new dependency. Its hand-written fail-closed
  selector engine supports Playwright `:has-text()` and raises on anything unsupported. It does text-only prose
  neutralization (the public-repo policy), strips tracking, signed-URL and token params (`dib`), drops JSON-shaped
  and correlation `data-*`, and has an `assert_clean` that scans the bytes, the concatenated text and a
  whitespace-stripped view. `scripts/capture_fixtures.py` captures in a logged-out, cookie-asserted context. The
  security auditor signed it off after two attack rounds (V-1..V-7, R-1..R-4), and later sanitizer fixes re-ran
  both attack harnesses.
- **02** Corpus layout, a policy README with a provenance table, `.gitattributes`, and corpus-integrity tests (meta,
  orphans, budgets, sha256).
- **03** A default-deny Chromium harness: page- and context-level routes, service workers blocked, WebSockets
  closed, httpx `MockTransport`, and `BOOKSCRAPER_CHROMIUM_PATH` for macOS 13. No app code changed.
- **04** Detail goldens with `--update-goldens` (rewrite and fail) and `_known_issues` links to the fixing subtasks.
- **05** Search and API fixtures and goldens, including the **first live fixtures** (Amazon search, two robots
  files). `search_utils.py` is at 98%.
- **06** Edge-case fixtures (13) and the first live Amazon detail page.
- **07** `--refresh` drift report, with `--apply` as the only in-tree write path, which refuses wall pages.

**Tests / gate:** 488 → 661 passed with Chromium (630 + 31 skipped without it), 98% total, `sanitize.py` 100%.
`/verify-subtask` PASS ×5 and PARTIAL ×2 (04 and 05; deviations logged and ratified above). `/pr-review`: feature
LGTM and security PASS (the 103-file corpus scanned clean three ways), so APPROVE. The follow-ups are applied in
`99b1bf3`.

**Deferred / open:** the `scrape_details.py` ≥ 90% criterion (82%) is deferred to 03.0/03, the `BASE_URL` fix.
The provenance is mostly hand-built: Amazon/Packt/O'Reilly detail pages are walled intermittently, and the Leanpub
APIs are gone. Hand-built fixtures don't detect markup drift; the live ones do. `--refresh` exits 0 on drift, and
a non-zero exit is left to 06.2/05.

**Findings routed to later tasks:**
- missing `BASE_URL` for packtpub/oreilly → 03.0/03;
- live Amazon `DESCRIPTION` drift → 03.0/04;
- Amazon search detail-link drift → 03.3;
- dead Leanpub detail and search APIs → 03.0/05 + 03.3;
- the `search_utils.py:249` logger misuse → 03.3.

**Reconciliation note:** all seven subtask specs are *consumed*. 05's spec text was amended (a full 100-item
Leanpub page), and 02's success criterion was met with a ratified hand-built corpus.

## Task 06.1 - 90% coverage gate

Total line coverage is 95% (344 passed, 8 integration deselected), but the gate is not enforced in configuration,
`scrape_details.py` (80%) and `search_utils.py` (88%) are below the bar, entry points are uncovered, branch
coverage is unmeasured, and `docs/test/code_test_coverage.md` describes the wrong project.

## Notes & decisions

- **2026-09-29 - implement-milestone run started (Phase R).** Baseline: 344 passed, 8 integration deselected.
  status.md and the task READMEs agree; R3 found no stale rows or live divergences. R4: every task has a folder,
  a README and one spec per subtask row, so no gap specs were needed.
- **2026-09-29 - Execution order** (dependency-respecting, per plan *Execution order*): 01.0 → 02.0 → 02.1 → 06.0 →
  04.0 → 06.1 → 05.0 → 03.0 → 03.1 → 03.2 → 07.0 → 03.3 → 08.0 → 06.2.
- **2026-09-29 - Run conventions (user-ratified):** work lands on branch `milestone/0001-generic-implementation`,
  one commit per subtask, never pushed. The run pauses for review after each task close. 06.0 fixtures are
  captured from the live sites. If a site bot-walls the capture, a hand-built fixture is used for that site and
  recorded here.
- **2026-09-29 - Known blocker:** 02.1/08 (live X.509 re-diagnosis) needs Atlas credentials that this machine
  doesn't have. Per that spec's requirement 4, it stays 🔶 with the blocker written down unless credentials are
  provided.
- **2026-09-29 - Repo fix during setup:** `.gitignore`'s `/books.json` line carried an inline `#` comment, which
  gitignore treats as part of the pattern, so `books.json` was not actually ignored. Fixed, and `.claude/state/`
  (loop cursors) is now ignored.
- **2026-09-29 - 01.0/07 follow-up (open, minor):** `backends/local/store.py`'s module functions bind
  `LOCAL_STORE_FILE` as a def-time default, so monkeypatching it (the `isolated_local_store` fixture) doesn't reach
  a call that relies on the default. Every current caller passes an explicit path (`JsonBackend` now passes
  `store.LOCAL_STORE_FILE` at call time), so nothing is exposed today. Candidate fix: `path: Optional[Path] = None`
  resolved at call time. Revisit in 06.1 or 08.0.
- **2026-09-29 - follow-up from the 01.0 review (pre-existing, not 01.0 scope):** `commands/search.py` calls
  `storage_backend.save_books()` directly, which blocks the event loop during a Mongo bulk write, whereas
  `scrape_urls.py` wraps the same call in `asyncio.to_thread`. Pick this up in 03.3 (search pipeline).
- **2026-09-30 - Task 02.0 threat model:** [2026-09-30-mongodb-auth-configuration.md](/docs/security/2026-09-30-mongodb-auth-configuration.md)
  (SEC-1..12, no CRITICAL). Its two HIGH findings (raw pymongo exception text reaching the persistent logs, and
  `check_mongodb_connection` dropping a missing cert and ignoring `TLS_CA_FILE`) are closed by subtask 05.
- **2026-09-30 - Spec extension ratified by the user:** subtask 06's permission check also warns when
  `settings.json` is a symlink or not owned by the current UID (threat model SEC-9). It stays warning-only, once
  per process, and is skipped on Windows.
- **2026-09-30 - Task 02.1 threat model:** [2026-09-30-x509-certificate-rotation.md](/docs/security/2026-09-30-x509-certificate-rotation.md)
  (ROT-1..18, no CRITICAL). Subtask 08 (live re-diagnosis) is **blocked**: this machine has no `ATLAS_*` or
  `TEST_MONGODB_URI_*` variables and no cert or `settings.json`, so per its requirement 4 it stays 🔶 and the xfail
  markers stay. Execution order: 06 → 09 → 07 → 10.
- **2026-09-30 - Observation, 02.1/06 verification:** running `rotate-cert --check` on this machine exited 1 with
  "Could not read certificate file" for `X509-cert-142838411852079927.pem`, the old hand-downloaded cert name. That
  file doesn't exist and `~/.bookscrapper/` holds only `logs/`. Most likely a stale `TLS_CERT_FILE` in `.env` or
  the environment is still pointing at it through the legacy fallback. That's correct fail-loud behaviour, and no
  test wrote outside `tmp_path`. Not confirmed, because reading `.env` was declined. For the user to check.
- **2026-09-30 - Task 06.0 threat model:** [2026-09-30-real-html-test-corpus.md](/docs/security/2026-09-30-real-html-test-corpus.md)
  (FIX-1..14). FIX-1 (logged-out, cookie-less capture context), FIX-2 (fail-closed `assert_clean`) and FIX-8
  (default-deny offline harness) are CRITICAL merge gates.
- **2026-09-30 - Corpus policy (user-ratified; `yuchdev/BookScraper` is a PUBLIC repo):** (1) Fixtures commit the
  real markup and structure that selectors need. Long prose (descriptions, reviews, blurbs, author bios) is replaced
  with neutral placeholder text, sizes are capped, and factual metadata (title, authors, ISBN, date, publisher) is
  kept. This answers the threat model's FIX-12 licensing question. (2) The one-off capture of a handful of pages is
  done **regardless of robots.txt**, by the user's explicit decision. It is still logged out, cookie-less and
  rate-limited, and the decision is recorded in the corpus policy (`tests/fixtures/README.md`). The FIX-7 clause
  "assumes robots is honoured during capture" is therefore superseded for this capture only.
- **2026-10-01 - 06.0/02 capture outcome and decision (user-ratified):** the headless, logged-out capture was
  blocked on every site: an Amazon CAPTCHA, a Packtpub Cloudflare challenge, and an O'Reilly JS gate (a ~344 B
  page). This machine's Playwright 1.63 won't run its bundled Chromium on macOS 13, so capture drives the system
  Chromium 147 via `executable_path`. That is a platform workaround, not evasion. Decision: retry **once per site
  headed** (the scraper itself runs headed, `HEADLESS_BROWSER=False`), still logged out, cookie-less, rate-limited
  and without stealth tricks. Keep whatever succeeds. Sites that still block stay hand-built, flagged
  `hand_built: true` in `.meta.json`, and record that real-markup drift detection is not yet achieved for them.
- **2026-10-01 - Finding for task 03.0 (Leanpub API):** the documented `api/v1/cache/books/<slug>.json` endpoint
  returns 404, and `leanpub.com/<slug>.json` returns 401. `search` is the only end-to-end workflow and depends on
  it (`get_leanpub_book_details`), so live Leanpub scraping is likely broken today. The Leanpub fixtures are
  hand-built from the JSON:API shape the parser expects. Route this to 03.0/05 (Leanpub API detail hardening) or
  03.3.
- **2026-10-01 - Sanitizer over-redaction (fixed before any fixture is committed):** the substring match `auth`
  redacted Leanpub's `accepted_authors`, and the prose rule neutralized the short O'Reilly heading "Book
  description", which its selector anchors on. Fix: segment-based key matching, and never neutralize text under
  ~80 chars.
- **2026-10-01 - Bug found by the 06.0/04 goldens (for 03.0/03):** `site_constants` has no `BASE_URL` for
  `packtpub` or `oreilly`, but `scrape_book()` reads `site_constants[site]["BASE_URL"]` unconditionally. Every
  Packtpub/O'Reilly detail scrape raises `KeyError`, is retried 3x and returns `(None, "FAILED")`, so
  `scrape-urls` cannot scrape those two sites today. The goldens characterize this deliberately: the plan does
  06.0 before 03.0, and the fix belongs to 03.0, where the goldens will then flip with a reviewed diff.
- **2026-10-01 - Sanitizer defect found by the 06.0/04 goldens (being fixed):** the aggregate prose-collapse path
  replaced whole subtrees with a text node, which deleted the child elements the description selectors target
  (Amazon `.a-expander-content`), so the Amazon goldens recorded a FAILED that the fixture pipeline itself caused.
  Fix: neutralize text only and keep every element; then regenerate the fixtures and the goldens.
- **2026-10-01 - 06.0/04 coverage criterion (`scrape_details.py` ≥ 90%):** it is 80%. The missing lines sit behind
  the `BASE_URL` failure above (the extraction branches for Packtpub/O'Reilly), plus edge cases planned for
  subtask 06. The criterion is re-checked after the fixture regeneration. Anything still short is deferred to
  03.0/03 plus 06.0/06 rather than met by inventing fixtures.
- **2026-10-02 - 06.0/05 findings from the first LIVE fixtures (Amazon search is not walled; detail pages are):**
  (1) **Amazon search selector drift:** `SEARCH_BOOK_DETAIL_LINK` matches no live result card, so every candidate's
  `url` and `asin` is `None`, which makes Amazon search effectively broken (for 03.3; the goldens characterize it).
  (2) **Leanpub search API dead:** `/api/v1/cache/simple_books.json` returns 404 HTML. Together with the dead
  detail API, the `search` workflow (Leanpub is the only wired site) cannot work today. This is for 03.0/05 + 03.3,
  and it is the most consequential finding of the run so far. (3) **`search_utils.py:249`** calls
  `logger.error(msg, "error")` with a stray positional argument, which can raise inside the logging call and abort
  the Amazon search on a navigation failure (for 03.3). (4) Packtpub and O'Reilly robots.txt return 403 to a
  logged-out fetch, so they are hand-built. (5) The sanitizer gained `dib`/token-shaped URL-param stripping
  (`8972900`, v1.1.0) after `assert_clean` correctly refused the live pages.
- **2026-10-02 - 06.0/06 results:** the first LIVE Amazon detail page (`amazon/a_tour_of_cpp_3e`, headed, no CAPTCHA
  this time; Amazon walling is intermittent) parses title, authors, ISBNs and date, but `description` is `null`:
  neither `DESCRIPTION` nor `DESCRIPTION_ALT` matches today's live DOM. That is real selector drift (for 03.0/04,
  wire unused selectors and fallbacks). `scrape_details.py` coverage was re-checked at **82%** (from 80%). The
  remaining lines sit behind the missing `BASE_URL` (03.0/03) or are dead branches, so the ≥ 90% criterion from
  06.0/04 stays **deferred to 03.0/03**. The golden tier takes ~114 s with Chromium; `_bot_wall`/`_missing_title`
  retry 3x, and that cost goes away with 03.2/04.
- **2026-10-02 - Task 04.0 threat model:** [2026-10-02-ai-schema-detection.md](/docs/security/2026-10-02-ai-schema-detection.md)
  (AIS-1..16, no CRITICAL). Hard gates: AIS-3 (nothing writes `parameters.py` or `src/`) and AIS-9 (offline modes
  make zero network calls and need no API key).
- **2026-10-02 - Task 04.0 decisions (user-ratified):**
  - `detect-schema` runs **offline-only in automation**: the CI scheduled drift audit (06.2/05) uses only
    `--html-file` on the corpus, replay goldens or `--check-current-only`, with no network, no key and no spend.
    Live and Anthropic runs stay manual.
  - **Cost ceiling:** ≤ 5 sample URLs per run, at most 1 retry on a transient error, and a 40k input-token budget
    per request.
  - Following the earlier corpus policy, any recording or replay file that embeds page HTML passes the 06.0
    sanitize/prose pipeline before entering the public repo (AIS-14). Prompt-caching the static field-name list
    is fine, because it is already public in `parameters.py`.
  - **04.0/05 model:** the spec names the Sonnet tier, so the default is `claude-sonnet-5`; model facts came from
    the `claude-api` skill. Sonnet 5 runs adaptive thinking by default and thinking counts toward `max_tokens`, so
    the 2048 cap rises to about 16000, and the `refusal`/`max_tokens` stop reasons are handled explicitly.
- **2026-10-02 - 04.0/05 deviations (orchestrator-recorded, not user-ratified):** (1) `detect-schema` now sends
  one request per sample URL, merged first-non-null, instead of one request for sample 0 only. The spec's own
  success criterion needs this, and it fits the ratified per-URL cost cap. (2) The cacheable static instruction
  block is only about 230 tokens, below Sonnet 5's 1024-token cache minimum (per the `claude-api` skill), so it
  will not actually cache on the real API. The spec's "requests 2-3 report cached tokens > 0" holds only under
  mocks. Padding the prompt to the minimum would cost more than caching saves, so it is left as is; revisit if
  the static block grows.
- **2026-10-04 - 04.0/08 drift rule (user-ratified):** `--check-current-only` treats `X_ALT` as broken only when
  its primary `X` is also broken. The keys in the explicit `OPTIONAL_SELECTOR_KEYS` set in `parameters.py`
  (`DETAILS_BUTTON`, `READ_MORE_LINK`, `ASIN_DETAIL_PAGE_SELECTOR`, `SEARCH_NEXT_PAGE_SELECTOR`,
  `SEARCH_NO_RESULTS_TEXT`, `SEARCH_NO_BOOKS_FOUND`) are reported `absent_optional` and never gate. Each field
  gets a `status` of ok | broken | fallback_unused | absent_optional, and exit 4 means at least one field is broken.
  CI-gate runs: the hand-built amazon, packtpub and oreilly detail sets exit 0 (asserted); live amazon search exits
  4 on `SEARCH_BOOK_DETAIL_LINK` (real drift, recorded).
- **2026-10-04 - Follow-ups from 04.0/08:**
  1. **Drift is count-based.** A gating selector that matches an element which is present but EMPTY counts as
     `ok`. The live amazon `DESCRIPTION` matches an empty element, so the check passes although the scraper gets
     no description. Checking for non-empty text on gating fields would catch this (for 03.0/04 or a later
     04.0 iteration).
  2. **The real CLI ignores `BOOKSCRAPER_CHROMIUM_PATH`**, which only the test harness reads, and the bundled
     Playwright Chromium won't launch on macOS 13. So on this machine no browser-based command (`scrape-urls`,
     `detect-schema --url/--html-file`) can run outside the tests. Route to 05.0/03 (browser flags): add a
     Chromium executable-path option.
- **2026-10-04 - Task 04.1 added (from the Scrapegraph-ai evaluation):** the evaluation's two "worth borrowing"
  ideas ([report](/docs/reviews/2026-10-04-scrapegraph-ai-evaluation.md)) become task
  [04.1 LLM extraction fallback](/docs/roadmap/0001-generic-implementation/04.1-llm-extraction-fallback/README.md)
  (5 subtasks, P2): an opt-in `--ai-fallback` that fills single drifted fields with evidence- and
  normalizer-validated, provenance-marked values, plus a Markdown-snapshot measurement spike. The ideas are
  borrowed, not the dependency. 03.1/01 reserves `ai_extracted_fields` now, so 04.1 needs no schema bump. The
  Amazon `DESCRIPTION` empty-match case (follow-up 1 above) is its first golden target. Totals 94 → 99.
- **2026-10-06 - 03.1/04 hash policy (user-ratified):** `_clean_authors` de-duplicates authors case-insensitively,
  and the clean list is the `hash_book` input ("Dedup + migrate"). In the corpus only the synthetic `_many_authors`
  fixtures change hash; every real-book golden hash is unchanged. Stored documents with a repeated author get their
  hash recomputed by the 03.1/06 migration, which reports every changed hash and any collision (never auto-merged).
- **2026-10-22 - 03.3/03 descoped (user-ratified):** live investigation found Packtpub (Cloudflare managed
  challenge, HTTP 403 on every host including the plausible API) and O'Reilly (Akamai 403 on the search page and
  on `robots.txt` itself) both hard-block automated access. Evasion is out of policy, so `PacktpubProvider` /
  `OreillyProvider` are not implemented; `SITES_TO_SCRAPE` stays `["leanpub", "amazon"]`. See
  [`docs/scraping/search-sources.md`](/docs/scraping/search-sources.md) and
  [03.3/03](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/03-packtpub-and-oreilly-search.md)'s
  Investigation outcome. Three wall-independent pieces still land under the same subtask: `book_exists_by_url`,
  a wider Cloudflare bot-wall rule, and a refreshed packtpub robots.txt fixture.
