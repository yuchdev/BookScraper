# CLI Reference

`bookscraper` exposes six subcommands. Run `bookscraper --help` or
`bookscraper <subcommand> --help` for argparse-generated usage, or read this reference
for the complete flag tables, env overrides, and exit-code contract.

## Global options

These options belong to the top-level parser and are not subcommand-specific.

| Flag | `dest` | Default | Env override | Description |
|------|--------|---------|--------------|-------------|
| `-V` / `--version` | — | — | — | Print `bookscraper <version> (Python <x.y.z>, Playwright <v>)` and exit. |

## Exit codes

All subcommands share this contract (source of truth: `src/bookscraper/exitcodes.py`).
Per-subcommand notes appear in each section below.

| Code | Name | Meaning |
|------|------|---------|
| `0` | `OK` | Completed; nothing failed. |
| `1` | `FATAL` | Could not run or was aborted: config error, pre-flight failure, unavailable backend, or an unhandled exception. |
| `2` | `USAGE` | argparse usage error (missing required flag, invalid choice, etc.). |
| `3` | `PARTIAL` | Completed, but at least one item failed; also `rotate-cert --check` when the certificate is expiring soon. |
| `4` | `DRIFT` | `detect-schema` only: at least one current selector matched no sample page. |
| `130` | `INTERRUPTED` | The run was cancelled with Ctrl+C. |

---

## scrape-urls

Scrape book details from URLs listed in a CSV file (via Playwright).

```
bookscraper scrape-urls -f <path> --store-backend {mongo|json} [options]
```

### Flags

| Flag | `dest` | Default | Env override | Description |
|------|--------|---------|--------------|-------------|
| `-f` / `--input-file` | `input_file` | *(required)* | — | Path to the CSV file containing URLs to scrape; must have a `url` column. |
| `--store-backend {mongo,json}` | `store_backend` | *(required)* | — | Storage backend for duplicate checks and writes: `mongo` (MongoDB Atlas) or `json` (local `books.json`). |
| `--headless` / `--no-headless` | `headless` | `None` (falls back to env/parameters.py) | `BOOKSCRAPER_HEADLESS` (`1`/`0`/`true`/`false`) | Run the browser headless or headed. Unset uses `BOOKSCRAPER_HEADLESS`, then `HEADLESS_BROWSER` in `parameters.py`. |
| `--concurrency` | `concurrency` | `4` | — | Maximum detail scrapes in flight at once (1–32). Per-host pacing is the rate limiter's job. |
| `--nav-timeout` | `nav_timeout_s` | `60.0` | — | Per-page navigation timeout in seconds (> 0). |
| `--ignore-robots` | `ignore_robots` | `False` | — | Skip `robots.txt` consultation. Use only for targets you own or are permitted to crawl; prints a one-time warning. |
| `--dry-run` | `dry_run` | `False` | — | Run everything including duplicate-check reads, but save nothing. Prints title/site/hash of each book that would have been saved. |
| `--output-dir` | `output_dir` | `.` | — | Directory for diagnostic CSVs (`failed_*.csv`, `other_links.csv`); created if missing. |
| `--json-store` | `json_store` | `None` | — | Explicit path for the `json` backend's store file (default `books.json`). Invalid with `--store-backend mongo`. |
| `--log-severity {debug,info,warning,error}` | `log_severity` | `info` | — | Minimum severity written to the run's log file under `~/.bookscrapper/logs/`. Console output is unaffected. |

### Examples

```bash
# Scrape a curated URL list and save to the local JSON store:
uv run bookscraper scrape-urls -f content/urls.csv --store-backend json

# Scrape to MongoDB with headed browser and higher concurrency:
uv run bookscraper scrape-urls -f content/urls.csv --store-backend mongo \
  --no-headless --concurrency 8

# Dry run: see what would be saved without touching the store:
uv run bookscraper scrape-urls -f content/urls.csv --store-backend json --dry-run

# Write diagnostic CSVs to a specific directory:
uv run bookscraper scrape-urls -f content/urls.csv --store-backend json \
  --output-dir reports/
```

### Exit codes

Uses the global contract. Exit `3` when at least one URL failed to scrape or save.

---

## search

Search configured sites for books matching queries, deduplicate, and scrape details.

```
bookscraper search --store-backend {mongo|json} [options]
```

