"""The `bookscraper detect-schema` workflow: AI-assisted CSS-selector proposals.

Unlike `scrape-urls`/`search` this command never touches book storage (no
`resolve_store_backend()` call) and, unlike anything else in the package, it never
writes to `parameters.py`. It loads sample detail-page URLs, asks Claude to propose
selectors, validates them against each page's live DOM, and prints a diff-style report
for a human to apply by hand (optionally also writing it to `--output`).

See `scraping/schema_detection.py` for the pipeline and
`docs/scraping/schema-detection.md` for the full manual-and-automated workflow writeup.
"""

import argparse
import sys

from dotenv import load_dotenv

from ..book_utils import print_log
from ..scraping.schema_detection import SchemaDetectionError, detect_schema


async def run(args: argparse.Namespace) -> None:
    # Load .env so ANTHROPIC_API_KEY (and an optional ANTHROPIC_MODEL) can live there,
    # the same lightweight way commands/rotate_cert.py picks up its ATLAS_* vars.
    load_dotenv()

    try:
        report = await detect_schema(args.site, args.url)
    except SchemaDetectionError as exc:
        print_log(f"Schema detection failed: {exc}", "error")
        sys.exit(1)

    # The report body goes to stdout verbatim (it is the tool's primary output); status
    # lines go through print_log like the rest of the CLI.
    sys.stdout.write(report + "\n")
    sys.stdout.flush()

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(report + "\n")
        except OSError as exc:
            print_log(f"Error: could not write report to '{args.output}': {exc}", "error")
            sys.exit(1)
        print_log(f"Report written to {args.output}", "success")

    print_log(
        "Schema detection complete. Review the proposals above and apply desired changes "
        "to site_constants in parameters.py by hand - nothing was written automatically.",
        "success",
    )
