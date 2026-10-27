# BookScraper

[![Lint](https://github.com/yuchdev/BookScraper/actions/workflows/lint.yml/badge.svg)](https://github.com/yuchdev/BookScraper/actions/workflows/lint.yml)
[![Tests](https://github.com/yuchdev/BookScraper/actions/workflows/tests.yml/badge.svg)](https://github.com/yuchdev/BookScraper/actions/workflows/tests.yml)
![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fyuchdev%2FBookScraper%2Fmaster%2Fpyproject.toml&style=for-the-badge&logo=python)
![Playwright](https://img.shields.io/badge/Playwright-Chromium-green?style=for-the-badge&logo=playwright)
![MongoDB](https://img.shields.io/badge/MongoDB-4.0%2B-47A248?style=for-the-badge&logo=mongodb)
![License](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)

`bookscraper` is an asyncio + Playwright scraper that extracts book metadata (title, authors, ISBNs,
publication date, description, tags) from Amazon, Packtpub, Leanpub, and O'Reilly, and persists results
to a MongoDB Atlas collection or a local `books.json` store.

---

## Features

- **Four scraping targets:** Amazon, Packtpub, Leanpub, and O'Reilly
- **Two interchangeable storage backends:** `json` (local `books.json`, persistent, accumulates across
  runs) and `mongo` (MongoDB Atlas, unique hash index); `--store-backend {mongo|json}` is required and
  explicit on every scraping subcommand — there is no implicit default
- **Six subcommands:** `scrape-urls`, `search`, `rotate-cert`, `detect-schema`, `migrate`, `doctor`
- **AI-assisted schema detection (`detect-schema`):** when a site's markup drifts, proposes updated CSS
  selectors via Playwright + Claude and validates them against live DOM samples; never auto-applies to
  `parameters.py`; needs `ANTHROPIC_API_KEY`. See
  [`docs/scraping/schema-detection.md`](docs/scraping/schema-detection.md)
- **Opt-in LLM extraction fallback (`--ai-fallback`):** when a deterministic selector leaves an eligible
  field (description, ISBN-10/13, publication date, authors) empty, asks Claude for just that field from
  the already-loaded page HTML, then validates the answer through deterministic evidence and normalizer
  gates before storing. Off by default; never touches the title (which feeds the dedup hash); needs
  `ANTHROPIC_API_KEY`
- **Politeness:** per-host rate limiting and `robots.txt` consultation (`--ignore-robots` disables it,
  prints a one-time warning, and is recorded in the run report)
- **X.509 certificate rotation (`rotate-cert`):** issues a fresh Atlas certificate via the Admin API,
  verifies it authenticates, repoints `settings.json`, and prunes superseded certificates. See
  [`docs/runbooks/x509-certificate-rotation.md`](docs/runbooks/x509-certificate-rotation.md)
- **Deterministic configuration:** `~/.bookscrapper/settings.json` is the sole auth source of truth when
  present; a missing field or unrecognised key raises a loud error and aborts rather than silently
  falling back. See [CLAUDE.md — Authentication configuration](CLAUDE.md)
- **Exit codes:** `0` OK, `1` FATAL, `3` PARTIAL, `4` DRIFT, `130` interrupted. Full contract in
  [`docs/cli/cli.md`](docs/cli/cli.md)
- **Structured per-run logging:** timestamped file under `~/.bookscrapper/logs/` (10 most recent kept);
  `--log-severity {debug|info|warning|error}` controls the file threshold (console always shows
  everything)

---

## Quick Start

```bash
git clone https://github.com/yuchdev/BookScraper.git
cd BookScraper
uv sync
uv run playwright install chromium
uv run bookscraper doctor
uv run bookscraper search --store-backend json --query Python --dry-run
```

`uv sync` creates `.venv`, installs pinned dependencies from `uv.lock`, and registers the `bookscraper`
console script — there is no separate `pip install` step. `uv run` executes inside `.venv` without
activating it first; alternatively run `source .venv/bin/activate` and drop the `uv run` prefix.

---

## Installation

### Prerequisites

- **Python 3.13+** from [python.org](https://www.python.org/downloads/)
- **[uv](https://docs.astral.sh/uv/)** for dependency and environment management:

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

- **Playwright Chromium** (installed after `uv sync`):

  ```bash
  uv run playwright install chromium
  ```

- **MongoDB Atlas cluster** (optional — only needed for `--store-backend mongo`)

### Steps

1. Clone the repository:

   ```bash
   git clone https://github.com/yuchdev/BookScraper.git
   cd BookScraper
   ```

2. Install dependencies and create the virtual environment:

   ```bash
   uv sync
   ```

   `uv sync` reads `pyproject.toml` / `uv.lock`, creates `.venv`, installs all pinned dependencies,
   and installs the `bookscraper` package itself in editable mode — there is no separate
   `pip install -e .` step.

3. Install Playwright browser binaries:

   ```bash
   uv run playwright install chromium
   ```

4. Verify the setup:

   ```bash
   uv run bookscraper doctor
   ```

---

## Configuration

`~/.bookscrapper/settings.json` is the preferred, deterministic auth source when present — its declared
`auth_type` and `{"source": "env"|"literal", "value": "..."}` field shapes are followed exactly, with
no fallback chain. A missing env var or unrecognised top-level key raises `ConfigError` immediately.

See **[CLAUDE.md — Authentication configuration](CLAUDE.md)** for the complete reference: password
(SCRAM) and X.509 schemas, the optional `database` block, the strict unknown-key rejection policy, and
the POSIX permission-hardening warnings. A copy-ready example lives at
[`docs/examples/settings.example.json`](docs/examples/settings.example.json).

**Legacy `.env` fallback** (no `settings.json`): place `MONGODB_URI` and optionally `TLS_CERT_FILE` in
a `.env` file at the repository root — see `CLAUDE.md` *Authentication configuration* for details and
caveats.

For X.509 certificate lifecycle (issuance, verification, rotation schedule, recovery procedures), see
the operator runbook:
[`docs/runbooks/x509-certificate-rotation.md`](docs/runbooks/x509-certificate-rotation.md).

`ANTHROPIC_API_KEY` is read only when `detect-schema` or `--ai-fallback` is used; none of the other
subcommands read it. An optional `ANTHROPIC_MODEL` overrides the default model for both.

---

## Usage

`bookscraper` has six subcommands. The complete flag reference, env overrides, and exit-code contract
for each live in [`docs/cli/cli.md`](docs/cli/cli.md). Run `bookscraper <subcommand> --help` for a
quick argparse summary.

```bash
# Check the local setup (toolchain, config, cert, backends) — offline and instant:
uv run bookscraper doctor

# Scrape a curated list of URLs, save to the local JSON store:
uv run bookscraper scrape-urls -f content/urls.csv --store-backend json

# Search configured sites for new books, save to MongoDB Atlas:
uv run bookscraper search --store-backend mongo

# Search for a specific topic without saving anything:
uv run bookscraper search --store-backend json --query Python --dry-run

# Scrape with the opt-in LLM extraction fallback enabled (needs ANTHROPIC_API_KEY):
uv run bookscraper scrape-urls -f content/urls.csv --store-backend json --ai-fallback

# Check the active X.509 certificate's expiry without contacting Atlas:
uv run bookscraper rotate-cert --check --warn-days 14
```

`--store-backend {mongo|json}` is required for `scrape-urls`, `search`, and `migrate`; omitting it is
an argparse error (exit `2`).

### scrape-urls

Takes a CSV file (with a `url` column), buckets URLs by site, and scrapes each with Playwright.
Supports `--concurrency` (default: 4, max: 32) and `--dry-run` (reads the backend for duplicate
detection but saves nothing).

Example CSV:

```csv
url
https://www.amazon.com/Product-Title/dp/B00ABCD123
https://www.packtpub.com/product/another-book/9781234567890
https://leanpub.com/your-great-book
https://www.oreilly.com/library/view/programming-language/9780123456789/
```

### search

Searches configured sites for books matching queries, deduplicates against the chosen backend before
the expensive detail scrape, then scrapes details for new results. Restrict to a site with
`--site {amazon|packtpub|leanpub|oreilly}` and supply queries with `-q`/`--query` or `--queries-file`.
Currently only Leanpub is wired end-to-end.

### migrate

Rewrites stored pre-03.1 v1 documents to the canonical schema-2 `BookRecord` shape, recomputing each
document's hash. Dry-run by default (`--apply` writes). Idempotent; never deletes a document; writes a
migration report JSON to `~/.bookscrapper/logs/`.

```bash
uv run bookscraper migrate --store-backend json
```

---

## Storage Backends

Both backends store the same canonical `BookRecord` shape and enforce uniqueness by a SHA-256 hash of
normalised title + authors + year:

| Backend | Implementation | Persists across runs |
|---------|----------------|---------------------|
| `json` | `books.json` at repo root; atomic write (temp + `fsync` + `os.replace`); one-generation `.bak` backup before each replace | Yes |
| `mongo` | MongoDB Atlas collection; unique index on `hash`; `insert_many(ordered=False)` | Yes |

A corrupt `books.json` (invalid JSON) is refused with `LocalStoreCorruptError` rather than overwritten.
Override the store path with `--json-store PATH`.

Diagnostic artifacts (`failed_books.csv`, `failed_urls.csv`, `other_links.csv`) are always written as
CSV best-effort alongside the main store — they are not part of book storage and are not controlled by
`--store-backend`. Override their output directory with `--output-dir`.

---

## Logging

Each run writes its own timestamped file:
`~/.bookscrapper/logs/bookscraper_<YYYYMMDD_HHMMSS>_<pid>.log`. The 10 most recent files are kept;
older ones are pruned at the start of each new run. `--log-severity {debug|info|warning|error}` sets
the minimum severity written to the log file (default: `info`). Console output is always full-detail
regardless of the configured severity.

---

## Development

### Tests

Three tiers run under `pytest`:

- **`tests/unit/`** — pure logic, no mocks, no network
- **`tests/mock/`** — full subcommand coverage with mocked external dependencies; includes an offline
  HTML fixture harness (real captured pages served locally via Playwright and `httpx`)
- **`tests/integration/`** — hits real MongoDB Atlas; tagged `@pytest.mark.integration` and excluded
  from the default run

```bash
uv run pytest           # unit + mock tiers
uv run pytest --cov     # same, with combined line+branch coverage report
uv run pytest -m integration   # Atlas integration tier (needs .env credentials)
```

See [`docs/test/code_test_coverage.md`](docs/test/code_test_coverage.md) for the 90% combined
line+branch coverage gate, per-module floors, and the allowed `pragma: no cover` patterns.

### HTML Fixture Corpus

`tests/mock/scraping/html/` runs the unmodified scraper against committed real-HTML fixtures for golden
(characterisation) tests. Refresh a site's fixtures with:

```bash
uv run python scripts/capture_fixtures.py --refresh --site leanpub
```

See [`docs/test/html-fixtures.md`](docs/test/html-fixtures.md) for the full refresh-and-drift workflow.

### CI

Three GitHub Actions workflows (lint, test + coverage, optional Atlas integration) run on every push
and pull request. See [`docs/dev/ci.md`](docs/dev/ci.md).

---

## Documentation

- [`docs/cli/cli.md`](docs/cli/cli.md) — full CLI reference (flags, defaults, env overrides, exit codes)
- [`docs/dev/ci.md`](docs/dev/ci.md) — CI workflows and required checks
- [`docs/runbooks/x509-certificate-rotation.md`](docs/runbooks/x509-certificate-rotation.md) — X.509
  rotation operator runbook
- [`docs/scraping/schema-detection.md`](docs/scraping/schema-detection.md) — AI-assisted schema
  detection guide
- [`docs/adr/0002-ai-assisted-schema-detection.md`](docs/adr/0002-ai-assisted-schema-detection.md) —
  schema detection ADR
- [`docs/adr/0003-llm-extraction-fallback.md`](docs/adr/0003-llm-extraction-fallback.md) — LLM
  extraction fallback ADR
- [`docs/test/code_test_coverage.md`](docs/test/code_test_coverage.md) — coverage gate reference
- [`docs/test/html-fixtures.md`](docs/test/html-fixtures.md) — fixture corpus guide
- [`docs/examples/settings.example.json`](docs/examples/settings.example.json) — settings.json template

---

## Contributing

Bug reports and pull requests are welcome. Open an issue or submit a PR against the `master` branch.

---

## License

MIT License — see [LICENSE](LICENSE) for details.
