# BookScraper

![Python](https://img.shields.io/badge/Python-3.13%2B-blue?style=for-the-badge&logo=python)
![Playwright](https://img.shields.io/badge/Playwright-Chromium-green?style=for-the-badge&logo=playwright)
![MongoDB](https://img.shields.io/badge/MongoDB-4.0%2B-47A248?style=for-the-badge&logo=mongodb)
![License](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)

## 📚 Overview

`BookScraper` is a powerful and robust web scraping tool designed to extract detailed information about books from
various online retailers and publishing platforms. Built with `asyncio` and `Playwright`, it efficiently navigates web
pages, collects data, and stores it in either a MongoDB database or a local JSON file with the same document schema.

The project emphasizes reliability with features like dynamic user-agent rotation, retry mechanisms, and pre-flight
checks for output destinations, ensuring a smooth and resilient scraping experience.

---

## 🚀 Quick Start During Development

To quickly start the application at this stage of development:

1. cd into the BookScraper project folder
2. run `uv sync` to create `.venv` and install dependencies (this also registers the `bookscraper` command inside
   the venv), then `uv run playwright install chromium`
3. **Step 1 - scrape** into the local staging file (no database needed):
   `uv run bookscraper search` (or `uv run bookscraper scrape-urls -f content/urls.json`)
4. **Review** `staging/books.json` in any editor - fix fields, or mark a document `"_staging": {"status": "rejected"}`
5. **Step 2 - push** to MongoDB Atlas (needs the `.env` below): `uv run bookscraper push --dry-run`, then
   `uv run bookscraper push`
    ```dotenv
    MONGODB_URI="mongodb+srv://<username>:<password>@<cluster-url>/<database-name>?retryWrites=true&w=majority"
    TLS_CERT_FILE="/path/to/your/tls_certificate.pem" # Optional if not using client certificates
    ```

---

## ✨ Features

* **Multi-Site Scraping:** Currently supports scraping book details from:
    * Amazon (`amazon.com`)
    * Packtpub (`packtpub.com`)
    * Leanpub (`leanpub.com`)
    * O'Reilly (`oreilly.com`)
* **AI-Assisted Schema Detection:** The `detect-schema` subcommand loads sample detail pages with Playwright,
  asks Claude (via the `anthropic` SDK) to propose CSS selectors for the fields in `site_constants[site]`, validates
  every proposal against each page's live DOM, and prints a diff-style report — it *proposes* selector updates for a
  human to apply and never rewrites the schema itself. Needs `ANTHROPIC_API_KEY`. See
  [`docs/scraping/schema-detection.md`](docs/scraping/schema-detection.md).
* **Asynchronous & Parallel Processing:** Leverages `asyncio` and `Playwright` for high-performance concurrent scraping.
* **Robustness:**
    * **Retry Mechanism:** Automatic retries for failed page loads or scraping errors.
    * **User-Agent Rotation:** Randomly selects user-agents to mimic natural Browse patterns and avoid detection.
    * **Resource Blocking:** Blocks unnecessary resources (images, media, ads) for faster and more efficient scraping.
    * **404 Page Detection:** Identifies and skips URLs leading to 404 (Not Found) pages.
* **Review Before It Reaches the Database** ([ADR 0003](docs/adr/0003-staged-review-and-two-way-sync.md)):
    * **Step 1 - scrape to a staging file:** `scrape-urls` and `search` only ever append to `staging/books.json`; your
      edits in that file are never overwritten by later scrapes.
    * **Step 2 - `push`:** validates the whole file, shows a dry-run plan (insert / duplicate / update / unchanged),
      and applies it to MongoDB Atlas in one bulk write. Updates to existing documents need `--allow-updates`;
      nothing is ever deleted.
    * **Reverse - `pull`:** exports the entire MongoDB collection to the same JSON format, so you can bulk-edit it and
      push the corrections back.
* **JSON Everywhere:** Input URL lists, the staging file, exports, and diagnostic reports are all JSON.
* **Fail Loudly:** a corrupt or invalid staging file stops the run with every problem listed - nothing is guessed,
  nothing half-written.
* **Comprehensive Logging:** Detailed console output for real-time progress and a timestamped per-run log file under
  `~/.bookscrapper/logs/` (the 10 most recent runs are kept) for in-depth debugging and historical records, with a
  configurable `--log-severity` threshold.
* **Command-Line Interface (CLI):** Easy-to-use arguments for specifying input file and output preferences.

---

## 🚀 Getting Started

These instructions will get you a copy of the project up and running on your local machine for development and testing
purposes.

### Prerequisites

* **Python 3.13+**: Make sure you have a compatible Python version installed. You can download it
  from [python.org](https://www.python.org/downloads/).
* **[uv](https://docs.astral.sh/uv/)**: This project uses `uv` for dependency management, virtual environments, and
  packaging (it replaces `pip` + `venv` + `setup.py`). Install it with:
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```
* **Playwright Browsers**: Playwright requires browser binaries. After installing project dependencies, you'll need to
  install them:
    ```bash
    # (after uv sync)
    uv run playwright install chromium
    ```
* **MongoDB Atlas Cluster (for `push` / `pull`)**: Scraping into the staging file needs no database; to push it, you'll need access to a
  MongoDB Atlas cluster. Refer to the [MongoDB Atlas documentation](https://www.mongodb.com/cloud/atlas/getting-started)
  for setup instructions. You'll need a `MONGODB_URI` and potentially a `TLS_CERT_FILE`.

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/YannickLalonde/BookScraper.git
   cd BookScraper
   ```
2. **Install dependencies and create the virtual environment:**
   ```bash
   uv sync
   ```
   `uv sync` reads `pyproject.toml`/`uv.lock`, creates `.venv` automatically, installs all dependencies pinned in
   `uv.lock`, and installs the `bookscraper` package itself in editable mode (registering the `bookscraper` console
   script inside `.venv`) — there's no separate `pip install -e .` step.
3. **Install Playwright browser binaries:**
   ```bash
   uv run playwright install chromium
   ```
4. **Run the CLI:**
   ```bash
   uv run bookscraper --help
   ```
   `uv run` executes a command inside the project's `.venv` without needing to activate it first. Alternatively,
   activate the venv yourself (`source .venv/bin/activate`) and drop the `uv run` prefix from any command below.

### Configuration (`.env` file)

For `push`, `pull`, and `--check-mongo`, you need to set up environment variables. Create a file named `.env` in the root directory of the
project (the same directory as `pyproject.toml` and `README.md`).

```dotenv
MONGODB_URI="mongodb+srv://<username>:<password>@<cluster-url>/<database-name>?retryWrites=true&w=majority"
TLS_CERT_FILE="/path/to/your/tls_certificate.pem" # Optional if not using client certificates
````

* Replace `<username>`, `<password>`, `<cluster-url>`, and `<database-name>` with your MongoDB Atlas credentials.
* `TLS_CERT_FILE`: This is optional. If your MongoDB Atlas setup requires client certificate authentication, provide the
  full path to your `.pem` file. If not needed for your connection, you can omit this line or leave it empty.
* `ANTHROPIC_API_KEY`: Required **only** for the `detect-schema` subcommand (AI-assisted CSS-selector proposals).
  None of the scraping/storage subcommands need it. An optional `ANTHROPIC_MODEL` pins/overrides the model used.

#### Default Configuration

If you don't specify the configuration in the `.env` file, the application will use the following defaults:

* `MONGODB_URI`: If not defined or empty, the application will try to read it from `~/.bookscrapper/driver_string.txt`.
  The URI format will be validated before use.
* `TLS_CERT_FILE`: If not defined or empty, the application will use `~/.bookscrapper/X509-cert-142838411852079927.pem`
  if it exists.

-----

## 💡 Usage

The workflow is **scrape → review → push**:

```
scrape-urls / search ──► staging/books.json ──► [you review & edit] ──► push ──► MongoDB Atlas
                                                                          ▲          │
                                [edit] ◄── staging/books_mongo.json ◄── pull ◄───────┘
```

`bookscraper` subcommands: `scrape-urls` (step 1: scrape details for a known list of URLs), `search` (step 1:
discover new books by searching configured sites, then scrape their details), `push` (step 2: apply a reviewed
staging file to MongoDB), `pull` (export the whole MongoDB collection to JSON), `rotate-cert` (rotate the Atlas X.509
client certificate), and `detect-schema` (an AI-assisted developer-maintenance tool that proposes CSS-selector
updates for a site's detail page). Run `bookscraper --help` or `bookscraper <subcommand> --help` for full option
listings. The staging file format is documented in [`docs/dev/staging-file-format.md`](docs/dev/staging-file-format.md).

**`detect-schema`** — propose CSS-selector updates for a site's detail page (dev maintenance):

```bash
uv run bookscraper detect-schema --site amazon \
  --url "https://www.amazon.com/dp/1098131029" \
  --url "https://www.amazon.com/dp/1492051365"
```

It loads each `--url` with Playwright, asks Claude to propose selectors for the fields already in
`site_constants[site]`, validates them against every sample page's live DOM (flagging any that match
inconsistently), and prints a diff-style report (optionally also to `--output <path>`). It never writes to
`parameters.py` — you apply the changes you agree with by hand. Requires `ANTHROPIC_API_KEY` (see
Configuration above). It never touches book data (no staging file, no MongoDB). Full writeup, including
the manual selector loop it automates, is in [`docs/scraping/schema-detection.md`](docs/scraping/schema-detection.md).

### Input JSON Format (`scrape-urls`)

`scrape-urls` requires an input JSON file (`-f`/`--input-file`): an array of book documents in the staging-file
schema. Only each document's `url` field is used; every other field is optional and ignored, so a minimal entry is
just `{"url": "..."}`, and a staging file or a `pull` export is itself valid input. The file must already exist. A file that isn't valid JSON, isn't an array, or has any entry without a
non-empty string `url` is rejected as a whole (exit code 1), rather than silently skipping entries.

Example content:

```json
[
  {"title": "Some Amazon Book", "authors": ["Jane Doe"], "url": "https://www.amazon.com/Product-Title/dp/B00ABCD123"},
  {"url": "https://www.packtpub.com/product/another-book/9781234567890"},
  {"url": "https://leanpub.com/your-great-book"},
  {"url": "https://www.oreilly.com/library/view/programming-language/9780123456789/"},
  {"url": "https://unsupported-site.com/book/some-title"}
]
```

The curated lists in `content/` (`urls.json`, `urls_amazon.json`) are full documents (`title`, `authors`,
`publication_year`, `category`, `site`, `hash`, ...) and serve as working examples.

### Command-Line Arguments

**`scrape-urls`** — scrape details for a known list of URLs:

* `-f` or `--input-file <path/to/urls.json>`: **(Required)** Path to your input JSON file containing book documents
  (e.g. the curated lists checked into `content/`).
* `--staging-file <path>`: **(Optional)** Staging file new books are appended to (default: `staging/books.json`).
  Books already in it are skipped; documents you've edited are never changed.
* `--check-mongo`: **(Optional)** Also skip books that already exist in MongoDB (read-only lookups; needs the
  `.env` credentials). Without it, step 1 never contacts MongoDB.
* `--log-severity {debug|info|warning|error}`: **(Optional)** Minimum severity written to this run's log file
  under `~/.bookscrapper/logs/` (default: `info`).

**`search`** — discover new books by searching configured sites, then scrape their details:

* `--staging-file <path>`, `--check-mongo`: **(Optional)** As for `scrape-urls`.
* `--max-search-pages <n>`: **(Optional)** Maximum number of search-result pages to fetch per query per site
  (default: `3`).
* `--log-severity {debug|info|warning|error}`: **(Optional)** As above.

**`push`** — validate a reviewed staging file and apply it to MongoDB:

* `--staging-file <path>`: **(Optional)** The file to push (default: `staging/books.json`); a `pull` export works too.
* `--dry-run`: **(Optional)** Validate and print the plan, write the report, change nothing in MongoDB.
* `--allow-updates`: **(Optional)** Allow replacing existing MongoDB documents (those with an `_id`, from `pull`)
  that you edited. Without it, a plan containing updates is refused before anything is written.
* `--approved-only`: **(Optional)** Push only documents marked `"_staging": {"status": "approved"}` (default:
  everything not marked `rejected`).
* `--report-file <path>`: **(Optional)** Per-document outcome report (default: `push_report.json`).
* `--log-severity {debug|info|warning|error}`: **(Optional)** As above.

Exit codes: `0` success (or dry run), `1` refused before writing anything (invalid file, connection failure, updates
without `--allow-updates`), `3` written, but some documents failed (see the report). `push` never deletes documents
and never modifies the staging file.

**`pull`** — export the whole MongoDB collection to a staging-format JSON file:

* `-o` or `--output <path>`: **(Required)** File to write, e.g. `staging/books_mongo.json`.
* `--force`: **(Optional)** Overwrite `--output` if it exists (otherwise refused, to protect review work).
* `--log-severity {debug|info|warning|error}`: **(Optional)** As above.

The old `--store-backend` flag no longer exists: scraping always writes the staging file, and only `push` writes
MongoDB.

### Examples

All examples below use `uv run`, which runs the command inside `.venv` without activating it. If you've activated
`.venv` yourself, drop the `uv run` prefix and call `bookscraper` (or `python -m bookscraper`) directly.

1. **Scrape a known list of URLs into the staging file:**
   ```bash
   uv run bookscraper scrape-urls -f content/urls.json
   ```
2. **Search configured sites for new books, skipping ones already in MongoDB, up to 5 result pages per query:**
   ```bash
   uv run bookscraper search --check-mongo --max-search-pages 5
   ```
3. **Preview, then push the reviewed staging file to MongoDB:**
   ```bash
   uv run bookscraper push --dry-run
   uv run bookscraper push
   ```
4. **Bulk-correct the database: pull, edit, push the changes back:**
   ```bash
   uv run bookscraper pull -o staging/books_mongo.json
   # ... edit staging/books_mongo.json ...
   uv run bookscraper push --staging-file staging/books_mongo.json --dry-run
   uv run bookscraper push --staging-file staging/books_mongo.json --allow-updates
   ```
5. **Without `uv run`, use `python -m bookscraper` inside an activated venv:**
   ```bash
   python -m bookscraper scrape-urls -f content/urls.json
   python -m bookscraper push --dry-run
   ```
6. **Display help messages:**
   ```bash
   uv run bookscraper --help
   uv run bookscraper scrape-urls --help
   uv run bookscraper push --help
   ```

-----

## 📊 Output Files

All output is JSON, written to your current directory (these are run artifacts, not committed to the repo — see
`.gitignore`). Every file is a top-level JSON array of objects.

* **`staging/books.json`** (`scrape-urls`, `search`): the staging file — every newly scraped book (title, authors,
  ISBNs, publication date and year, description, tags, URL, site, and a unique `hash`) plus a `_staging` review
  block. It **persists and accumulates across runs**: books already present (by `hash`) are skipped, and documents
  you've edited are never touched. Format: [`docs/dev/staging-file-format.md`](docs/dev/staging-file-format.md).
* **`staging/books_mongo.json`** (or whatever you pass to `pull -o`): a full export of the MongoDB collection.
* **`push_report.json`** (`push`): one record per staging document — planned action, outcome, `_id`, changed
  fields, and an error class/code if a write failed. Written on every push, including dry runs.
* **`failed_books.json`** (`scrape-urls`): `{"url", "status"}` records for URLs that failed to scrape (`FAILED`,
  `UNKNOWN_ERROR`) or were skipped as already stored (`DUPLICATE`).
* **`other_links.json`** (`scrape-urls`): `{"url"}` records for input URLs that don't belong to a supported site
  (Amazon, Packtpub, Leanpub, O'Reilly) and were skipped.
* **`failed_urls.json`** (`search`): `{"url", "site", "error"}` records for detail scrapes that failed.

The three scrape reports are diagnostics, not book data: they're overwritten on every run, and not written at all
when there's nothing to report. Everything under `staging/` is gitignored — it's your review work and database
content, so don't commit it.

-----

## 📝 Logging

The `BookScraper` utilizes a comprehensive logging system:

* **Console Output:** Real-time progress and critical messages are printed to your terminal, often with color-coded
  statuses (e.g., yellow for info, red for errors). Console output always shows everything passed to it, regardless
  of `--log-severity`.
* **Log File:** Each run writes its own timestamped file under `~/.bookscrapper/logs/`
  (`bookscraper_<YYYYMMDD_HHMMSS>_<pid>.log`), capturing all application logging (from every module, at or above the
  configured severity) including full tracebacks for errors. Only the 10 most recent run logs are kept — older ones
  are deleted automatically at the start of each run.
* **`--log-severity {debug|info|warning|error}`:** **(Optional, both subcommands)** Sets the minimum severity written
  to the log file (default: `info`). Does not affect console output.

-----

## 🏗️ Project Structure

```
BookScraper/
├── .env                  # Environment variables for MongoDB (ignored by Git)
├── pyproject.toml        # Project metadata + dependencies (uv/PEP 621)
├── uv.lock               # Locked, resolved dependency versions (commit this file)
├── README.md             # This README file
├── content/
│   ├── urls.json          # Curated input: general book/reference URLs for scrape-urls (committed)
│   └── urls_amazon.json   # Curated input: Amazon-specific URLs for scrape-urls (committed)
└── src/
    └── bookscraper/
        ├── __init__.py
        ├── __main__.py        # Enables `python -m bookscraper`
        ├── cli.py             # Argument parsing (the `bookscraper`/`scrape-urls`/`search` CLI)
        ├── main.py            # Entry point; dispatches to commands/ (also the console-script target)
        ├── output.py          # Shared pre-flight checks + interactive (C)SV/(M)ongo/(B)oth/(E)xit prompt
        ├── commands/
        │   ├── scrape_urls.py # "I already have URLs" workflow
        │   └── search.py      # "Discover new books" workflow
        ├── book_utils.py      # Helper functions (logging, hashing, date extraction, pre-flight checks)
        ├── database.py        # MongoDB interaction logic
        ├── parameters.py      # Site-specific constants (selectors, 404 titles)
        └── scrape_details.py  # Core scraping logic for each site (Playwright interactions)
```

-----

## 🤝 Contributing

Contributions are welcome\! If you find a bug or have an idea for a new feature (e.g., support for a new book site),
please feel free to open an issue or submit a pull request.

-----

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](https://www.google.com/search?q=LICENSE) file for
details.
