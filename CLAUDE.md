# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

`bookscraper` is an asyncio + Playwright scraper that pulls book metadata (title, authors, ISBNs, publication date,
description, tags) from Amazon, Packtpub, Leanpub, and O'Reilly, and writes results to CSV and/or a MongoDB Atlas
collection. It exposes one CLI (`bookscraper`) with two subcommands covering two distinct workflows — see
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

MongoDB output requires a `.env` file in the repo root:
```dotenv
MONGODB_URI="mongodb+srv://<user>:<pass>@<cluster>/<db>?retryWrites=true&w=majority"
TLS_CERT_FILE="/path/to/cert.pem"   # optional, for X.509 client-cert auth
TLS_CA_FILE="/path/to/ca.pem"       # optional, for server CA verification (database.py only)
```

If `MONGODB_URI` isn't set, `~/.bookscrapper/driver_string.txt` is read as a fallback; 
if `TLS_CERT_FILE` isn't set, `~/.bookscrapper/X509-cert-142838411852079927.pem` is used if present.

## Running

```bash
# `uv sync` (see Setup above) already registers the `bookscraper` console script inside .venv.

# scrape-urls: scrapes a fixed list of URLs from a CSV (works for all 4 sites via Playwright).
uv run bookscraper scrape-urls -f content/urls.csv -c        # -c/--output-to-csv
uv run bookscraper scrape-urls -f content/urls.csv -m        # -m/--output-to-mongo
uv run bookscraper scrape-urls -f content/urls.csv -c -m     # both

# search: searches sites for books matching queries defined in parameters.py, then scrapes details.
uv run bookscraper search -c
uv run bookscraper search -m --max-search-pages 5

# Without `uv run` (inside an activated .venv), or via `python -m` directly:
python -m bookscraper scrape-urls -f content/urls.csv -c
python -m bookscraper search -c
```

If neither `-c`/`--output-to-csv` nor `-m`/`--output-to-mongo` is passed, both subcommands run pre-flight checks
(CSV write permission, MongoDB ping) via `output.resolve_output_destinations()` and then interactively prompt for
`(C)SV`, `(M)ongoDB`, `(B)oth`, or `(E)xit`.

## Testing

There is no real test suite. `test/bookscraper_test.py` is an empty `unittest` placeholder. `test/connection_test.py`
is a standalone diagnostic script (not pytest-based) for verifying MongoDB Atlas connectivity independent of the
package's import structure — run it directly with `python test/connection_test.py`.

## Architecture

CLI wiring lives directly under `src/bookscraper/`:
- **`cli.py`** — argparse only: builds the `bookscraper` parser and its two subparsers (`scrape-urls`, `search`).
  Every flag sets an explicit `dest=` (e.g. `--input-file` → `input_file`) so a CLI flag can never silently drift
  from the attribute name the code reads.
