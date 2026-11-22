"""Retroactive cleanup for CPP_BOOKS_amazon_verified.json's titles.

scripts/verify_amazon_books.py was run before scrape_book() learned to parse a trailing
"(... Edition)"/"(... Series)" suffix off a scraped title (see
bookscraper.book_utils.extract_edition_or_series_from_title), so its output still carries
titles like "Tour of C++, A (C++ In-Depth Series)" with edition/series left at "N/A". This
applies the same rule to that existing file in place, using the same pure helpers the live
scraper now uses - not a reimplementation.

Usage: uv run python scripts/clean_amazon_titles.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bookscraper.book_utils import (
    dumps_documents,
    extract_edition_or_series_from_title,
    fill_or_confirm_field,
    print_log,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(REPO_ROOT / "CPP_BOOKS_amazon_verified.json"))
    parser.add_argument("--output", default=None, help="Defaults to overwriting --input in place.")
    return parser.parse_args()


def clean_record(record: dict) -> tuple[str, list[str]]:
    """Mutates `record` in place. Returns ("unchanged" | "title_cut", conflict messages)."""
    title = record.get("scraped_title")
    if not title:
        return "unchanged", []

    cleaned_title, edition_from_title, series_from_title = extract_edition_or_series_from_title(title)
    if cleaned_title == title and not edition_from_title and not series_from_title:
        return "unchanged", []

    record["scraped_title"] = cleaned_title
    conflicts: list[str] = []
    for field, candidate in (("edition", edition_from_title), ("series", series_from_title)):
        if not candidate:
            continue
        current = record.get(field)
        if fill_or_confirm_field(record, field, candidate) == "conflict":
            conflicts.append(
                f"{title!r}: {field} selector={current!r} vs title-suffix={candidate!r} (kept {current!r})"
            )
    return "title_cut", conflicts


def run(args: argparse.Namespace) -> None:
    with open(args.input, encoding="utf-8-sig") as handle:
        records = json.load(handle)

    cleaned = 0
    all_conflicts: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        outcome, conflicts = clean_record(record)
        if outcome == "title_cut":
            cleaned += 1
        all_conflicts.extend(conflicts)

    for conflict in all_conflicts:
        print_log(f"Edition/series conflict: {conflict}", "warning")

    output_path = args.output or args.input
    Path(output_path).write_text(dumps_documents(records) + "\n", encoding="utf-8")
    print_log(
        f"Cleaned {cleaned} of {len(records)} title(s) "
        f"({len(all_conflicts)} edition/series conflict(s) kept the selector value). Wrote {output_path}.",
        "info",
    )


if __name__ == "__main__":
    run(parse_args())