### Flags

| Flag | `dest` | Default | Env override | Description |
|------|--------|---------|--------------|-------------|
| `--store-backend {mongo,json}` | `store_backend` | *(required)* | — | Storage backend for duplicate checks and writes. |
| `--site {amazon,packtpub,leanpub,oreilly}` | `sites` | `None` (uses `SITES_TO_SCRAPE` from `parameters.py`) | — | Restrict the search to one site; repeat `--site` to target several. |
| `-q` / `--query` | `queries` | `None` (uses `SEARCH_QUERIES` from `parameters.py`) | — | A search query; repeat to run several. Combines with `--queries-file` (union, order preserved, deduplicated). |
| `--queries-file` | `queries_file` | `None` | — | File of search queries, one per line; blank lines and `#`-prefixed lines are skipped. Combines with `-q`. |
| `--max-search-pages` | `max_search_pages` | `3` | — | Maximum search-result pages per query per site (>= 1). |
| `--no-filters` | `no_filters` | `False` | — | Disable `SCRAPE_FILTERS` rating thresholds; keep every candidate (not yet active - see task 03.3). |
| `--headless` / `--no-headless` | `headless` | `None` | `BOOKSCRAPER_HEADLESS` | Browser headless/headed mode (see `scrape-urls` for resolution order). |
| `--concurrency` | `concurrency` | `4` | — | Maximum detail scrapes in flight at once (1–32). |
| `--nav-timeout` | `nav_timeout_s` | `60.0` | — | Per-page navigation timeout in seconds (> 0). |
| `--ignore-robots` | `ignore_robots` | `False` | — | Skip `robots.txt` consultation. |
| `--dry-run` | `dry_run` | `False` | — | Run everything including duplicate-check reads, but save nothing. |
| `--output-dir` | `output_dir` | `.` | — | Directory for diagnostic CSVs; created if missing. |
| `--json-store` | `json_store` | `None` | — | Explicit path for the `json` backend's store file. Invalid with `--store-backend mongo`. |
| `--log-severity {debug,info,warning,error}` | `log_severity` | `info` | — | Minimum severity written to the run log file. |

### Examples

```bash
# Search Leanpub with configured queries, save to MongoDB:
uv run bookscraper search --store-backend mongo

# Search Leanpub and Amazon for a specific query, up to 5 pages:
uv run bookscraper search --store-backend json --site leanpub --site amazon \
  -q "Python" --max-search-pages 5

# Dry run: check what new books would be scraped without saving:
uv run bookscraper search --store-backend json --dry-run

# Load queries from a file and ignore rating filters:
uv run bookscraper search --store-backend json --queries-file my-queries.txt --no-filters
```

### Exit codes

Uses the global contract. Exit `3` when at least one item failed to scrape or save.

---

## rotate-cert

Issue a new MongoDB Atlas X.509 client certificate via the Atlas Admin API, write it to
`~/.bookscrapper/`, and update `settings.json` if it references the cert by filename.

Requires `ATLAS_CLIENT_ID`, `ATLAS_CLIENT_SECRET`, `ATLAS_PROJECT_ID`, and `ATLAS_DB_USER`
in `.env` or the environment (not needed for `--check`).

See [/docs/runbooks/x509-certificate-rotation.md](/docs/runbooks/x509-certificate-rotation.md)
for the operator runbook.

```
bookscraper rotate-cert [options]
bookscraper rotate-cert --check [--warn-days N]
```

### Flags

| Flag | `dest` | Default | Env override | Description |
|------|--------|---------|--------------|-------------|
| `--months` | `months` | `None` (uses env, else `6`) | `ATLAS_CERT_MONTHS` | Certificate validity in months, 1–24. |
| `--output` | `output` | `None` (uses env, else timestamped `X509-cert-*.pem`) | `ATLAS_CERT_FILE` | Path to write the new certificate. |
| `--check` | `check` | `False` | — | Report the currently active certificate's expiry without rotating. No Atlas contact; no `ATLAS_*` vars needed. |
| `--warn-days` | `warn_days` | `30` | — | With `--check`: days before expiry that trigger exit code `3`. |
| `--no-verify` | `no_verify` | `False` | — | Skip the post-rotation Atlas ping. Use for offline or first-time setups; settings.json is repointed without verification. |
| `--verify-attempts` | `verify_attempts` | `5` | — | Retry count for the post-rotation Atlas ping (Atlas can take a moment to propagate a new cert). |
| `--keep` | `keep` | `2` | — | After a successful rotation, keep only the newest N `X509-cert-*.pem` files (plus the active cert). Must be >= 1. |
| `--no-prune` | `no_prune` | `False` | — | Disable pruning of superseded certificate files after rotation. |
| `--log-severity {debug,info,warning,error}` | `log_severity` | `info` | — | Minimum severity written to the run log file. |

