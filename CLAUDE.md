# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

`bookscraper` is an asyncio + Playwright scraper that pulls book metadata (title, authors, ISBNs, publication date,
description, tags) from Amazon, Packtpub, Leanpub, and O'Reilly, and writes results to one of two interchangeable
storage backends — a MongoDB Atlas collection, or a local `books.json` file schema-identical to it (see **Storage
backend** below). It exposes one CLI (`bookscraper`) with six subcommands — two scraping workflows, MongoDB Atlas
X.509 certificate rotation (`rotate-cert`), an AI-assisted selector-schema detection tool (`detect-schema`), a
store migration command (`migrate`), and a diagnostic command (`doctor`) — see Architecture below.

## Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management, virtual environments, and packaging —
there is no `requirements.txt`, `setup.py`, or `setup.cfg`; `pyproject.toml` (dependencies) + `uv.lock` (pinned,
resolved versions — commit it) are the single source of truth.

```bash
uv sync                        # creates .venv and installs dependencies + the bookscraper package itself (editable)
uv run playwright install chromium
```

`uv sync` replaces `python -m venv` + `pip install -r requirements.txt` + `pip install -e .` in one step. Run any
command inside the venv via `uv run <cmd>`, or activate it yourself with `source .venv/bin/activate` and drop the
`uv run` prefix.

MongoDB output needs an auth method configured — see **Authentication configuration** below for the deterministic
`~/.bookscrapper/settings.json` mechanism (preferred) and the legacy `.env`-only fallback it supersedes.
`~/.bookscrapper/` is intentionally minimal: `settings.json`, one or more `X509-cert-*.pem` files, and `logs/`
(run logs — see Logging below) are the only things that belong there.

## Roadmap

- [docs/roadmap/README.md](docs/roadmap/README.md) — milestone hierarchy and vocabulary
- [docs/roadmap/0001-generic-implementation/status.md](docs/roadmap/0001-generic-implementation/status.md) —
  per-task status for the current milestone

## Authentication configuration

`src/bookscraper/backends/mongo/config.py` is the single place that decides how the app authenticates to MongoDB
Atlas — both the real connection and the pre-flight check `check_mongodb_connection()` (both in
`backends/mongo/database.py`) call `config.resolve_mongo_connection()` rather than reading `os.environ` themselves.
Don't move the pre-flight check back into `book_utils.py` (import-cycle risk; see flakeforge X006).

**Primary mechanism — `~/.bookscrapper/settings.json` (deterministic, no fallback chain):** if this file exists, it
is the *sole* source of truth: its declared `auth_type` and each field's declared `source` are followed exactly,
with nothing else consulted. A missing env var or unrecognized shape raises `config.ConfigError` immediately
(surfaced as a critical log + `print_log` error, connection aborted).

Every field is a `{"source": "env" | "literal", "value": "..."}` object. Password (SCRAM) auth:
```json
{
  "auth_type": "pass",
  "pass": {
    "uri": {"source": "literal", "value": "mongodb+srv://<user>:<pass>@<cluster>/<db>?retryWrites=true&w=majority"}
  }
}
```

X.509 (client-cert) auth — two independently-sourced fields:
```json
{
  "auth_type": "x509",
  "x509": {
    "uri": {"source": "env", "value": "MONGODB_URI"},
    "cert": {"source": "literal", "value": "X509-cert-20260921_041307.pem"}
  }
}
```

**Optional `database` block:** either shape may add a top-level `database` block:
```json
"database": {"name": {"source": "literal", "value": "bookscraper_db"},
             "collection": {"source": "literal", "value": "books"}}
```
`config.resolve_mongo_namespace()` returns `(name, collection)`. When `settings.json` omits the block, defaults
`bookscraper_db` / `books` apply and `MONGODB_DB_NAME` / `MONGODB_COLLECTION_NAME` are **not** consulted. The block
must define *both* `name` and `collection` or resolution fails. Unknown top-level keys raise `ConfigError`.

