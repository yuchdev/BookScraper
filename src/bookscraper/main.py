from __future__ import annotations

import asyncio
import logging
import sys

from . import exitcodes
from .backends import BackendUnavailableError
from .book_utils import configure_logging, print_log
from .cli import build_parser
from .commands import detect_schema, migrate, rotate_cert, scrape_urls, search


async def main() -> int:
    """Parse CLI arguments, configure logging, dispatch to a command's ``run``, and return its exit code.

    Each command's ``run`` returns one of the :mod:`bookscraper.exitcodes` constants rather than
    exiting the process itself. A :class:`~bookscraper.backends.BackendUnavailableError` raised by a
    command's ``resolve_store_backend`` pre-flight is mapped to :data:`bookscraper.exitcodes.FATAL`
    here, so the decision to abort lives in one place.

    :returns: The process exit code to hand to :func:`main_sync`.
    """
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(getattr(logging, args.log_severity.upper()))

    try:
        if args.command == "scrape-urls":
            return await scrape_urls.run(args)
        if args.command == "search":
            return await search.run(args)
        if args.command == "rotate-cert":
            return await rotate_cert.run(args)
        if args.command == "detect-schema":
            return await detect_schema.run(args)
        if args.command == "migrate":
            return await migrate.run(args)
    except BackendUnavailableError as exc:
        print_log(f"Storage backend unavailable: {exc}", "error")
        return exitcodes.FATAL

    # Unreachable: the subparsers are required=True.
    parser.error(f"Unknown command: {args.command}")
    return exitcodes.USAGE


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