### Examples

```bash
# Rotate with defaults (6-month cert, verify against Atlas, prune keeping 2):
uv run bookscraper rotate-cert

# 12-month cert, written to a custom path:
uv run bookscraper rotate-cert --months 12 --output ~/.bookscrapper/X509-cert-manual.pem

# Skip Atlas verification (first-time or offline):
uv run bookscraper rotate-cert --no-verify

# More retries for slow Atlas propagation:
uv run bookscraper rotate-cert --verify-attempts 10

# Keep more historical certs after rotation:
uv run bookscraper rotate-cert --keep 5

# Disable pruning entirely:
uv run bookscraper rotate-cert --no-prune

# Check active cert expiry only (no Atlas contact, no ATLAS_* vars needed):
uv run bookscraper rotate-cert --check
uv run bookscraper rotate-cert --check --warn-days 14
```

### Exit codes

Standard `0`/`1`/`2`/`130` apply. **`--check` has its own sub-contract:**

| Code | `--check` meaning |
|------|-------------------|
| `0` | More than `--warn-days` remain (or no X.509 cert is configured). |
| `1` | Certificate is expired or unreadable. |
| `3` | Certificate expires within `--warn-days` (safe for cron/CI alerting). |

---

## migrate

Rewrite stored v1 book documents to the canonical schema-2 `BookRecord` shape, recomputing
each document's hash. Dry-run by default.

```
bookscraper migrate --store-backend {mongo|json} [--apply]
```

### Flags

| Flag | `dest` | Default | Env override | Description |
|------|--------|---------|--------------|-------------|
| `--store-backend {mongo,json}` | `store_backend` | *(required)* | — | Storage backend to migrate. |
| `--apply` | `apply` | `False` | — | Write the migrated documents to the store. Without this flag the command is a dry run that reports changes and writes nothing. |
| `--log-severity {debug,info,warning,error}` | `log_severity` | `info` | — | Minimum severity written to the run log file. |

### Examples

```bash
# Dry run: see what would change (writes nothing):
uv run bookscraper migrate --store-backend json

# Apply migration to the local JSON store:
uv run bookscraper migrate --store-backend json --apply

# Apply migration to MongoDB Atlas:
uv run bookscraper migrate --store-backend mongo --apply
```

### Exit codes

Uses the global contract. A migration report JSON is always written to
`~/.bookscrapper/logs/` regardless of `--apply`. Hash collisions are reported for human
review and never cause a non-zero exit by themselves.

---

## doctor

Report the health of the local setup in one screen: toolchain, config, cert, backends.
Offline and instant by default; pass `--online` for the MongoDB Atlas ping.

```
bookscraper doctor [--store-backend {mongo|json}] [--online]
```

### Flags

| Flag | `dest` | Default | Env override | Description |
|------|--------|---------|--------------|-------------|
| `--store-backend {mongo,json}` | `store_backend` | `None` (reports all) | — | Optional. A `json` value skips the MongoDB Atlas ping. Omit to report every backend. |
| `--online` | `online` | `False` | — | Also run network checks (currently the MongoDB Atlas ping). Off by default so `doctor` is instant and offline-safe. |
| `--log-severity {debug,info,warning,error}` | `log_severity` | `info` | — | Minimum severity written to the run log file. |

### Examples

```bash
# Full offline health check:
uv run bookscraper doctor

# Check with Atlas ping:
uv run bookscraper doctor --online

# Report only the json backend (skips Atlas ping):
uv run bookscraper doctor --store-backend json

# Report mongo backend with online ping:
uv run bookscraper doctor --store-backend mongo --online
```

### Exit codes

Exit `1` if any check reports FAIL. A WARN never causes a non-zero exit.

---

## detect-schema