**Permission/ownership hardening (POSIX only, advisory).** `load_settings()` warns when `settings.json` is
group/other-readable (`mode & 0o077 != 0`), is a symlink, or is not owned by the current UID. Cert-path resolution
logs only the resolved path (never file contents).

**Example file.** `docs/examples/settings.example.json` is a copy-ready x509 shape with placeholders only.

**Legacy fallback (no settings.json):** `MONGODB_URI` / `TLS_CERT_FILE` env vars (via `.env` in the repo root or
the real environment) are read directly, with `TLS_CERT_FILE` falling back to the newest
`~/.bookscrapper/X509-cert-*.pem` by modification time. `TLS_CA_FILE` is independent and always comes from the
environment:
```dotenv
MONGODB_URI="mongodb+srv://<user>:<pass>@<cluster>/<db>?retryWrites=true&w=majority"
TLS_CERT_FILE="/path/to/cert.pem"   # optional, for X.509 client-cert auth
TLS_CA_FILE="/path/to/ca.pem"       # optional, for server CA verification (database.py only)
```

**Single `MongoClient` factory (`backends/mongo/client.py`).** Both the real connection and the pre-flight check
build their client through `client.build_mongo_client()` + `client.ping()` — so there is exactly one
`MongoClient(...)` construction site in `src/`. Certificate validation is never disableable:
`tlsAllowInvalidCertificates=False` and `ServerApi("1")` are hard-coded. `client.ping()` re-raises every
connection/auth failure as a redacted `MongoConnectionError` carrying only the exception class name plus pymongo
`code`/`codeName` — raw pymongo text (which can embed the host list or connection string) never reaches a log. The server-selection timeout is 10 000 ms by default and
overridable via `MONGODB_SERVER_SELECTION_TIMEOUT_MS` (a bad value warns and falls back; see [docs/cli/cli.md](/docs/cli/cli.md)).

**TLS certificate rotation** — see operator runbook:
[`docs/runbooks/x509-certificate-rotation.md`](docs/runbooks/x509-certificate-rotation.md). The runbook covers
operational procedures (rotation, scheduling, recovery) and a "Behavior reference" section with code-level details
(verify-before-repoint, pruning, expiry inspection, and the unverified Atlas error 8000 status).

## Storage backend

`--store-backend {mongo|json}` is **required** on both scraping subcommands and picks a single backend for *both*
reads (duplicate checks) and writes — there is no dual-write and no implicit default.
`src/bookscraper/backends/storage.py` defines the contract (`StorageBackend.save_books()` /
`.book_exists_by_hash()` / `.book_exists_by_asin()` / `.leanpub_book_exists()` / `.close()`) and
`resolve_store_backend(name)`, which runs a pre-flight check and raises `BackendUnavailableError` if it fails.

Both backends store one canonical document shape — `src/bookscraper/models.py`'s schema-2 `BookRecord`
(`@dataclass(frozen=True, slots=True)`, `SCHEMA_VERSION = 2`). Its field table, worked examples, and the v1→v2
mapping live in [`docs/scraping/book-record-schema.md`](docs/scraping/book-record-schema.md).

`save_books()` returns a frozen `SaveSummary` (`inserted` / `duplicates` / `errors` / `total`) from both backends.
`summary.errors > 0` is the partial-failure signal. Diagnostic reports (`failed_urls.csv` / `failed_books.csv` /
`other_links.csv`) are unrelated to `--store-backend` and are always written best-effort.

- **`mongo`** — thin wrapper over `backends/mongo/database.py` + `backends/mongo/deduplicate.py`.
- **`json`** — a local `books.json` at the repo root, persisting and accumulating across runs, with the same
  `hash`-uniqueness semantics as MongoDB's unique index. Writes are atomic (temp file + `fsync` + `os.replace`) with
  a one-generation `books.json.bak` backup. A corrupted (non-JSON) store raises `LocalStoreCorruptError` rather than
  being silently overwritten.

