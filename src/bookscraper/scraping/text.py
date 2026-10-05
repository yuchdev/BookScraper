"""Pure HTML-to-text conversion for scraped prose (subtask 03.0/05).

Extracted from :func:`scrape_details.get_leanpub_book_details`'s ad hoc regex pipeline so it
can be unit-tested in isolation and reused by subtask 03.1 for Playwright description fields.
The module has no I/O and no framework coupling: a single pure function that turns a possibly
``None`` HTML fragment into plain text.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Optional

# Block-level tags that map to a line / paragraph break, the catch-all tag stripper, and the
# whitespace collapsers. Compiled once at import so a per-book call never recompiles them.
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_CLOSE_P_RE = re.compile(r"</p\s*>", re.IGNORECASE)
_CLOSE_LI_RE = re.compile(r"</li\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]*>")
_HORIZONTAL_WS_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def html_to_text(html: Optional[str]) -> str:
    """Convert an HTML fragment to collapsed plain text.

    ``<br>`` becomes a single newline, ``</p>`` a blank line, and ``</li>`` a newline; every
    remaining tag is stripped. HTML entities are unescaped **after** tags are stripped, so a
    source entity such as ``&lt;script&gt;`` cannot re-introduce an angle-bracket tag that the
    strip pass already removed. Finally runs of horizontal whitespace collapse to one space and
    runs of three-or-more newlines collapse to a blank line, and the result is stripped.

    :param html: The HTML fragment to convert, or ``None``.
    :returns: The plain-text rendering, or ``""`` when ``html`` is ``None``.
    """
    if html is None:
        return ""
    text = _BR_RE.sub("\n", html)
    text = _CLOSE_P_RE.sub("\n\n", text)
    text = _CLOSE_LI_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    text = _HORIZONTAL_WS_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()
