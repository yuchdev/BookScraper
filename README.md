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
3. Ensure the .env file is set with:
    ```dotenv
    MONGODB_URI="mongodb+srv://<username>:<password>@<cluster-url>/<database-name>?retryWrites=true&w=majority"
    TLS_CERT_FILE="/path/to/your/tls_certificate.pem" # Optional if not using client certificates
    ```
4. run `uv run bookscraper search --store-backend mongo`

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
* **Interchangeable Storage Backends** (`--store-backend {mongo|json}`):
    * **MongoDB Atlas:** Store scraped data directly into a MongoDB database, with unique indexing to prevent duplicate
      entries.
    * **Local JSON:** A persistent `books.json` holding the exact documents a MongoDB insert would receive, with the
      same hash-based duplicate detection, so the two backends behave identically.
* **JSON Everywhere:** Input URL lists and diagnostic reports (failed URLs, unhandled URLs) are JSON too.
* **Pre-flight Checks:** Verifies local write permission or MongoDB connectivity for the chosen backend *before*
  starting the scraping process, preventing late-stage failures.
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
* **MongoDB Atlas Cluster (Optional, for MongoDB output)**: If you plan to use MongoDB output, you'll need access to a
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

For MongoDB output, you need to set up environment variables. Create a file named `.env` in the root directory of the
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

`bookscraper` has three subcommands: `scrape-urls` (scrape details for a known list of URLs), `search` (discover
new books by searching configured sites, then scrape their details), and `detect-schema` (an AI-assisted
developer-maintenance tool that proposes CSS-selector updates for a site's detail page). Run `bookscraper
--help` or `bookscraper <subcommand> --help` for full option listings.

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
Configuration above). Takes no `--store-backend`: it never touches book storage. Full writeup, including
the manual selector loop it automates, is in [`docs/scraping/schema-detection.md`](docs/scraping/schema-detection.md).

### Input JSON Format (`scrape-urls`)

`scrape-urls` requires an input JSON file (`-f`/`--input-file`): an array of book documents in the same schema the
storage backends write (`books.json` / the MongoDB collection). Only each document's `url` field is used; every other
field is optional and ignored, so a minimal entry is just `{"url": "..."}`, and an existing `books.json` is itself
valid input. The file must already exist. A file that isn't valid JSON, isn't an array, or has any entry without a
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
* `--store-backend {mongo|json}`: **(Required)** Storage backend used for both duplicate checks and saving scraped
  books: `mongo` (your configured MongoDB Atlas database) or `json` (local `books.json`).
* `--log-severity {debug|info|warning|error}`: **(Optional)** Minimum severity written to this run's log file
  under `~/.bookscrapper/logs/` (default: `info`).

**`search`** — discover new books by searching configured sites, then scrape their details:

* `--store-backend {mongo|json}`: **(Required)** As for `scrape-urls`.
* `--max-search-pages <n>`: **(Optional)** Maximum number of search-result pages to fetch per query per site
  (default: `3`).
* `--log-severity {debug|info|warning|error}`: **(Optional)** Minimum severity written to this run's log file
  under `~/.bookscrapper/logs/` (default: `info`).

There is no implicit default and no interactive prompt: omitting `--store-backend` is an argparse error, so which
store a run touched is always explicit from its command line.

### Examples

All examples below use `uv run`, which runs the command inside `.venv` without activating it. If you've activated
`.venv` yourself, drop the `uv run` prefix and call `bookscraper` (or `python -m bookscraper`) directly.

1. **Scrape a known list of URLs, save to the local JSON store:**
   ```bash
   uv run bookscraper scrape-urls -f content/urls.json --store-backend json
   ```
2. **Scrape a known list of URLs, save to MongoDB:**
   ```bash
   uv run bookscraper scrape-urls -f content/urls.json --store-backend mongo
   ```
3. **Search configured sites for new books, save to the local JSON store:**
   ```bash
   uv run bookscraper search --store-backend json
   ```
4. **Search configured sites for new books, save to MongoDB, up to 5 result pages per query:**
   ```bash
   uv run bookscraper search --store-backend mongo --max-search-pages 5
   ```
5. **Without `uv run`, use `python -m bookscraper` inside an activated venv:**
   ```bash
   python -m bookscraper scrape-urls -f content/urls.json --store-backend json
   python -m bookscraper search --store-backend json
   ```
6. **Display help messages:**
   ```bash
   uv run bookscraper --help
   uv run bookscraper scrape-urls --help
   uv run bookscraper search --help
   ```

-----

## 📊 Output Files

All output is JSON, written to your current directory (these are run artifacts, not committed to the repo — see
`.gitignore`). Every file is a top-level JSON array of objects.

* **`books.json`** (`--store-backend json` only): every successfully scraped book, as the exact document a MongoDB
  insert would receive — title, authors, ISBNs, publication date and year, description, tags, URL, site, and a unique
  `hash`. Unlike the reports below, it **persists and accumulates across runs**: books already present (by `hash`) are
  skipped, mirroring MongoDB's unique index. With `--store-backend mongo` the same documents go to the database
  instead.
* **`failed_books.json`** (`scrape-urls`): `{"url", "status"}` records for URLs that failed to scrape (`FAILED`,
  `UNKNOWN_ERROR`) or were skipped as already stored (`DUPLICATE`).
* **`other_links.json`** (`scrape-urls`): `{"url"}` records for input URLs that don't belong to a supported site
  (Amazon, Packtpub, Leanpub, O'Reilly) and were skipped.
* **`failed_urls.json`** (`search`): `{"url", "site", "error"}` records for detail scrapes that failed.

The three reports are diagnostics, not book data: they're written regardless of `--store-backend`, overwritten on
every run, and not written at all when there's nothing to report.

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