**Migrating a store from v1 to v2** (`bookscraper migrate --store-backend {mongo|json} [--apply]`) — see
[`docs/runbooks/migrate-v1-to-v2.md`](docs/runbooks/migrate-v1-to-v2.md) for the full walkthrough. Dry-run by
default; `--apply` rewrites v1 documents in place; idempotent; never deletes.

## Running

See [docs/cli/cli.md](docs/cli/cli.md) for the full reference: every flag, default, env override,
and exit code for all six subcommands (`scrape-urls`, `search`, `rotate-cert`, `detect-schema`,
`migrate`, `doctor`).

Common invocations:

```bash
# Scrape a fixed list of URLs, save to the local JSON store:
uv run bookscraper scrape-urls -f content/urls.csv --store-backend json

# Search Leanpub for new books matching the configured queries, save to MongoDB:
uv run bookscraper search --store-backend mongo

# Report the active X.509 certificate's expiry (no Atlas contact needed):
uv run bookscraper rotate-cert --check --warn-days 14

# Scrape with the opt-in LLM extraction fallback enabled (see LLM extraction fallback below):
uv run bookscraper scrape-urls -f content/urls.csv --store-backend json --ai-fallback
```

`--store-backend {mongo|json}` is required for `scrape-urls`, `search`, and `migrate`.

## AI-assisted schema detection

`bookscraper detect-schema` automates figuring out a site's CSS selectors when its markup drifts. It proposes
selectors via Claude (`ANTHROPIC_API_KEY`), validates them against sample pages' live DOM, and emits a diff-style
report — but **never writes `parameters.py`** (propose, never auto-apply). See
[`docs/scraping/schema-detection.md`](docs/scraping/schema-detection.md) for the full reference (usage, page
types, offline modes, JSON drift report, exit codes, prompt-injection hardening, and CI integration). See
[`docs/adr/0002-ai-assisted-schema-detection.md`](docs/adr/0002-ai-assisted-schema-detection.md) for design
rationale.

## LLM extraction fallback

`--ai-fallback` (task 04.1) is an **opt-in** safety net: when a deterministic selector leaves an *eligible* field
empty, the scraper asks Claude for just that one field from the page HTML already in hand, validates the answer
through five deterministic gates, and stores it only if it passes. It is **off by default** — without the flag no
Anthropic call is made and `ANTHROPIC_API_KEY` is never read. Eligible fields: `DESCRIPTION`, `PUBLICATION_DATE`,
`ISBN13`, `ISBN10`, `AUTHORS`; `title` is never eligible (feeds the dedup hash). See
[`docs/scraping/llm-extraction-fallback.md`](docs/scraping/llm-extraction-fallback.md) for the full reference
(flags, eligible fields, implementation, provenance, security contracts, and tests).

## Testing

`tests/` is a real pytest suite in three tiers, mirroring `src/bookscraper`'s subpackages:

- **`tests/unit/`** — pure logic, no mocks: `cli.py`'s `build_parser()`, `book_utils.py`'s hash/date/write-check
  utilities, `backends/mongo/config.py`'s resolver (all six `settings.json` shapes plus the legacy fallback),
  `backends/mongo/cert_rotation.py`'s months validation / atomic write / `settings.json` repointing /
  `read_certificate_expiry` / `cert_expiry_warning` (throwaway self-signed certs generated in-test via
  `cryptography`), `backends/local/store.py`, `scraping/parameters.py` data sanity checks, and
  `scraping/schema_detection.py`'s pure logic.
- **`tests/mock/`** — every external dependency mocked (`pymongo.MongoClient`, `httpx.AsyncClient`, Playwright's
  `Browser`/`Page`/`Locator`): database, dedup, cert rotation, storage, scraping, search, schema detection,
  all six commands, and `main.py`. `tests/mock/scraping/html/` is the **offline fixture-serving harness** — see
  [`docs/test/html-fixtures.md`](docs/test/html-fixtures.md) for the corpus structure, `corpus_router`/`httpx_corpus`
  helpers, chromium marker behavior, and golden test details. Chromium-backed tests carry the `chromium` marker
  (included in the default run) and skip cleanly when no usable browser is found.
