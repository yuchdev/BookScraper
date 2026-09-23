import asyncio
import logging

from .book_utils import configure_logging
from .cli import build_parser
from .commands import detect_schema, rotate_cert, scrape_urls, search


async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(getattr(logging, args.log_severity.upper()))

    if args.command == "scrape-urls":
        await scrape_urls.run(args)
    elif args.command == "search":
        await search.run(args)
    elif args.command == "rotate-cert":
        await rotate_cert.run(args)
    elif args.command == "detect-schema":
        await detect_schema.run(args)
    else:
        # Unreachable: the subparsers are required=True.
        parser.error(f"Unknown command: {args.command}")


def main_sync() -> None:
    """Synchronous wrapper so the console-script entry point and `python -m bookscraper` can call this directly."""
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