- **`main.py`** — the real entry point: parses args via `cli.build_parser()` and dispatches to the matching
  `commands/*.py` module's `run(args)`. `main_sync()` wraps this in `asyncio.run(...)` and is what both the
  `bookscraper` console-script (registered in `pyproject.toml`'s `[project.scripts]`) and `__main__.py` call.
- **`__main__.py`** — enables `python -m bookscraper ...`; just calls `main.main_sync()`.
- **`output.py`** — `resolve_output_destinations()`, the pre-flight-checks + interactive `(C)/(M)/(B)/(E)` prompt
  logic shared by both subcommands.

**`commands/scrape_urls.py`** — the "I already have URLs" workflow: takes a CSV of book URLs, buckets them by site
via `identify_website()`, and scrapes each in batches of 10 concurrent Playwright tasks via
`scrape_details.scrape_book()`.

**`commands/search.py`** — the "discover new books" workflow: for each site in `SITES_TO_SCRAPE` and each query in
`SEARCH_QUERIES` (both in `parameters.py`), it searches for candidate books, deduplicates them against MongoDB
*before* doing the expensive detail scrape, then scrapes details for the survivors. Currently only Leanpub is wired
end-to-end, entirely via its JSON API through `httpx` (no browser needed). `SITES_TO_SCRAPE` is currently
`["leanpub"]`.

Other shared modules under `src/bookscraper/`:
- **`parameters.py`** — `site_constants`: per-site CSS selectors and API URLs (this is what to edit when a site
  changes its markup or a new site is added). Also `SEARCH_QUERIES`, `SITES_TO_SCRAPE`, `SCRAPE_FILTERS`
  (rating thresholds), `USER_AGENTS` for rotation, and `HEADLESS_BROWSER` (currently `False`, i.e. Playwright runs
  headed by default).
- **`scrape_details.py`** — per-site Playwright detail scraping (`scrape_book`) plus Leanpub's `httpx`-based
  `get_leanpub_book_details`. `route_handler` aborts all non-document requests (images/fonts/css) to speed up page
  loads.
- **`search_utils.py`** — collects search-result candidates per site: Leanpub via paginated API calls
  (`get_leanpub_search_results_via_api`), other sites via Playwright (partially implemented).
- **`deduplicate.py`** / **`database.py`** — two layers of dedup: (1) pre-scrape checks against MongoDB by
  `book_id`/`slug` (Leanpub) or `asin` (Amazon) to avoid re-scraping known books, and (2) a unique MongoDB index on
  a `hash` field (SHA-256 of normalized title+authors+year, computed by `book_utils.hash_book`) that rejects
  duplicate inserts at write time. `database.py` holds the MongoDB client/db/collection as module-level globals,
  lazily initialized by `get_mongo_collection()`.
- **`book_utils.py`** — `print_log()` (color-coded console + plain-text file logging), `configure_logging(level)`
  (attaches a single `FileHandler` to the **root** logger, pointed at a fresh per-run file under
  `~/.bookscrapper/logs/` — see below), pre-flight checks (`check_csv_write_permission`, `check_mongodb_connection`),
  `extract_year_from_date` (handles several site-specific date formats), `hash_book`.

## Logging

Every run writes to its own file: `~/.bookscrapper/logs/bookscraper_<YYYYMMDD_HHMMSS>_<pid>.log`, computed once as
`LOG_FILE_PATH` when `book_utils.py` is imported (so it's stable for the lifetime of the process even though
`configure_logging()` may be called more than once — see below). After creating a run's file, `_prune_old_logs()`
deletes the oldest files matching `bookscraper_*.log` beyond `MAX_LOG_FILES` (10), keyed on `st_mtime`, so the
directory never exceeds 10 run logs.

All application logging — regardless of which logger a module uses — lands in that file. Several modules create
their own logger via `logging.getLogger(__name__)` or an ad hoc name (`database.py`, `search_utils.py`,
`deduplicate.py`, `scrape_details.py`, `commands/search.py`) rather than sharing `book_utils.py`'s `"bookscraper_app"`
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
- `database.py`'s `get_mongo_collection()` logs `"Connection to MongoDB Atlas successful."` unconditionally after
  calling `_initialize_mongodb_connection()`, even when that call actually failed and returned `None` — a
  pre-existing, cosmetic logging bug (the real success/failure state is still reported correctly to the caller via
  the return value). Not yet fixed; `database.py` wasn't in scope of the CLI restructuring that touched this file's
  callers.
- Curated input URL lists (`content/urls.csv`, `content/urls_amazon.csv`) live under `content/` and are committed —
  they're hand-maintained candidate lists for `scrape-urls -f`, not run output. Actual run artifacts (`books.csv`,
  `failed_books.csv`, `scraped_books.csv`, `other_links.csv`, `failed_urls.csv`) are gitignored and written to the
  repo root by default; don't add new default output filenames without gitignoring them too.
- `init_template.py` and `release_package.py` are leftover from a generic cookiecutter-style Python module template
  (renaming a template project, building/publishing wheels, tagging GitHub releases). They aren't part of the
  scraping workflow and reference a generic `python_module` template name in places. They also both read/write
  `setup.cfg` directly (via `ConfigParser`), which no longer exists after the uv migration — running either script
  as-is will fail. Not fixed, since neither script is part of the actual scraping workflow.