Propose CSS-selector updates for a site's detail or search pages via Playwright + Claude.
Validates every proposal against each sample page's live DOM and prints a diff-style report.
Never writes to `parameters.py`; requires `ANTHROPIC_API_KEY` unless `--replay` or
`--check-current-only` is used.

```
bookscraper detect-schema --site <site> (--url URL... | --html-file PATH...) [options]
```

`--url` and `--html-file` are mutually exclusive; exactly one must be supplied.

### Flags

| Flag | `dest` | Default | Env override | Description |
|------|--------|---------|--------------|-------------|
| `--site {amazon,packtpub,leanpub,oreilly}` | `site` | *(required)* | — | Which site's selector schema to examine. |
| `--url` | `url` | *(required unless `--html-file`)* | — | A sample page URL; repeat for multiple. Mutually exclusive with `--html-file`. |
| `--html-file` | `html_file` | *(required unless `--url`)* | — | A local HTML file as a sample page (fully offline). Repeat for multiple. Mutually exclusive with `--url`. |
| `--page-type {detail,search}` | `page_type` | `detail` | — | Page kind: `detail` for book detail pages, `search` for search-results listings. |
| `--headless` / `--no-headless` | `headless` | `None` | `BOOKSCRAPER_HEADLESS` | Browser headless/headed mode (see `scrape-urls` for resolution order). |
| `--nav-timeout` | `nav_timeout_s` | `60.0` | — | Per-page navigation timeout in seconds (> 0). |
| `--model` | `model` | `None` (uses env, else `claude-sonnet-5`) | `ANTHROPIC_MODEL` | Anthropic model ID. An empty value is rejected. |
| `--max-input-tokens` | `max_input_tokens` | `40000` | — | Per-request input-token budget; an over-budget prompt is re-pruned toward a 10 000-char HTML floor. |
| `--replay` | `replay` | `None` | — | Path to a JSON file of recorded AI responses (keyed by `sha256(prompt)`). No API call is made; `ANTHROPIC_API_KEY` not required. |
| `--record` | `record` | `None` | — | Path to write this live run's AI responses for later `--replay`. Ignored in replay mode; never contains the API key. |
| `--format {text,json}` | `format` | `text` | — | Report format: `text` (diff-style, default) or `json` (machine-readable CI artifact; status lines go to stderr). |
| `--check-current-only` | `check_current_only` | `False` | — | Skip the AI step; only re-query the current `site_constants` selectors. No API key needed; with `--html-file` makes zero network calls. |
| `--output` | `output` | `None` | — | Optional file path to also write the report to (it is always printed to stdout). |
| `--log-severity {debug,info,warning,error}` | `log_severity` | `info` | — | Minimum severity written to the run log file. |

### Examples

```bash
# Propose selectors for Amazon's detail page against two sample URLs:
uv run bookscraper detect-schema --site amazon \
  --url "https://www.amazon.com/dp/1098131029" \
  --url "https://www.amazon.com/dp/1492051365"

# Save the text report to a file:
uv run bookscraper detect-schema --site leanpub \
  --url "https://leanpub.com/some-book" --output schema-report.txt

# Use a specific model with a larger token budget:
uv run bookscraper detect-schema --site amazon \
  --url "https://www.amazon.com/dp/1098131029" \
  --model claude-sonnet-5 --max-input-tokens 30000

# Offline drift check only (no AI, no network, no API key):
uv run bookscraper detect-schema --site amazon \
  --html-file tests/fixtures/amazon/detail.html --check-current-only

# JSON report for CI drift detection:
uv run bookscraper detect-schema --site leanpub \
  --html-file tests/fixtures/leanpub/detail.html \
  --check-current-only --format json

# Replay a recorded run (no API key needed):
uv run bookscraper detect-schema --site amazon \
  --html-file tests/fixtures/amazon/detail.html \
  --replay tests/fixtures/schema_detection/amazon.replay.json

# Propose search-page selectors:
uv run bookscraper detect-schema --site amazon --page-type search \
  --url "https://www.amazon.com/s?k=python"
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | All current selectors matched at least one sample page; no drift. |
| `1` | A `SchemaDetectionError` occurred (missing API key, page-load failure, malformed AI response). |
| `4` | Drift: at least one current (non-optional) selector matched no sample page. |
