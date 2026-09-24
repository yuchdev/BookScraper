from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Builds the `bookscraper` CLI's argument parser."""
    parser = argparse.ArgumentParser(
        prog="bookscraper",
        description="Scrape and manage book metadata from Amazon, Leanpub, Packtpub, and O'Reilly.",
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="{scrape-urls,search,rotate-cert,detect-schema}"
    )

    scrape_parser = subparsers.add_parser(
        "scrape-urls",
        help="Scrape book details from a CSV of known URLs.",
        description="Scrape book details from URLs listed in a CSV file (via Playwright).",
    )
    scrape_parser.add_argument(
        "-f",
        "--input-file",
        dest="input_file",
        required=True,
        help="Path to the CSV file containing URLs to scrape (must have a 'url' column).",
    )
    scrape_parser.add_argument(
        "--store-backend",
        dest="store_backend",
        choices=["mongo", "json"],
        required=True,
        help="Storage backend for both duplicate-check reads and saving scraped books: 'mongo' (the "
        "configured MongoDB Atlas database) or 'json' (local books.json, kept in sync with the same "
        "schema as the MongoDB documents).",
    )
    scrape_parser.add_argument(
        "--log-severity",
        dest="log_severity",
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="Minimum severity written to this run's log file under ~/.bookscrapper/logs/ (default: info).",
    )

    search_parser = subparsers.add_parser(
        "search",
        help="Search sites for new books matching configured queries, then scrape their details.",
        description="Search configured sites for books matching SEARCH_QUERIES, deduplicate "
        "against MongoDB, and scrape details for new results.",
    )
    search_parser.add_argument(
        "--store-backend",
        dest="store_backend",
        choices=["mongo", "json"],
        required=True,
        help="Storage backend for both duplicate-check reads and saving scraped books: 'mongo' (the "
        "configured MongoDB Atlas database) or 'json' (local books.json, kept in sync with the same "
        "schema as the MongoDB documents).",
    )
    search_parser.add_argument(
        "--max-search-pages",
        dest="max_search_pages",
        type=int,
        default=3,
        help="Maximum number of search-result pages to fetch per query per site (default: 3).",
    )
    search_parser.add_argument(
        "--log-severity",
        dest="log_severity",
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="Minimum severity written to this run's log file under ~/.bookscrapper/logs/ (default: info).",
    )

    rotate_parser = subparsers.add_parser(
        "rotate-cert",
        help="Issue a new MongoDB Atlas X.509 client certificate and point the app at it.",
        description="Rotate the Atlas-managed X.509 client certificate for the configured database user via the "
        "Atlas Admin API (credentials come from ATLAS_CLIENT_ID / ATLAS_CLIENT_SECRET / ATLAS_PROJECT_ID / "
        "ATLAS_DB_USER in .env or the environment), write it under ~/.bookscrapper/, and update "
        "settings.json if it references the cert by filename.",
    )
    rotate_parser.add_argument(
        "--months",
        dest="months",
        type=int,
        default=None,
        help="Certificate validity in months, 1-24 (default: ATLAS_CERT_MONTHS, else 6).",
    )
    rotate_parser.add_argument(
        "--output",
        dest="output",
        default=None,
        help="Where to write the certificate (default: ATLAS_CERT_FILE, else a timestamped "
        "X509-cert-*.pem under ~/.bookscrapper/).",
    )
    rotate_parser.add_argument(
        "--log-severity",
        dest="log_severity",
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="Minimum severity written to this run's log file under ~/.bookscrapper/logs/ (default: info).",
    )

    detect_schema_parser = subparsers.add_parser(
        "detect-schema",
        help="Propose CSS-selector updates for a site's detail page via Playwright + Claude.",
        description="Load one or more sample detail-page URLs, ask Claude to propose CSS selectors "
        "for the fields in site_constants[site], validate them against each page's live DOM, and "
        "print a diff-style report. Never writes to parameters.py; needs ANTHROPIC_API_KEY.",
    )
    detect_schema_parser.add_argument(
        "--site",
        dest="site",
        choices=["amazon", "packtpub", "leanpub", "oreilly"],
        required=True,
        help="Which site's detail-page schema to propose selectors for.",
    )
    detect_schema_parser.add_argument(
        "--url",
        dest="url",
        action="append",
        required=True,
        metavar="URL",
        help="A sample detail-page URL for the chosen site. Repeat --url to validate proposals "
        "across multiple sample pages (recommended).",
    )
    detect_schema_parser.add_argument(
        "--output",
        dest="output",
        default=None,
        help="Optional path to also write the report to (it is always printed to stdout).",
    )
    detect_schema_parser.add_argument(
        "--log-severity",
        dest="log_severity",
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="Minimum severity written to this run's log file under ~/.bookscrapper/logs/ (default: info).",
    )

    return parser
