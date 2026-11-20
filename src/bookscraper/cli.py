from __future__ import annotations

import argparse

# Mirrors backends.local.staging.DEFAULT_STAGING_FILE (kept as a literal so building the parser
# imports no backend code; tests/unit/test_cli.py asserts the two stay equal).
DEFAULT_STAGING_FILE = "staging/books.json"
DEFAULT_PUSH_REPORT = "push_report.json"


def _add_staging_file_flag(parser: argparse.ArgumentParser, help_text: str) -> None:
    parser.add_argument(
        "--staging-file",
        dest="staging_file",
        default=DEFAULT_STAGING_FILE,
        metavar="PATH",
        help=help_text + f" (default: {DEFAULT_STAGING_FILE}).",
    )


def _add_check_mongo_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--check-mongo",
        dest="check_mongo",
        action="store_true",
        help="Also skip books that already exist in MongoDB (read-only lookups; needs MongoDB credentials). "
        "Without it, step 1 never contacts MongoDB.",
    )


def _add_log_severity_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--log-severity",
        dest="log_severity",
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="Minimum severity written to this run's log file under ~/.bookscrapper/logs/ (default: info).",
    )


def build_parser() -> argparse.ArgumentParser:
    """Builds the `bookscraper` CLI's argument parser."""
    parser = argparse.ArgumentParser(
        prog="bookscraper",
        description="Scrape and manage book metadata from Amazon, Leanpub, Packtpub, and O'Reilly.",
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="{scrape-urls,search,push,pull,rotate-cert,detect-schema}"
    )

    scrape_parser = subparsers.add_parser(
        "scrape-urls",
        help="Step 1: scrape book details from a JSON file of known URLs into the staging file.",
        description="Scrape book details from URLs listed in a JSON file (via Playwright) and append them to "
        "the staging file for review. Nothing is written to MongoDB; run 'bookscraper push' afterwards.",
    )
    scrape_parser.add_argument(
        "-f",
        "--input-file",
        dest="input_file",
        required=True,
        help="Path to a JSON file holding an array of book documents (the books.json / MongoDB "
        "schema); each document's 'url' field is scraped, other fields are ignored.",
    )
    _add_staging_file_flag(
        scrape_parser,
        "Staging file that newly scraped books are appended to for review; push it to MongoDB "
        "afterwards with 'bookscraper push'",
    )
    _add_check_mongo_flag(scrape_parser)
    _add_log_severity_flag(scrape_parser)

    search_parser = subparsers.add_parser(
        "search",
        help="Step 1: search sites for new books matching configured queries, scrape them into the staging file.",
        description="Search configured sites for books matching SEARCH_QUERIES, skip books already in the "
        "staging file (and in MongoDB with --check-mongo), and append details of new results to the staging "
        "file for review. Nothing is written to MongoDB; run 'bookscraper push' afterwards.",
    )
    _add_staging_file_flag(
        search_parser,
        "Staging file that newly found books are appended to for review; push it to MongoDB "
        "afterwards with 'bookscraper push'",
    )
    _add_check_mongo_flag(search_parser)
    search_parser.add_argument(
        "--max-search-pages",
        dest="max_search_pages",
        type=int,
        default=3,
        help="Maximum number of search-result pages to fetch per query per site (default: 3).",
    )
    _add_log_severity_flag(search_parser)

    push_parser = subparsers.add_parser(
        "push",
        help="Step 2: validate a reviewed staging file and apply it to MongoDB.",
        description="Validate the whole staging file, plan every document (insert / duplicate / update / "
        "unchanged / skipped), then apply inserts and updates to MongoDB in one bulk write. Nothing is written "
        "if any document is invalid, or if the plan contains updates and --allow-updates is absent. Never "
        "deletes, never modifies the staging file. Exit codes: 0 ok, 1 refused before writing, 3 partial write.",
    )
    _add_staging_file_flag(push_parser, "Reviewed staging file (or a 'pull' export) to push")
    push_parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Validate and print the plan, write the report, but make no changes to MongoDB.",
    )
    push_parser.add_argument(
        "--allow-updates",
        dest="allow_updates",
        action="store_true",
        help="Allow replacing existing MongoDB documents (those carrying an _id from 'pull') that were edited.",
    )
    push_parser.add_argument(
        "--approved-only",
        dest="approved_only",
        action="store_true",
        help="Push only documents whose _staging.status is 'approved' (default: everything not 'rejected').",
    )
    push_parser.add_argument(
        "--report-file",
        dest="report_file",
        default=DEFAULT_PUSH_REPORT,
        metavar="PATH",
        help=f"Where to write the per-document outcome report (default: {DEFAULT_PUSH_REPORT}).",
    )
    _add_log_severity_flag(push_parser)

    pull_parser = subparsers.add_parser(
        "pull",
        help="Export the entire MongoDB books collection to a staging-format JSON file.",
        description="Read every document in the MongoDB books collection and write it, with its _id as a hex "
        "string, to a JSON file in the staging format - edit it and send changes back with "
        "'bookscraper push --staging-file FILE --allow-updates'. Read-only against MongoDB.",
    )
    pull_parser.add_argument(
        "-o",
        "--output",
        dest="output",
        required=True,
        metavar="FILE",
        help="File to write, e.g. staging/books_mongo.json. An existing file is never overwritten without --force.",
    )
    pull_parser.add_argument(
        "--force",
        dest="force",
        action="store_true",
        help="Overwrite --output if it already exists (atomically, only after the export succeeded).",
    )
    _add_log_severity_flag(pull_parser)

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
