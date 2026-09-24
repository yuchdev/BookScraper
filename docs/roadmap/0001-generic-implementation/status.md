# Milestone 0001 - Generic Implementation - Status

Tracks progress against [plan.md](/docs/roadmap/0001-generic-implementation/plan.md). Updated by the
`implement-subtasks` loop with a targeted row edit as each task lands.

## Current status

| Task | Name                               | Status         | Subtasks done | Tests                                                                                                  |
|------|------------------------------------|----------------|---------------|--------------------------------------------------------------------------------------------------------|
| 01.0 | Storage backend abstraction        | 🔶 In progress | 4 / 8         | `tests/unit/backends/local/test_store.py`, `tests/mock/backends/test_storage.py`                       |
| 02.0 | MongoDB auth configuration         | 🔶 In progress | 3 / 6         | `tests/unit/backends/mongo/test_config.py`, `tests/mock/backends/mongo/test_database.py`, `tests/integration/test_connection_settings.py` |
| 02.1 | X.509 certificate rotation         | 🔶 In progress | 5 / 10        | `tests/unit/backends/mongo/test_cert_rotation.py`, `tests/mock/backends/mongo/test_cert_rotation.py`, `tests/mock/commands/test_rotate_cert.py` |
| 03.0 | Scraping engine hardening          | ⬜ Not started | 0 / 6         | -                                                                                                      |
| 03.1 | Canonical book data model          | ⬜ Not started | 0 / 6         | -                                                                                                      |
| 03.2 | Politeness & anti-bot resilience   | ⬜ Not started | 0 / 6         | -                                                                                                      |
| 03.3 | Multi-site search pipeline         | ⬜ Not started | 0 / 6         | -                                                                                                      |
| 04.0 | AI-assisted schema detection       | 🔶 In progress | 4 / 9         | `tests/unit/scraping/test_schema_detection.py`, `tests/mock/scraping/test_schema_detection.py`, `tests/mock/commands/test_detect_schema.py` |
| 05.0 | CLI improvements                   | ⬜ Not started | 0 / 8         | `tests/unit/test_cli.py` (baseline)                                                                    |
| 06.0 | Real-HTML mock test corpus         | ⬜ Not started | 0 / 7         | -                                                                                                      |
| 06.1 | 90% coverage gate                  | 🔶 In progress | 1 / 6         | whole suite: 344 passed, 95% total (2026-09-24)                                                        |
| 06.2 | Continuous integration             | ⬜ Not started | 0 / 6         | -                                                                                                      |
| 07.0 | Observability & run reporting      | ⬜ Not started | 0 / 4         | -                                                                                                      |
| 08.0 | Repository hygiene & documentation | ⬜ Not started | 0 / 6         | -                                                                                                      |

**Legend:** ✅ Complete · 🔶 In progress / partial · ⬜ Not started

**Totals:** 17 / 94 subtasks complete.

## Task 01.0 - Storage backend abstraction

Delivered before this milestone was written down: the `StorageBackend` contract, `JsonBackend` (persistent
`books.json` with hash uniqueness), `MongoBackend` (delegating to `database.py` / `deduplicate.py`), and the
required `--store-backend` flag with a pre-flight check. Open: `save_books_to_mongodb` continues after a `None`
collection and returns nothing; the JSON store writes non-atomically and re-reads the file per lookup; no
indexes on `asin` / `book_id` / `slug`; no cross-backend parity test.

## Task 02.0 - MongoDB auth configuration

Delivered: the deterministic `settings.json` resolver (six valid shapes), the legacy env fallback with a
newest-cert glob, and the X.509 mechanism diagnostic. Open: the unconditional "connection successful" log, two
divergent `MongoClient` construction paths, and `settings.json` permissions / namespace / unknown-key validation.

## Task 02.1 - X.509 certificate rotation

Delivered: `bookscraper rotate-cert` issues a cert via the Atlas Admin API using `ATLAS_*` Service Account
credentials, writes it atomically `0600`, and repoints `settings.json` for literal cert references. Open: expiry
inspection (`--check`), retention/pruning, verify-before-repoint, a recorded live re-diagnosis of the unverified
Atlas-8000 claim (so the inert `xfail(strict=False)` markers can be removed or made strict), and an operator
runbook.

## Task 04.0 - AI-assisted schema detection

Delivered per [ADR 0002](/docs/adr/0002-ai-assisted-schema-detection.md): Playwright load → prune → Claude proposal →
cross-sample DOM validation → diff report, propose-only and fail-loudly. Open: current model default plus a token
budget and prompt caching, search-page support, offline `--html-file`/`--replay` mode with golden tests, a JSON
report with a drift exit code, and prompt-injection hardening.

## Task 06.1 - 90% coverage gate

Total line coverage is 95% (344 passed, 8 integration deselected), but the gate is not enforced in configuration,
`scrape_details.py` (80%) and `search_utils.py` (88%) are below the bar, entry points are uncovered, branch
coverage is unmeasured, and `docs/test/code_test_coverage.md` describes the wrong project.
