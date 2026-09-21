# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

`bookscraper` is an asyncio + Playwright scraper that pulls book metadata (title, authors, ISBNs, publication date,
description, tags) from Amazon, Packtpub, Leanpub, and O'Reilly, and writes results to one of two interchangeable
storage backends — a MongoDB Atlas collection, or a local `books.json` file schema-identical to it (see **Storage
backend** below). It exposes one CLI (`bookscraper`) with two subcommands covering two distinct workflows — see
Architecture below.

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
(run logs — see Logging below) are the only things that belong there. Nothing else does — see Authentication
configuration for where credentials that used to live there (`driver_string.txt`, a `test/` subdirectory) went.

## Authentication configuration

`src/bookscraper/backends/mongo/config.py` is the single place that decides how the app authenticates to MongoDB
Atlas — both `backends/mongo/database.py` (the real connection) and `book_utils.py`'s `check_mongodb_connection()`
(the pre-flight check) call `config.resolve_mongo_connection()` rather than reading `os.environ` themselves, so
there is exactly one resolution path to reason about, not two copies that could drift. Note:
`check_mongodb_connection()` imports `config` *inside* the function body rather than at module level — `book_utils.py`
sits at the package root and is imported by `backends/__init__.py`'s own import chain, so a module-level
`from .backends.mongo import config` there would be circular (see `book_utils.py`'s comment on that import). Don't
"clean this up" by hoisting it back to the top of the file.

**Primary mechanism — `~/.bookscrapper/settings.json` (deterministic, no fallback chain):** if this file exists, it
is the *sole* source of truth: its declared `auth_type` and each field's declared `source` are followed exactly,
with nothing else consulted. A missing env var or an unrecognized shape raises `config.ConfigError` immediately
(surfaced as a critical log + `print_log` error, connection aborted) rather than silently trying something else —
determinism means a bad config fails loudly, not that it guesses.

Every field is a `{"source": "env" | "literal", "value": "..."}` object: `"literal"` uses `value` as-is (a full
connection string, or for a cert, a filename resolved under `~/.bookscrapper/` if not absolute); `"env"` treats
`value` as an environment variable name to look up (so `.env`, loaded by the caller before resolution, works too).

Password (SCRAM) auth — one field:
```json
{
  "auth_type": "pass",
  "pass": {
    "uri": {"source": "literal", "value": "mongodb+srv://<user>:<pass>@<cluster>/<db>?retryWrites=true&w=majority"}
  }
}
```

X.509 (client-cert) auth — two independently-sourced fields, `uri` and `cert`:
```json
{
  "auth_type": "x509",
  "x509": {
    "uri": {"source": "env", "value": "MONGODB_URI"},
    "cert": {"source": "literal", "value": "X509-cert-20260921_041307.pem"}
  }
}
```
`uri.source` and `cert.source` are chosen independently, so all four combinations (literal/literal, literal/env,
env/literal, env/env) are valid and behave identically as long as they resolve to the same underlying values — see
`tests/connection_test_settings.py`, which exercises all four plus both `pass` shapes.

**Legacy fallback (no settings.json):** for backward compatibility, `MONGODB_URI` / `TLS_CERT_FILE` env vars (via
`.env` in the repo root or the real environment) are read directly, with `TLS_CERT_FILE` — if unset — falling back
further to the newest `~/.bookscrapper/X509-cert-*.pem` by modification time (`config.get_default_tls_cert_file()`).
`TLS_CA_FILE` (server CA verification, `backends/mongo/database.py` only) is independent of all of this and always
comes straight from the environment:
```dotenv
MONGODB_URI="mongodb+srv://<user>:<pass>@<cluster>/<db>?retryWrites=true&w=majority"
TLS_CERT_FILE="/path/to/cert.pem"   # optional, for X.509 client-cert auth
TLS_CA_FILE="/path/to/ca.pem"       # optional, for server CA verification (database.py only)
```

**TLS certificate rotation pipeline:** `scripts/rotate_atlas_cert.py` issues a fresh Atlas-managed X.509 client
certificate via the Atlas Admin API (`POST .../databaseUsers/{user}/certs`, requires a Project-Owner-scoped Atlas
API Service Account: `ATLAS_CLIENT_ID` / `ATLAS_CLIENT_SECRET` / `ATLAS_PROJECT_ID` / `ATLAS_DB_USER` in `.env` or
the environment) and atomically writes it to `~/.bookscrapper/X509-cert-<UTC timestamp>.pem`. Its position in the
auth system above depends on how the app is configured:
- **No `settings.json`:** nothing else to do — the legacy fallback's "newest `X509-cert-*.pem`" glob picks up the
  new file automatically on the next run.
- **`settings.json` with `x509.cert.source: "literal"`:** the script repoints `x509.cert.value` at the new
  filename itself (`update_settings_after_rotation()`), so resolution stays fully deterministic — no stale
  reference to a since-rotated cert — without a manual edit.
- **`settings.json` with `x509.cert.source: "env"`:** the script only prints a reminder naming the env var and the
  new path; it deliberately never modifies your environment or `.env` on your behalf.
- **`auth_type: "pass"`, or no rotation applicable:** the script leaves `settings.json` untouched and says so.

Note: as of this writing, Atlas still rejects the certificates this cluster's X.509 database user presents
(`certificate validation failed`, Atlas error code 8000) — a server-side/Atlas-registration issue, not a client- or
resolver-side bug (`tests/connection_test_tls.py` and all four `x509` shapes in `tests/connection_test_settings.py`
fail with this exact error today, by design, until that's resolved on the Atlas side).

## Storage backend

`--store-backend {mongo|json}` is **required** on both subcommands and picks a single backend used for *both*
reads (duplicate checks) and writes (saving scraped books) — there is no dual-write and no implicit default, so
which store a run touched is always explicit from its command line. `src/bookscraper/backends/storage.py` defines
the contract (`StorageBackend.save_books()` / `.book_exists_by_hash()` / `.book_exists_by_asin()` /
`.leanpub_book_exists()` / `.close()`) and `resolve_store_backend(name)`, which runs a pre-flight check for the
requested backend (MongoDB ping, or local write-permission test) and exits the process if it's unusable — the same
"explicit, no ambiguous fallback" approach as `config.resolve_mongo_connection()` (see Authentication configuration
above). `backends/__init__.py` re-exports `StorageBackend`, `MongoBackend`, `JsonBackend`, and
`resolve_store_backend` for `from ..backends import ...` elsewhere in the package.

- **`mongo`** (`storage.MongoBackend`) — thin wrapper over `backends/mongo/database.py`'s existing MongoDB
  functions (`save_books_to_mongodb`, `check_book_exists_in_db`, `check_amazon_asin_exists_in_db`) plus
  `backends/mongo/deduplicate.py`'s `leanpub_prescrape_deduplicate`.
- **`json`** (`storage.JsonBackend`, backed by `backends/local/store.py`) — a local `books.json` at the repo root:
  one JSON array of book documents, schema-identical to what a MongoDB `insert_one` would receive. Unlike the CSV
  output it replaces (a disposable, per-run-overwritten dump that was never read back), `books.json` **persists
  and accumulates across runs**, and enforces the same uniqueness-by-`hash` semantics as MongoDB's unique index —
  a book saved in one run is still there, and still correctly detected as a duplicate, in the next. This is what
  makes the two backends behave identically from the caller's perspective, and what "local storage matches
  database content one-to-one" means in practice.
- Diagnostic reports that aren't database content — `failed_urls.csv` / `failed_books.csv` / `other_links.csv` —
  are unrelated to `--store-backend` and are always written as CSV best-effort by both subcommands, since MongoDB
  itself never stores failed/skipped attempts either.

## Running

```bash
# `uv sync` (see Setup above) already registers the `bookscraper` console script inside .venv.

# scrape-urls: scrapes a fixed list of URLs from a CSV (works for all 4 sites via Playwright).
uv run bookscraper scrape-urls -f content/urls.csv --store-backend mongo
uv run bookscraper scrape-urls -f content/urls.csv --store-backend json

# search: searches sites for books matching queries defined in parameters.py, then scrapes details.
uv run bookscraper search --store-backend mongo
uv run bookscraper search --store-backend json --max-search-pages 5

# Without `uv run` (inside an activated .venv), or via `python -m` directly:
python -m bookscraper scrape-urls -f content/urls.csv --store-backend json
python -m bookscraper search --store-backend json
```

`--store-backend` is required — omitting it is an argparse error, not an interactive prompt (there is no more
`(C)/(M)/(B)/(E)` choice: see Storage backend above for why the choice is always explicit).

## Testing

`tests/` is a real pytest suite in three tiers, mirroring `src/bookscraper`'s subpackages:

- **`tests/unit/`** — pure logic, no mocks: `cli.py`'s `build_parser()`, `book_utils.py`'s
  `hash_book`/`extract_year_from_date`/`check_local_write_permission`, `backends/mongo/config.py`'s
  resolver (all six `settings.json` shapes plus the legacy fallback, purely as resolution logic — no
  network), `backends/local/store.py` (real file I/O via `tmp_path`), and `scraping/parameters.py`
  data sanity checks.
- **`tests/mock/`** — the rest of `src/bookscraper`, with every external dependency mocked
  (`pymongo.MongoClient`, `httpx.AsyncClient`, Playwright's `Browser`/`Page`/`Locator` via
  `tests/mock/scraping/playwright_fakes.py`): `backends/mongo/database.py`, `backends/mongo/deduplicate.py`,
  `backends/storage.py`, `scraping/scrape_details.py`, `scraping/search_utils.py`,
  `commands/scrape_urls.py`, `commands/search.py`, and `main.py`.
- **`tests/integration/`** — hits real MongoDB Atlas, tagged `@pytest.mark.integration` and excluded
  from the default run (`addopts = -m "not integration"` in `pyproject.toml`); run explicitly with
  `uv run pytest -m integration`. `test_connection_password.py` / `test_connection_tls.py` verify
  SCRAM/X.509 connectivity directly; `test_connection_settings.py` integration-tests the real
  `config.resolve_mongo_connection()` resolver (see Authentication configuration above) across all
  six valid `settings.json` shapes using a temporary settings file per scenario (the real
  `~/.bookscrapper/settings.json` is never touched). Expected outcome: both `pass` shapes resolve and
  connect; all four `x509` shapes resolve to correct values but the live connection currently fails
  with Atlas's known `certificate validation failed` error (see Authentication configuration) — those
  legs are `@pytest.mark.xfail(strict=False)`, so they report as expected/non-blocking today and
  would surface as `XPASS` the day that's fixed on the Atlas side, rather than silently staying green
  forever or hard-failing the suite.

**Coverage target:** `uv run pytest -m "not integration" --cov=bookscraper --cov-report=term-missing --cov-fail-under=90`
enforces ≥90% line coverage on `src/bookscraper` (`scripts/*.py` is out of scope). The two
Playwright-DOM-heavy functions — `scrape_details.py`'s `scrape_book()` and `search_utils.py`'s
`get_search_results_via_playwright()` — get happy-path-plus-primary-error-branch coverage rather
than exhaustive per-selector coverage; everything else targets ~100%.

**Shared fixtures** (`tests/conftest.py`, used by `unit/` and `mock/`): `isolated_config` and
`isolated_local_store` monkeypatch `backends/mongo/config.py`'s `CONFIG_DIR`/`SETTINGS_FILE` and
`backends/local/store.py`'s `LOCAL_STORE_FILE` to `tmp_path`, so a test can never read or write the
real `~/.bookscrapper/settings.json` (it holds real credentials) or the repo-root `books.json`.
`no_real_env` strips MongoDB env vars for a test's duration so a prior `load_dotenv()` can't leak
real values in. `tests/integration/conftest.py` additionally provides `require_env(name)`, which
skips (not fails) a test when a required `.env` credential is absent.

Test credentials (`tests/integration/` only) come from `TEST_MONGODB_URI_PASS` / `TEST_MONGODB_URI_TLS`,
set in `.env` (repo root, git-ignored) — env-var only, no fallback file
(`tests/integration/mongo_test_helpers.py`'s `get_mongodb_uri()` takes no fallback path), so a bug in
either the production resolver or a test can never cross-load the other's credentials. The TLS test
still reads the TLS *cert* from the production location (`TLS_CERT_FILE` / the newest
`X509-cert-*.pem`) rather than a test-only copy, since there is one real X.509 identity, not a
separate test one.

**Credential separation (test vs. production):** production auth lives in `~/.bookscrapper/settings.json` (see
Authentication configuration above). Test-only driver strings are `TEST_MONGODB_URI_PASS` / `TEST_MONGODB_URI_TLS`,
set in `.env` (repo root, git-ignored) — env-var only, no fallback file (`mongo_test_helpers.get_mongodb_uri()`
takes no fallback path), so a bug in either the production resolver or a test script can never cross-load the
other's credentials, and there's no separate `~/.bookscrapper/test/` subtree to keep in sync. The TLS test script
still reads the TLS *cert* from the production location (`TLS_CERT_FILE` / the newest `X509-cert-*.pem`) rather
than a test-only copy, since there is one real X.509 identity, not a separate test one.

## Architecture

`src/bookscraper/` is organized into four subpackages by responsibility, plus a small set of root-level entry
points and cross-cutting utilities. Import direction is one-way and there are no cycles:
`commands/` → `scraping/` + `backends/`; `scraping/` → `backends/` (for the `StorageBackend` type) + `book_utils.py`;
`backends/` → `book_utils.py`; nothing under `backends/` or `scraping/` ever imports from `commands/`.

Root-level entry points and shared utilities:
- **`cli.py`** — argparse only: builds the `bookscraper` parser and its two subparsers (`scrape-urls`, `search`).
  Every flag sets an explicit `dest=` (e.g. `--input-file` → `input_file`) so a CLI flag can never silently drift
  from the attribute name the code reads.
- **`main.py`** — the real entry point: parses args via `cli.build_parser()` and dispatches to the matching
  `commands/*.py` module's `run(args)`. `main_sync()` wraps this in `asyncio.run(...)` and is what both the
  `bookscraper` console-script (registered in `pyproject.toml`'s `[project.scripts]`) and `__main__.py` call.
- **`__main__.py`** — enables `python -m bookscraper ...`; just calls `main.main_sync()`.
- **`book_utils.py`** — `print_log()` (color-coded console + plain-text file logging), `configure_logging(level)`
  (attaches a single `FileHandler` to the **root** logger, pointed at a fresh per-run file under
  `~/.bookscrapper/logs/` — see below), pre-flight checks (`check_local_write_permission`, `check_mongodb_connection`),
  `extract_year_from_date` (handles several site-specific date formats), `hash_book`. Stays at the package root
  since every subpackage below imports from it and it has no backend/scraping-specific logic of its own.

**`commands/`** — the two CLI workflows, each calling `resolve_store_backend()` (from `backends/`) once up front:
- **`scrape_urls.py`** — the "I already have URLs" workflow: takes a CSV of book URLs, buckets them by site via
  `identify_website()`, and scrapes each in batches of 10 concurrent Playwright tasks via
  `scraping.scrape_details.scrape_book()`, passing it the resolved `StorageBackend` for inline duplicate checks.
- **`search.py`** — the "discover new books" workflow: for each site in `SITES_TO_SCRAPE` and each query in
  `SEARCH_QUERIES` (both in `scraping/parameters.py`), it searches for candidate books, deduplicates them against
  the resolved storage backend *before* doing the expensive detail scrape, then scrapes details for the survivors.
  Currently only Leanpub is wired end-to-end, entirely via its JSON API through `httpx` (no browser needed).
  `SITES_TO_SCRAPE` is currently `["leanpub"]`.

**`backends/`** — the storage-backend abstraction (see Storage backend above):
- **`storage.py`** — `StorageBackend` (the mongo/json contract), `MongoBackend`, `JsonBackend`, and
  `resolve_store_backend()`. `backends/__init__.py` re-exports all four.
- **`mongo/database.py`** / **`mongo/deduplicate.py`** — two layers of dedup for the `mongo` backend: (1)
  pre-scrape checks by `book_id`/`slug` (Leanpub) or `asin` (Amazon) to avoid re-scraping known books, and (2) a
  unique MongoDB index on a `hash` field (SHA-256 of normalized title+authors+year, computed by
  `book_utils.hash_book`) that rejects duplicate inserts at write time. `database.py` holds the MongoDB
  client/db/collection as module-level globals, lazily initialized by `get_mongo_collection()`; connection details
  come from `config.resolve_mongo_connection()`, not from reading env vars directly.
- **`mongo/config.py`** — `resolve_mongo_connection()`, the deterministic MongoDB auth resolver
  (`~/.bookscrapper/settings.json` when present, else the legacy env-var fallback) shared by `mongo/database.py`
  and `book_utils.py`. See Authentication configuration above.
- **`local/store.py`** — the `json` backend's implementation: `books.json` at the repo root, persistent and
  accumulating across runs, with the same `hash`-uniqueness and `asin`/`book_id`/`slug` lookup semantics as
  `mongo/database.py`'s MongoDB functions (`save_books`, `check_book_exists`, `check_amazon_asin_exists`,
  `leanpub_book_exists`).

**`scraping/`** — everything about talking to the four book sites:
- **`parameters.py`** — `site_constants`: per-site CSS selectors and API URLs (this is what to edit when a site
  changes its markup or a new site is added). Also `SEARCH_QUERIES`, `SITES_TO_SCRAPE`, `SCRAPE_FILTERS`
  (rating thresholds), `USER_AGENTS` for rotation, and `HEADLESS_BROWSER` (currently `False`, i.e. Playwright runs
  headed by default).
- **`scrape_details.py`** — per-site Playwright detail scraping (`scrape_book`) plus Leanpub's `httpx`-based
  `get_leanpub_book_details`. `route_handler` aborts all non-document requests (images/fonts/css) to speed up page
  loads.
- **`search_utils.py`** — collects search-result candidates per site: Leanpub via paginated API calls
  (`get_leanpub_search_results_via_api`), other sites via Playwright (partially implemented).

## Logging

Every run writes to its own file: `~/.bookscrapper/logs/bookscraper_<YYYYMMDD_HHMMSS>_<pid>.log`, computed once as
`LOG_FILE_PATH` when `book_utils.py` is imported (so it's stable for the lifetime of the process even though
`configure_logging()` may be called more than once — see below). After creating a run's file, `_prune_old_logs()`
deletes the oldest files matching `bookscraper_*.log` beyond `MAX_LOG_FILES` (10), keyed on `st_mtime`, so the
directory never exceeds 10 run logs.

All application logging — regardless of which logger a module uses — lands in that file. Several modules create
their own logger via `logging.getLogger(__name__)` or an ad hoc name (`backends/mongo/database.py`,
`scraping/search_utils.py`, `backends/mongo/deduplicate.py`, `scraping/scrape_details.py`, `commands/search.py`)
rather than sharing `book_utils.py`'s `"bookscraper_app"`
logger, so `configure_logging()` attaches the file handler to the **root** logger instead of a specific named one —
every logger propagates there by default, so this is the only placement that reliably captures all of them.
`main.py` calls `configure_logging()` once at startup with the level from `--log-severity {debug|info|warning|error}`
(defined on both `scrape-urls` and `search` in `cli.py`; default `info`). `book_utils.py` also calls
`configure_logging()` once at import time (default `info`) so the logger works for any code that imports it
without going through the CLI — this is safe to call twice per run since it only adds the `FileHandler` (and only
prunes old logs) the first time, keyed on `LOG_FILE_PATH` already being attached. Note: `print_log()`'s colored
console output writes to `sys.stdout` directly, bypassing the logging framework entirely — `--log-severity` only
filters what reaches the log file, not the console, which always prints everything passed to `print_log()`.

## Known inconsistencies to watch for
- `backends/mongo/database.py`'s `get_mongo_collection()` logs `"Connection to MongoDB Atlas successful."`
  unconditionally after calling `_initialize_mongodb_connection()`, even when that call actually failed and
  returned `None` — a pre-existing, cosmetic logging bug (the real success/failure state is still reported
  correctly to the caller via the return value). Not yet fixed; this file wasn't in scope of the CLI restructuring
  that touched its callers.
- Curated input URL lists (`content/urls.csv`, `content/urls_amazon.csv`) live under `content/` and are committed —
  they're hand-maintained candidate lists for `scrape-urls -f`, not run output. Actual run artifacts (`books.json`
  for `--store-backend json`; `failed_books.csv`, `failed_urls.csv`, `other_links.csv` diagnostics regardless of
  backend) are gitignored and written to the repo root by default; don't add new default output filenames without
  gitignoring them too. Unlike the diagnostics, `books.json` is meant to persist across runs — don't delete it
  casually, it's the entire point of the `json` backend (see Storage backend above).
- `init_template.py` and `release_package.py` are leftover from a generic cookiecutter-style Python module template
  (renaming a template project, building/publishing wheels, tagging GitHub releases). They aren't part of the
  scraping workflow and reference a generic `python_module` template name in places. They also both read/write
  `setup.cfg` directly (via `ConfigParser`), which no longer exists after the uv migration — running either script
  as-is will fail. Not fixed, since neither script is part of the actual scraping workflow.
