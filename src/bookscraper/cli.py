import argparse


def build_parser() -> argparse.ArgumentParser:
    """Builds the `bookscraper` CLI's argument parser."""
    parser = argparse.ArgumentParser(
        prog="bookscraper",
        description="Scrape and manage book metadata from Amazon, Leanpub, Packtpub, and O'Reilly.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="{scrape-urls,search}")

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

    return parser