- **`tests/integration/`** — hits real MongoDB Atlas, tagged `@pytest.mark.integration` and excluded from the
  default run (`addopts = -m "not integration"`); run explicitly with `uv run pytest -m integration`.
  `test_connection_settings.py` exercises all six valid `settings.json` shapes. Expected outcome: both `pass` shapes
  connect; all four `x509` shapes resolve but the live connection is currently `xfail(strict=False)` pending
  re-diagnosis of Atlas error 8000 (see the runbook's Behavior reference).

**Integration tier in CI.** `.github/workflows/integration.yml` runs only on `workflow_dispatch` and a weekly
schedule, under the `atlas-integration` GitHub Environment holding `TEST_MONGODB_URI_PASS`,
`TEST_MONGODB_URI_TLS`, and `TEST_X509_CERT_PEM`. A missing secret or all-skipped run **fails** the workflow.
See [`docs/dev/ci.md`](docs/dev/ci.md).

**Coverage target:** `uv run pytest --cov` — `fail_under = 90` combined line+branch coverage. A per-module
floor of 90% is enforced separately by `uv run python scripts/check_module_coverage.py` after
`uv run pytest --cov --cov-report=json -q`. See
[`docs/test/code_test_coverage.md`](docs/test/code_test_coverage.md).

**Test credentials** (`tests/integration/` only): `TEST_MONGODB_URI_PASS` / `TEST_MONGODB_URI_TLS` in `.env`
(repo root, git-ignored) — env-var only, no fallback file (`mongo_test_helpers.get_mongodb_uri()` takes no
fallback path), so a bug in either the production resolver or a test can never cross-load the other's
credentials. The TLS test reads the TLS cert from the production location (`TLS_CERT_FILE` / the newest
`X509-cert-*.pem` by mtime) — that resolution must stay a **glob for the newest file, never a pinned filename**
(`tests/unit/test_integration_helpers.py` guards this).

## Architecture

`src/bookscraper/` is organized into four subpackages by responsibility. Import direction is one-way and there are
no cycles: `commands/` → `scraping/` + `backends/`; `scraping/` → `backends/` (for the `StorageBackend` type) +
`book_utils.py`; `backends/` → `book_utils.py`; nothing under `backends/` or `scraping/` ever imports from
`commands/`.

Root-level entry points and shared utilities:
- **`cli.py`** — argparse only: builds the `bookscraper` parser and its subparsers. Every flag sets an explicit
  `dest=` (e.g. `--input-file` → `input_file`) so a CLI flag can never silently drift from the attribute name the
  code reads.
- **`main.py`** — the real entry point: parses args via `cli.build_parser()` and dispatches to the matching
  `commands/*.py` module's `run(args)`. `main_sync()` wraps this in `asyncio.run(...)` and is the single
  `sys.exit` call site. Both the `bookscraper` console-script and `__main__.py` call `main_sync()`.
- **`book_utils.py`** — `print_log()`, `configure_logging()`, `check_local_write_permission`,
  `extract_year_from_date`, `hash_book`. Stays at the package root since every subpackage imports from it.

**`commands/`** — the CLI workflows:
- **`scrape_urls.py`** — takes a CSV of book URLs, buckets them by site, and scrapes each in batches of 10
  concurrent Playwright tasks via `scraping.scrape_details.scrape_book()`.
- **`search.py`** — for each site in `SITES_TO_SCRAPE` and each query in `SEARCH_QUERIES`, searches for
  candidate books, deduplicates them before the expensive detail scrape, then scrapes details for survivors.
  Currently only Leanpub is wired end-to-end (via its JSON API; `SITES_TO_SCRAPE = ["leanpub"]`).
- **`rotate_cert.py`** — loads `.env`, calls `cert_rotation.rotate_certificate()` in a worker thread (blocking
  `httpx`), reports the result, and returns a `bookscraper.exitcodes` value.
- **`detect_schema.py`** — loads sample detail-page URLs, asks Claude to propose CSS selectors, validates them
  against each page's live DOM, and prints a diff-style report. Never writes `parameters.py`.
- **`migrate.py`** — rewrites a store's v1 documents in place to schema-2. Dry-run by default.
- **`doctor.py`** — runs pre-flight checks and reports the setup health.

**`backends/`** — the storage-backend abstraction:
- **`storage.py`** — `StorageBackend`, `MongoBackend`, `JsonBackend`, `resolve_store_backend()`.
- **`mongo/database.py`** / **`mongo/deduplicate.py`** — two dedup layers: (1) pre-scrape checks by
  `book_id`/`slug` or `asin`, and (2) a unique MongoDB index on `hash` (SHA-256 of normalized
  title+authors+year, computed by `book_utils.hash_book`).
- **`mongo/config.py`** — `resolve_mongo_connection()`, the deterministic MongoDB auth resolver.
- **`mongo/client.py`** — the single `MongoClient` factory (`build_mongo_client()`, `ping()`). `MongoClient(`
  appears exactly once in `src/`.
- **`mongo/cert_rotation.py`** — Atlas Admin API client for issuing a new X.509 client certificate.
- **`local/store.py`** — the `json` backend: `books.json` at the repo root, persistent and accumulating across
  runs.

**`scraping/`** — everything about talking to the four book sites:
- **`parameters.py`** — `site_constants`: per-site CSS selectors and API URLs (this is what to edit when a site
  changes its markup or a new site is added). Also `SEARCH_QUERIES`, `SITES_TO_SCRAPE`, `SCRAPE_FILTERS`,
  `RATE_LIMITS`/`BOT_WALL`, `USER_AGENT_MODE`/`USER_AGENT_TEMPLATES`, and `HEADLESS_BROWSER` (default `True` —
  headed mode is an explicit `--no-headless` debugging choice; a scheduled/CI run must never open a window).
- **`scrape_details.py`** — per-site Playwright detail scraping (`scrape_book`) plus Leanpub's `httpx`-based
  `get_leanpub_book_details`. `route_handler` aborts all non-document requests.
- **`search_utils.py`** — collects search-result candidates per site.
- **`schema_detection.py`** — the `detect-schema` engine.
- **`ai_common.py`** / **`ai_extraction.py`** — shared AI infrastructure and the `--ai-fallback` engine.

## Logging

Every run writes to `~/.bookscrapper/logs/bookscraper_<YYYYMMDD_HHMMSS>_<pid>.log`. All modules use
`logging.getLogger(__name__)`, forming one `bookscraper.*` hierarchy; `configure_logging()` attaches a single
redacting file handler to the **root** logger with a `RedactingFilter`+`RedactingFormatter` (masks
credentials/PEM keys, never removed) and a `ContextFilter` (stamps each record with `<site>:<short-id>` for the
active scrape). Third-party loggers (`pymongo`, `httpx`, `anthropic`, etc.) are pinned to `WARNING`; the app's own
`bookscraper.*` logger follows `--log-severity`. `BOOKSCRAPER_LIB_LOG_LEVEL` overrides the third-party clamp.
See [`docs/dev/logging.md`](docs/dev/logging.md) for the full reference.

## Known inconsistencies to watch for

- Curated input URL lists (`content/urls.csv`, `content/urls_amazon.csv`) live under `content/` and are committed —
  they're hand-maintained candidate lists for `scrape-urls -f`, not run output. Actual run artifacts (`books.json`
  for `--store-backend json`; `failed_books.csv`, `failed_urls.csv`, `other_links.csv` diagnostics regardless of
  backend) are gitignored and written to the repo root by default; don't add new default output filenames without
  gitignoring them too. Unlike the diagnostics, `books.json` is meant to persist across runs — don't delete it
  casually, it's the entire point of the `json` backend.
