from __future__ import annotations

import asyncio
import logging
import sys

from . import exitcodes
from .backends import BackendUnavailableError
from .book_utils import configure_logging, print_log
from .cli import build_parser
from .commands import detect_schema, doctor, migrate, rotate_cert, scrape_urls, search
from .commands._reporting import RunReport

#: Subcommands that produce a machine-readable run report (task 07.0 subtask 03). Only these carry
#: the ``--report`` flag; the others (rotate-cert/detect-schema/doctor) never touch book storage.
_REPORTING_COMMANDS = frozenset({"scrape-urls", "search", "migrate"})


async def main() -> int:
    """Parse CLI arguments, configure logging, dispatch to a command's ``run``, and return its exit code.

    Each command's ``run`` returns one of the :mod:`bookscraper.exitcodes` constants rather than
    exiting the process itself. A :class:`~bookscraper.backends.BackendUnavailableError` raised by a
    command's ``resolve_store_backend`` pre-flight is mapped to :data:`bookscraper.exitcodes.FATAL`
    here, so the decision to abort lives in one place.

    For the scrape/search/migrate commands a :class:`~bookscraper.commands._reporting.RunReport` is
    created up front, recorded into by the command, and finalized with the exit code in a ``finally``
    - so even an interrupted run (``Ctrl+C``) leaves a report carrying ``exit_code`` 130. Writing the
    report is best-effort and can never change the exit code or mask the run's own exception.

    :returns: The process exit code to hand to :func:`main_sync`.
    """
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(getattr(logging, args.log_severity.upper()))

    report = RunReport(args.command, sys.argv[1:]) if args.command in _REPORTING_COMMANDS else None
    exit_code = exitcodes.FATAL
    try:
        if args.command == "scrape-urls":
            exit_code = await scrape_urls.run(args, report)
        elif args.command == "search":
            exit_code = await search.run(args, report)
        elif args.command == "rotate-cert":
            exit_code = await rotate_cert.run(args)
        elif args.command == "detect-schema":
            exit_code = await detect_schema.run(args)
        elif args.command == "migrate":
            exit_code = await migrate.run(args, report)
        elif args.command == "doctor":
            exit_code = await doctor.run(args)
        else:
            # Unreachable: the subparsers are required=True.
            parser.error(f"Unknown command: {args.command}")
            exit_code = exitcodes.USAGE
        return exit_code
    except BackendUnavailableError as exc:
        print_log(f"Storage backend unavailable: {exc}", "error")
        exit_code = exitcodes.FATAL
        return exit_code
    except KeyboardInterrupt:
        # Interrupted mid-run: record 130 in the report (written by the finally) and re-raise so
        # main_sync prints the one-line notice and performs the real sys.exit.
        exit_code = exitcodes.INTERRUPTED
        raise
    finally:
        if report is not None:
            try:
                report.finalize(exit_code)
                report.write(getattr(args, "report", None))
            except Exception as exc:  # noqa: X002 - report writing is best-effort; it must never mask the run's outcome
                print_log(f"Run report could not be written: {exc}", "warning")


def main_sync():
    """Synchronous entry point: run :func:`main` and translate its result into a process exit code.

    This is what both the ``bookscraper`` console script and ``python -m bookscraper`` call. A
    ``KeyboardInterrupt`` (Ctrl+C) surfaces as a single warning line and exit
    :data:`bookscraper.exitcodes.INTERRUPTED` with no traceback; otherwise the code returned by
    :func:`main` is used verbatim. Each command closes its storage backend in a ``finally`` block,
    so the backend is released even on interrupt.
    """
    try:
        code = asyncio.run(main())
    except KeyboardInterrupt:
        print_log("Interrupted.", "warning")
        sys.exit(exitcodes.INTERRUPTED)
    sys.exit(code)


if __name__ == "__main__":
    main_sync()
