# BookScraper

![Python](https://img.shields.io/badge/Python-3.13%2B-blue?style=for-the-badge&logo=python)
![Playwright](https://img.shields.io/badge/Playwright-Chromium-green?style=for-the-badge&logo=playwright)
![MongoDB](https://img.shields.io/badge/MongoDB-4.0%2B-47A248?style=for-the-badge&logo=mongodb)
![License](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)

## 📚 Overview

`BookScraper` is a powerful and robust web scraping tool designed to extract detailed information about books from
various online retailers and publishing platforms. Built with `asyncio` and `Playwright`, it efficiently navigates web
pages, collects data, and provides flexible output options to CSV files or a MongoDB database.

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
4. run `uv run bookscraper search --output-to-mongo`

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
* **Flexible Output:**
    * **CSV Files:** Export scraped book data, failed URLs, and unhandled URLs to `.csv` files.
    * **MongoDB Atlas:** Store scraped data directly into a MongoDB database, with unique indexing to prevent duplicate
      entries.
* **Pre-flight Checks:** Verifies write permissions for CSV output and establishes MongoDB connection *before* starting
  the scraping process, preventing late-stage failures.
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

### Input CSV Format (`scrape-urls`)

`scrape-urls` requires an input CSV file (`-f`/`--input-file`) with a single column named `url`. The file must
already exist — it is not created automatically.

Example content:

```csv
url
[https://www.amazon.com/Product-Title/dp/B00ABCD123](https://www.amazon.com/Product-Title/dp/B00ABCD123)
[https://www.packtpub.com/product/another-book/9781234567890](https://www.packtpub.com/product/another-book/9781234567890)
[https://leanpub.com/your-great-book](https://leanpub.com/your-great-book)
[https://www.oreilly.com/library/view/programming-language/9780123456789/](https://www.oreilly.com/library/view/programming-language/9780123456789/)
[https://unsupported-site.com/book/some-title](https://unsupported-site.com/book/some-title)
```

### Command-Line Arguments

**`scrape-urls`** — scrape details for a known list of URLs:

* `-f` or `--input-file <path/to/urls.csv>`: **(Required)** Path to your input CSV file containing book URLs (e.g.
  the curated lists checked into `content/`).
* `-c` or `--output-to-csv`: **(Optional)** If present, scraped data will be saved to `books.csv`, `failed_books.csv`,
  and `other_links.csv` in the current directory.
* `-m` or `--output-to-mongo`: **(Optional)** If present, scraped data will be saved to your configured MongoDB Atlas
  database.
* `--log-severity {debug|info|warning|error}`: **(Optional)** Minimum severity written to this run's log file
  under `~/.bookscrapper/logs/` (default: `info`).

**`search`** — discover new books by searching configured sites, then scrape their details:

* `-c` or `--output-to-csv`: **(Optional)** If present, scraped data will be saved to `scraped_books.csv` and
  `failed_urls.csv` in the current directory.
* `-m` or `--output-to-mongo`: **(Optional)** If present, scraped data will be saved to your configured MongoDB Atlas
  database.
* `--max-search-pages <n>`: **(Optional)** Maximum number of search-result pages to fetch per query per site
  (default: `3`).
* `--log-severity {debug|info|warning|error}`: **(Optional)** Minimum severity written to this run's log file
  under `~/.bookscrapper/logs/` (default: `info`).

### Examples

All examples below use `uv run`, which runs the command inside `.venv` without activating it. If you've activated
`.venv` yourself, drop the `uv run` prefix and call `bookscraper` (or `python -m bookscraper`) directly.

1. **Scrape a known list of URLs, save to CSV only:**
   ```bash
   uv run bookscraper scrape-urls -f content/urls.csv -c
   ```
2. **Scrape a known list of URLs, save to MongoDB only:**
   ```bash
   uv run bookscraper scrape-urls -f content/urls.csv -m
   ```
3. **Scrape a known list of URLs, save to both CSV and MongoDB:**
   ```bash
   uv run bookscraper scrape-urls -f content/urls.csv -c -m
   ```
4. **Search configured sites for new books, save to CSV only:**
   ```bash
   uv run bookscraper search -c
   ```
5. **Search configured sites for new books, save to MongoDB only, up to 5 result pages per query:**
   ```bash
   uv run bookscraper search -m --max-search-pages 5
   ```
6. **Without `uv run`, use `python -m bookscraper` inside an activated venv:**
   ```bash
   python -m bookscraper scrape-urls -f content/urls.csv -c -m
   python -m bookscraper search -c -m
   ```
7. **Display help messages:**
   ```bash
   uv run bookscraper --help
   uv run bookscraper scrape-urls --help
   uv run bookscraper search --help
   ```

### Interactive Output Selection

If you run either subcommand without specifying `-c` or `-m` (e.g., `bookscraper scrape-urls -f content/urls.csv`), it will
perform pre-flight checks for CSV write permissions and MongoDB connectivity. Based on the successful checks, it will
then prompt you to choose your desired output destination interactively:

```
Running pre-flight checks for output destinations...
  Checking CSV write permissions...
  CSV write permission: OK
  Checking MongoDB connection...
  MongoDB connection: OK

No valid output destination specified via command line arguments (-c, -m).
Do you want to save to (C)SV, (M)ongoDB, or (B)oth, or (E)xit?
```

If a destination (e.g., MongoDB) fails its pre-flight check, it will not be offered as an option in the interactive
prompt.

-----

## 📊 Output Files

Upon successful execution (and if CSV output is enabled), the following files will be generated in your project root
(these are run artifacts, not committed to the repo — see `.gitignore`):

* **`books.csv`**: Contains all successfully scraped book details. Each row represents a book with columns for title,
  authors, ISBNs, publication date, description, tags, URL, site, year, and a unique hash.
* **`failed_books.csv`**: Lists the URLs that the scraper attempted to process but failed to retrieve details for after
  multiple retries.
* **`other_links.csv`**: Contains URLs from your input file that do not belong to the currently supported scraping
  sites (Amazon, Packtpub, Leanpub, O'Reilly). These URLs are skipped during the scraping process.

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
│   ├── urls.csv           # Curated input: general book/reference URLs for scrape-urls (committed)
│   └── urls_amazon.csv    # Curated input: Amazon-specific URLs for scrape-urls (committed)
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
