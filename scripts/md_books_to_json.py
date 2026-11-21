"""Convert a Markdown book catalog (e.g. ``CPP_BOOKS.md``) into a JSON array of book documents.

Expected Markdown shape::

    ## Category Name
    Free-form prose (ignored).
    * [Book Title](https://www.amazon.com/dp/0321714113) (Author One, Author Two, 2012)

Each ``## `` (level-2) heading becomes the ``category`` of every ``*`` bullet below it, until
the next level-2 heading. Every bullet becomes one document::

    {"title", "url", "website", "authors", "publication_year", "category"}

Parsing rules for the trailing ``(...)`` group(s) after the link:

- The **last** parenthesized group holds ``authors..., year``; earlier groups such as
  ``(pdf)`` are format notes and are ignored.
- The year is the trailing 4-digit number (``last updated on 2023`` -> ``2023``); if there is
  none, ``publication_year`` is ``null`` and the whole group is treated as authors.
- Groups listed in ``NON_AUTHOR_NOTES`` (e.g. ``MS documentation page``) are not authors.
- Empty author fragments (``Tom Oliver, ,2024``) and stray whitespace are dropped.
- An empty link target ``[Title]()`` yields ``url``/``website`` = ``null``.

``website`` is the URL's host with a leading ``www.`` stripped (``amazon.com``, ``leanpub.com``).

Fails loudly: any ``*`` bullet that is not a ``[title](url)`` link aborts with its line number
instead of being silently skipped.

Usage::

    uv run python scripts/md_books_to_json.py CPP_BOOKS.md                 # -> CPP_BOOKS.json
    uv run python scripts/md_books_to_json.py CPP_BOOKS.md -o content/cpp_books.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

CATEGORY_RE = re.compile(r"^##\s+(?P<name>.+?)\s*$")
BULLET_RE = re.compile(r"^\s*\*\s+(?P<rest>.*)$")
# [title](url) followed by any number of trailing "(...)" groups.
LINK_RE = re.compile(r"^\[(?P<title>[^\]]+)\]\((?P<url>[^)]*)\)(?P<tail>.*)$")
PAREN_GROUP_RE = re.compile(r"\(([^()]*)\)")
TRAILING_YEAR_RE = re.compile(r"(?:^|[\s,])(?P<year>(?:19|20)\d{2})\s*$")
# Leading phrases that qualify a year rather than name an author.
YEAR_QUALIFIER_RE = re.compile(r"(?:last\s+updated\s+on|updated|published)\s*$", re.IGNORECASE)

NON_AUTHOR_NOTES = {"pdf", "ms documentation page"}


class CatalogParseError(ValueError):
    """Raised when a bullet line cannot be parsed into a book document."""


def website_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    host = urlparse(url).hostname
    if not host:
        return None
    return host.removeprefix("www.")


def parse_attribution(tail: str) -> tuple[list[str], Optional[int]]:
    """Extract (authors, year) from the text after the link, e.g. ' (pdf) (Agner Fog, 2004)'."""
    groups = [g.strip() for g in PAREN_GROUP_RE.findall(tail)]
    groups = [g for g in groups if g and g.lower() not in NON_AUTHOR_NOTES]
    if not groups:
        return [], None

    text = groups[-1]
    year = None
    if match := TRAILING_YEAR_RE.search(text):
        year = int(match.group("year"))
        text = text[: match.start("year")]
        text = YEAR_QUALIFIER_RE.sub("", text.strip().rstrip(",").strip())

    authors = [a.strip() for a in text.split(",")]
    return [a for a in authors if a], year


def parse_catalog(markdown: str) -> list[dict]:
    books: list[dict] = []
    category: Optional[str] = None

    for lineno, line in enumerate(markdown.splitlines(), start=1):
        if heading := CATEGORY_RE.match(line):
            category = heading.group("name")
            continue

        bullet = BULLET_RE.match(line)
        if not bullet:
            continue

        link = LINK_RE.match(bullet.group("rest").strip())
        if not link:
            raise CatalogParseError(f"line {lineno}: bullet is not a [title](url) link: {line!r}")
        if category is None:
            raise CatalogParseError(f"line {lineno}: book listed before any '## ' category heading")

        url = link.group("url").strip() or None
        authors, year = parse_attribution(link.group("tail"))
        books.append(
            {
                "title": link.group("title").strip(),
                "url": url,
                "website": website_from_url(url),
                "authors": authors,
                "publication_year": year,
                "category": category,
            }
        )
    return books


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", type=Path, help="Markdown catalog, e.g. CPP_BOOKS.md")
    parser.add_argument("-o", "--output", type=Path, help="Output JSON (default: input with .json suffix)")
    args = parser.parse_args(argv)

    output = args.output or args.input.with_suffix(".json")
    try:
        books = parse_catalog(args.input.read_text(encoding="utf-8"))
    except (OSError, CatalogParseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Same on-disk format as book_utils.dumps_documents(): array, 2-space indent, UTF-8 verbatim.
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(books, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    categories = len({b["category"] for b in books})
    print(f"Wrote {len(books)} books in {categories} categories to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
