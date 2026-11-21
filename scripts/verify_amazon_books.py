"""One-off data-quality pass over CPP_BOOKS.json's amazon.com entries.

Re-scrapes each Amazon book page via the project's own ``scrape_book`` and emits an
original-vs-scraped JSON file a human can eyeball. This is NOT a ``bookscraper``
subcommand and never touches MongoDB or the staging/ pipeline - it is a throwaway
verification utility, run as ``uv run python scripts/verify_amazon_books.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path

from playwright.async_api import async_playwright

from bookscraper.book_utils import dumps_documents, print_log
from bookscraper.scraping.parameters import HEADLESS_BROWSER
from bookscraper.scraping.scrape_details import scrape_book

REPO_ROOT = Path(__file__).resolve().parent.parent
BATCH_SIZE = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(REPO_ROOT / "CPP_BOOKS.json"))
    parser.add_argument("--output", default=str(REPO_ROOT / "CPP_BOOKS_amazon_verified.json"))
    parser.add_argument("--limit", type=int, default=None, help="Cap how many Amazon entries to scrape (test runs).")
    return parser.parse_args()


def _merged_record(original: dict, scraped: dict) -> dict:
    return {
        "url": scraped.get("url") or original.get("url"),
        "original_title": original.get("title"),
        "scraped_title": scraped.get("title"),
        "original_authors": original.get("authors"),
        "scraped_authors": scraped.get("authors"),
        "original_publication_year": original.get("publication_year"),
        "scraped_publication_year": scraped.get("publication_year"),
        "category": original.get("category"),
        "edition": scraped.get("edition"),
        "isbn10": scraped.get("isbn10"),
        "isbn13": scraped.get("isbn13"),
        "publisher": scraped.get("publisher"),
        "series": scraped.get("series"),
        "cover_path": scraped.get("cover_path"),
    }


def _failed_record(original: dict) -> dict:
    return {
        "url": original.get("url"),
        "category": original.get("category"),
        "original_title": original.get("title"),
        "status": "FAILED",
    }


async def run(args: argparse.Namespace) -> None:
    with open(args.input, encoding="utf-8-sig") as handle:
        documents = json.load(handle)

    amazon_entries = [doc for doc in documents if isinstance(doc, dict) and doc.get("website") == "amazon.com"]
    if args.limit is not None:
        amazon_entries = amazon_entries[: args.limit]

    total = len(amazon_entries)
    print_log(f"Scraping {total} amazon.com entries from {args.input}.", "info")

    records: list[dict] = []
    failed = 0

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=HEADLESS_BROWSER)

        for start in range(0, total, BATCH_SIZE):
            batch = amazon_entries[start : start + BATCH_SIZE]
            tasks = [scrape_book(entry["url"], browser, "amazon", dedup_source=None) for entry in batch]
            results = await asyncio.gather(*tasks)

            for entry, result in zip(batch, results, strict=True):
                if isinstance(result, dict):
                    records.append(_merged_record(entry, result))
                else:
                    failed += 1
                    records.append(_failed_record(entry))

            if start + BATCH_SIZE < total:
                await asyncio.sleep(random.uniform(3, 5))

        await browser.close()

    Path(args.output).write_text(dumps_documents(records) + "\n", encoding="utf-8")
    print_log(f"Scraped {total - failed} of {total} amazon.com entries ({failed} failed). Wrote {args.output}.", "info")


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
