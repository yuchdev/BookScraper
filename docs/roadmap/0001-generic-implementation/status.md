# Milestone 0001 - Generic Implementation - Status

Tracks progress against [plan.md](/docs/roadmap/0001-generic-implementation/plan.md). Updated by the
`implement-subtasks` loop with a targeted row edit as each task lands.

## Current status

| Task | Name                               | Status         | Subtasks done | Tests                                                                                                  |
|------|------------------------------------|----------------|---------------|--------------------------------------------------------------------------------------------------------|
| 01.0 | Storage backend abstraction        | ✅ Complete    | 8 / 8         | `tests/unit/backends/local/test_store.py`, `tests/mock/backends/test_storage.py`, `tests/mock/backends/test_backend_parity.py`                       |
| 02.0 | MongoDB auth configuration         | ✅ Complete    | 6 / 6         | `tests/unit/backends/mongo/test_config.py`, `tests/mock/backends/mongo/test_database.py`, `tests/integration/test_connection_settings.py`, `tests/mock/backends/mongo/test_client.py` |
| 02.1 | X.509 certificate rotation         | ✅ Complete (08 blocked) | 9 / 10        | `tests/unit/backends/mongo/test_cert_rotation.py`, `tests/mock/backends/mongo/test_cert_rotation.py`, `tests/mock/commands/test_rotate_cert.py` |
| 03.0 | Scraping engine hardening          | ⬜ Not started | 0 / 6         | -                                                                                                      |
| 03.1 | Canonical book data model          | ⬜ Not started | 0 / 6         | -                                                                                                      |
| 03.2 | Politeness & anti-bot resilience   | ⬜ Not started | 0 / 6         | -                                                                                                      |
| 03.3 | Multi-site search pipeline         | ⬜ Not started | 0 / 6         | -                                                                                                      |
| 04.0 | AI-assisted schema detection       | 🔶 In progress | 4 / 9         | `tests/unit/scraping/test_schema_detection.py`, `tests/mock/scraping/test_schema_detection.py`, `tests/mock/commands/test_detect_schema.py` |
| 05.0 | CLI improvements                   | ⬜ Not started | 0 / 8         | `tests/unit/test_cli.py` (baseline)                                                                    |
| 06.0 | Real-HTML mock test corpus         | 🔶 In progress | 3 / 7         | -                                                                                                      |
| 06.1 | 90% coverage gate                  | 🔶 In progress | 1 / 6         | whole suite: 344 passed, 95% total (2026-09-24)                                                        |
| 06.2 | Continuous integration             | ⬜ Not started | 0 / 6         | -                                                                                                      |
| 07.0 | Observability & run reporting      | ⬜ Not started | 0 / 4         | -                                                                                                      |
| 08.0 | Repository hygiene & documentation | ⬜ Not started | 0 / 6         | -                                                                                                      |

**Legend:** ✅ Complete · 🔶 In progress / partial · ⬜ Not started

**Totals:** 31 / 94 subtasks complete.

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

## Task 04.0 - AI-assisted schema detection

Delivered per [ADR 0002](/docs/adr/0002-ai-assisted-schema-detection.md): Playwright load → prune → Claude proposal →
cross-sample DOM validation → diff report, propose-only and fail-loudly. Open: current model default plus a token
budget and prompt caching, search-page support, offline `--html-file`/`--replay` mode with golden tests, a JSON
report with a drift exit code, and prompt-injection hardening.

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
