"""The `bookscraper detect-schema` workflow: AI-assisted CSS-selector proposals.

Unlike `scrape-urls`/`search` this command never touches book storage (no
`resolve_store_backend()` call) and, unlike anything else in the package, it never
writes to `parameters.py`. It loads sample pages (live `--url`s or fully offline local
`--html-file`s), asks Claude - or a recorded `--replay` file, offline with no API key - to
propose selectors, validates them against each page's live DOM, and prints a report - a diff-style
text report (default) or, with `--format json`, a machine-readable JSON document for a CI drift job
- for a human to apply by hand (optionally also writing it to `--output`). `--record` captures a
live run's responses for a maintainer to curate into a replay file.

`--check-current-only` skips the AI step entirely (no key, no client, no network when paired with
`--html-file`) and only re-queries the current `site_constants` selectors against the samples. Exit
codes: `0` everything consistent and no current selector broken; `4` at least one current selector
matched on no sample (drift); `1` any error. Exit 4 propagates like rotate-cert's exit 3.

See `scraping/schema_detection.py` for the pipeline and
`docs/scraping/schema-detection.md` for the full manual-and-automated workflow writeup.
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from .. import exitcodes
from ..book_utils import print_log
from ..runtime import runtime_options
from ..scraping.schema_detection import SchemaDetectionError, detect_schema


async def run(args: argparse.Namespace) -> int:
    """
    The "propose selector-schema updates" workflow (dev maintenance, never touches book data).

    Runs :func:`detect_schema` over the sample pages, writes the report to stdout (and
    ``--output`` if given), and returns an exit code.

    :param args: Parsed ``detect-schema`` arguments.
    :returns: :data:`~bookscraper.exitcodes.FATAL` on a :class:`SchemaDetectionError` or an
        ``--output`` write failure, :data:`~bookscraper.exitcodes.DRIFT` when a current selector
        matched no sample page, otherwise :data:`~bookscraper.exitcodes.OK`.
    """
    # Load .env so ANTHROPIC_API_KEY (and an optional ANTHROPIC_MODEL) can live there,
    # the same lightweight way commands/rotate_cert.py picks up its ATLAS_* vars.
    load_dotenv()

    # Resolve the two runtime knobs that apply to detect-schema's Playwright page loads (headless
    # and the navigation timeout) via the same flag -> env -> parameters.py precedence the scraping
    # commands use; concurrency / ignore-robots do not apply here.
    runtime = runtime_options(args)

    try:
        report, usage_line, exit_code = await detect_schema(
            args.site,
            urls=args.url,
            html_files=args.html_file,
            model=args.model,
            max_input_tokens=args.max_input_tokens,
            page_type=args.page_type,
            replay=args.replay,
            record=args.record,
            report_format=args.format,
            check_current_only=args.check_current_only,
            headless=runtime.headless,
            nav_timeout_ms=runtime.nav_timeout_ms,
        )
    except SchemaDetectionError as exc:
        print_log(f"Schema detection failed: {exc}", "error")
        return exitcodes.FATAL

    json_format = args.format == "json"

    # The report body is the tool's primary output and goes to stdout verbatim. For --format json
    # stdout must stay a single parseable JSON document for a CI drift job, so every human status
    # line (usage, completion, drift notice) is routed to stderr instead of print_log's stdout.
    sys.stdout.write(report + "\n")
    sys.stdout.flush()

    def _status(message: str, level: str):
        """Emit a human status line to stderr in JSON mode, else via ``print_log`` to stdout."""
        if json_format:
            print(message, file=sys.stderr)
        else:
            print_log(message, level)

    # One-line usage summary (model, token counts, request count - no prices, no key).
    _status(usage_line, "info")

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(report + "\n")
        except OSError as exc:
            print_log(f"Error: could not write report to '{args.output}': {exc}", "error")
            return exitcodes.FATAL
        _status(f"Report written to {args.output}", "success")

    if exit_code == exitcodes.DRIFT:
        # Drift: at least one current selector matched on no sample page. Return 4 so a scheduled
        # CI drift job notices, the same way rotate-cert --check reports its exit 3.
        _status(
            "Schema drift detected: at least one current selector matched on no sample page. "
            "Review the report and refresh site_constants in parameters.py by hand.",
            "warning",
        )
        return exitcodes.DRIFT

    _status(
        "Schema detection complete. Review the proposals above and apply desired changes "
        "to site_constants in parameters.py by hand - nothing was written automatically.",
        "success",
    )
    return exitcodes.OK
