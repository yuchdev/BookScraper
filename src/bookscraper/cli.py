from __future__ import annotations

import argparse


def _positive_int(value: str) -> int:
    """
    ``argparse`` type for a strictly positive integer (>= 1).

    Used by ``rotate-cert --keep`` so ``0`` and negatives are rejected at parse time - a
    ``--keep 0`` would otherwise ask pruning to delete every certificate, including the active one.

    :param value: The raw command-line token.
    :returns: The parsed integer.
    :raises argparse.ArgumentTypeError: If ``value`` is not an integer or is below 1.
    """
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be an integer, got {value!r}") from None
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer (>= 1), got {parsed}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Builds the `bookscraper` CLI's argument parser."""
    parser = argparse.ArgumentParser(
        prog="bookscraper",
        description="Scrape and manage book metadata from Amazon, Leanpub, Packtpub, and O'Reilly.",
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="{scrape-urls,search,rotate-cert,detect-schema,migrate}"
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
        "--check",
        dest="check",
        action="store_true",
        help="Report the currently active X.509 certificate's expiry instead of rotating "
        "(no Atlas contact, no ATLAS_* needed). Exit 0 if more than --warn-days remain (or no "
        "cert is configured), 3 if within the window, 1 if expired or unreadable.",
    )
    rotate_parser.add_argument(
        "--warn-days",
        dest="warn_days",
        type=int,
        default=30,
        help="With --check, the number of days before expiry that triggers exit code 3 (default: 30).",
    )
    rotate_parser.add_argument(
        "--no-verify",
        dest="no_verify",
        action="store_true",
        help="Skip pinging Atlas with the new certificate before repointing settings.json (for "
        "offline or first-time setups). By default the new cert must authenticate first; if it "
        "cannot, settings.json is left untouched and the command exits non-zero.",
    )
    rotate_parser.add_argument(
        "--verify-attempts",
        dest="verify_attempts",
        type=int,
        default=5,
        help="How many times to retry the post-rotation Atlas verification ping before giving up "
        "(default: 5); Atlas can take a short while to propagate a newly issued certificate.",
    )
    rotate_parser.add_argument(
        "--keep",
        dest="keep",
        type=_positive_int,
        default=2,
        help="After a successful rotation, keep only the newest N X509-cert-*.pem files under "
        "~/.bookscrapper/ (plus the active cert, always) and delete the rest (default: 2). Must be "
        ">= 1 so pruning can never remove every certificate.",
    )
    rotate_parser.add_argument(
        "--no-prune",
        dest="no_prune",
        action="store_true",
        help="Do not delete superseded X509-cert-*.pem files after a successful rotation; keep "
        "every certificate on disk.",
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
        help="Propose CSS-selector updates for a site's detail or search pages via Playwright + Claude.",
        description="Load one or more sample detail or search-results pages (see --page-type), ask Claude "
        "to propose CSS selectors for the fields in site_constants[site], validate them against each page's live DOM, and "
        "print a diff-style report (or JSON with --format json). Never writes to parameters.py; "
        "needs ANTHROPIC_API_KEY (not with --replay/--check-current-only). Exit codes: 0 all "
        "current selectors matched; 4 at least one current selector matched on no sample (drift); "
        "1 any error.",
    )
    detect_schema_parser.add_argument(
        "--site",
        dest="site",
        choices=["amazon", "packtpub", "leanpub", "oreilly"],
        required=True,
        help="Which site's detail-page schema to propose selectors for.",
    )
    # Exactly one sample source is required: live URLs (--url) or local HTML files
    # (--html-file, the fully offline path). argparse enforces the mutual exclusion and the
    # "one of them" requirement.
    sample_source = detect_schema_parser.add_mutually_exclusive_group(required=True)
    sample_source.add_argument(
        "--url",
        dest="url",
        action="append",
        metavar="URL",
        help="A sample detail-page URL for the chosen site. Repeat --url to validate proposals "
        "across multiple sample pages (recommended). Mutually exclusive with --html-file.",
    )
    sample_source.add_argument(
        "--html-file",
        dest="html_file",
        action="append",
        metavar="PATH",
        help="A local HTML file to use as a sample page instead of fetching a URL. Loaded fully "
        "offline (no network, no API key needed when paired with --replay). Repeatable; "
        "mutually exclusive with --url.",
    )
    detect_schema_parser.add_argument(
        "--page-type",
        dest="page_type",
        choices=["detail", "search"],
        default="detail",
        help="Which page kind to propose selectors for: the book 'detail' page (default) or a "
        "'search'-results listing page (the card-container and card-relative SEARCH_* selectors).",
    )
    detect_schema_parser.add_argument(
        "--model",
        dest="model",
        default=None,
        help="Anthropic model ID to use (default: ANTHROPIC_MODEL, else claude-sonnet-5). An empty value is rejected.",
    )
    detect_schema_parser.add_argument(
        "--max-input-tokens",
        dest="max_input_tokens",
        type=int,
        default=40000,
        help="Pre-flight input-token budget per request; an over-budget prompt is re-pruned toward "
        "a 10000-character HTML floor and abandoned if it still will not fit (default: 40000).",
    )
    detect_schema_parser.add_argument(
        "--replay",
        dest="replay",
        default=None,
        help="Path to a JSON file of recorded AI responses keyed by sha256(prompt). When given, "
        "no Anthropic request is made and ANTHROPIC_API_KEY is not required (fully offline); a "
        "prompt missing from the file is a hard error.",
    )
    detect_schema_parser.add_argument(
        "--record",
        dest="record",
        default=None,
        help="Path to write this live run's AI responses (keyed by sha256(prompt)) for later "
        "--replay. Ignored in replay mode; never contains the API key.",
    )
    detect_schema_parser.add_argument(
        "--format",
        dest="format",
        choices=["text", "json"],
        default="text",
        help="Report format: 'text' (the diff-style report, default) or 'json' (a machine-readable "
        "CI artifact; stdout stays a single JSON document, status lines go to stderr).",
    )
    detect_schema_parser.add_argument(
        "--check-current-only",
        dest="check_current_only",
        action="store_true",
        help="Skip the AI step entirely and only re-query the current site_constants selectors "
        "against the samples (cheap, keyless; makes no AI call and - with --html-file - no network "
        "call). Each field is classified ok/broken/fallback_unused/absent_optional; exit 4 only if "
        "a needed (non-optional, non-fallback) selector matched on no sample. Basis for a scheduled "
        "drift job.",
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

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Rewrite stored v1 book documents to the canonical schema-2 shape (dry-run by default).",
        description="Migrate every document in the chosen storage backend from the pre-03.1 v1 shape "
        "to the canonical schema-2 BookRecord shape, recomputing each document's hash. Dry-run by "
        "default (prints a per-field change summary and writes nothing); pass --apply to write. "
        "Idempotent and never deletes a document; a recomputed hash that collides with another "
        "document is left untouched and reported for a human. A migration-report-*.json is written "
        "to ~/.bookscrapper/logs/.",
    )
    migrate_parser.add_argument(
        "--store-backend",
        dest="store_backend",
        choices=["mongo", "json"],
        required=True,
        help="Storage backend to migrate: 'mongo' (the configured MongoDB Atlas collection) or "
        "'json' (the local books.json store).",
    )
    migrate_parser.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="Write the migrated documents to the store. Without it the command is a dry run that "
        "reports what would change and writes nothing to the store.",
    )
    migrate_parser.add_argument(
        "--log-severity",
        dest="log_severity",
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="Minimum severity written to this run's log file under ~/.bookscrapper/logs/ (default: info).",
    )

    return parser
