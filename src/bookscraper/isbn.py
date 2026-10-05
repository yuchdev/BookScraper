"""ISBN cleaning, checksum validation, and canonicalization (subtask 03.1/02).

Scraped ISBN text is not trustworthy: it arrives hyphenated (``"978-1-09-813102-9"``), bare
(``"1098131029"``), label-prefixed (``"ISBN-13 : 978..."``), as an ``"N/A"`` sentinel, or as
garbage from a drifted selector that actually matched a page count. Nothing downstream
validated it, so a mis-matched element could be stored as an ISBN, and cross-site
deduplication (task 03.3) has no canonical key to join on.

This module is the one place that turns that raw text into a checksum-valid, canonical pair.
It is pure (no I/O beyond a debug log) and lives at the package root next to
:mod:`bookscraper.models`, so both ``backends/`` and ``scraping/`` may import it without
violating the one-way import direction. Following the milestone's "fail loudly, never guess"
rule only where a contract is truly violated: the public entry points
(:func:`clean`, :func:`normalize`, :func:`extract_first`) never raise on bad *input* - invalid
text simply becomes ``None`` with a truncated debug log - while :func:`isbn10_to_isbn13`, an
internal conversion whose precondition is a validated ISBN-10, raises :class:`ValueError` if
that precondition is broken.
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: Matches a plausible ISBN candidate inside free text: a leading digit, at least eight
#: interior digit / ``X`` / separator characters, and a trailing digit or ``X``. Used by
#: :func:`extract_first` to pick runs out of label-prefixed selector text before cleaning.
_CANDIDATE_RE = re.compile(r"[0-9][0-9Xx\s-]{8,}[0-9Xx]")

#: Everything that is not an ISBN character; stripped by :func:`clean`.
_NON_ISBN_RE = re.compile(r"[^0-9Xx]")

#: The two EAN prefixes a Bookland ISBN-13 may carry.
_VALID_PREFIXES = ("978", "979")

#: How many characters of a rejected raw value reach the debug log (never the full string).
_LOG_TRUNCATE = 32


def clean(raw: Optional[str]) -> str:
    """Strip a raw ISBN string down to its significant characters.

    Removes every character that is not a digit or ``X``/``x`` (hyphens, spaces, and any
    ``ISBN-13 :`` style label), then uppercases so a check digit written ``x`` matches ``X``.

    :param raw: The raw scraped text, or ``None``.
    :returns: The cleaned string (``""`` when ``raw`` is ``None`` or has no ISBN characters).
    """
    if raw is None:
        return ""
    return _NON_ISBN_RE.sub("", raw).upper()


def is_valid_isbn10(s: str) -> bool:
    """Report whether ``s`` is a checksum-valid ISBN-10.

    Requires exactly ten characters, digits only except for an optional ``X`` in the final
    position (worth ten), and a weighted sum (weights ten down to one) divisible by eleven.

    :param s: A cleaned candidate (see :func:`clean`).
    :returns: ``True`` only if ``s`` is a well-formed, checksum-valid ISBN-10.
    """
    if len(s) != 10:
        return False
    total = 0
    for i, ch in enumerate(s):
        if ch == "X":
            if i != 9:
                return False
            value = 10
        elif ch.isdigit():
            value = int(ch)
        else:
            return False
        total += value * (10 - i)
    return total % 11 == 0


def is_valid_isbn13(s: str) -> bool:
    """Report whether ``s`` is a checksum-valid ISBN-13.

    Requires exactly thirteen digits, a ``978`` or ``979`` prefix, and an alternating
    ``1``/``3`` weighted sum divisible by ten.

    :param s: A cleaned candidate (see :func:`clean`).
    :returns: ``True`` only if ``s`` is a well-formed, checksum-valid ISBN-13.
    """
    if len(s) != 13 or not s.isdigit():
        return False
    if s[:3] not in _VALID_PREFIXES:
        return False
    total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(s))
    return total % 10 == 0


def isbn10_to_isbn13(s: str) -> str:
    """Convert a valid ISBN-10 to its canonical ISBN-13.

    Prepends ``978`` to the first nine digits and appends a freshly computed ISBN-13 check
    digit (the original ISBN-10 check digit is discarded, as the two checksums differ).

    :param s: A checksum-valid ISBN-10 (typically already validated by the caller).
    :returns: The equivalent checksum-valid ISBN-13.
    :raises ValueError: if ``s`` is not a valid ISBN-10.
    """
    if not is_valid_isbn10(s):
        raise ValueError("isbn10_to_isbn13 requires a valid ISBN-10")
    core = "978" + s[:9]
    check = (10 - sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(core)) % 10) % 10
    return core + str(check)


def normalize(raw10: Optional[str], raw13: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Clean, validate, and canonicalize a scraped ISBN-10 / ISBN-13 pair.

    Each raw value is cleaned and validated independently; anything that does not pass becomes
    ``None`` (never an exception) with a truncated debug log. When only a valid ISBN-10 is
    present, the matching ISBN-13 is derived from it, so a record always carries the canonical
    ISBN-13 used for cross-site deduplication when any valid ISBN was scraped.

    :param raw10: The raw ISBN-10 text, or ``None``.
    :param raw13: The raw ISBN-13 text, or ``None``.
    :returns: A ``(isbn10, isbn13)`` tuple, each element a canonical value or ``None``.
    """
    isbn10 = _resolve_single(raw10, is_valid_isbn10, "isbn10")
    isbn13 = _resolve_single(raw13, is_valid_isbn13, "isbn13")
    if isbn13 is None and isbn10 is not None:
        isbn13 = isbn10_to_isbn13(isbn10)
    return isbn10, isbn13


def extract_first(text: str) -> Optional[str]:
    """Return the first checksum-valid ISBN found in free text, or ``None``.

    Scans left to right for ISBN-shaped runs (digits and separators) and returns the first one
    that cleans to a valid ISBN-13 or ISBN-10, so a label-prefixed selector value such as
    ``"ISBN-13 : 978-1-09-813102-9"`` yields the ISBN rather than the leading label digits.

    :param text: Arbitrary text that may embed an ISBN.
    :returns: The first valid ISBN (canonical, cleaned form), or ``None`` if none is found.
    """
    for match in _CANDIDATE_RE.finditer(text):
        candidate = clean(match.group())
        if len(candidate) == 13 and is_valid_isbn13(candidate):
            return candidate
        if len(candidate) == 10 and is_valid_isbn10(candidate):
            return candidate
    return None


def _resolve_single(raw: Optional[str], validator: Callable[[str], bool], label: str) -> Optional[str]:
    """Clean and validate a single raw ISBN value, logging a rejection.

    :param raw: The raw scraped text, or ``None``.
    :param validator: The checksum validator to apply (:func:`is_valid_isbn10` or
        :func:`is_valid_isbn13`).
    :param label: The field name used in the debug log on rejection.
    :returns: The cleaned value if valid, else ``None``.
    """
    cleaned = clean(raw)
    if validator(cleaned):
        return cleaned
    if raw:
        logger.debug("rejected %s value %r", label, raw[:_LOG_TRUNCATE])
    return None
