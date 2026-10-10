"""Control-character sanitizer for untrusted strings printed or serialized to a report.

:func:`sanitize_untrusted` is the single place that strips terminal escape/ANSI and
C0/C1/DEL control characters (plus the Unicode line/paragraph separators U+2028/U+2029) from
any string that came from an untrusted source - a scraped page, an LLM response, or a
page-derived identifier - before it is printed to a console/log or embedded in a JSON report.
A hostile value could otherwise emit ANSI escape sequences or a line break that spoofs a new
report line into stdout, or corrupt a JSON CI artifact (AIS-13 / PI-3).

It lives in this tiny stdlib-only module - rather than in :mod:`bookscraper.scraping.schema_detection`
where it originated - so that lightweight callers (e.g. ``commands/migrate.py`` printing
page-derived report ids) can reuse it without importing the heavy ``anthropic`` / Playwright
dependencies that module pulls in. ``schema_detection`` re-exports it so existing imports keep
working. This module lives under ``scraping/`` and imports only the standard library.
"""

from __future__ import annotations

import re
from typing import Optional

#: Strip ESC (``\x1b``) and every C0 (``\x00``-``\x1f``) / C1 (``\x80``-``\x9f``, which already
#: covers U+0085 NEL) / DEL (``\x7f``) control character - plus U+2028 / U+2029 - from an
#: untrusted string. Printable bytes (including the leftover ``[31m`` of a stripped ``\x1b[31m``)
#: are kept verbatim; only the dangerous control/separator bytes are removed.
_UNTRUSTED_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def sanitize_untrusted(value: Optional[str]) -> Optional[str]:
    """Strip ESC/ANSI and C0/C1/DEL control characters from one untrusted string (AIS-13 / PI-3).

    The single place control-char/ANSI stripping is applied, so every untrusted field reaches a
    console, log, or report identically sanitized. ``None`` passes through unchanged; all other
    values are returned with only their control/separator bytes removed - printable content,
    including the leftover ``[31m`` of a neutered ``\\x1b[31m`` sequence, is preserved verbatim.

    :param value: The untrusted string, or ``None``.
    :returns: ``None`` when ``value`` is ``None``; otherwise ``value`` with its control and
        line/paragraph-separator characters removed.
    """
    if value is None:
        return None
    return _UNTRUSTED_CONTROL_RE.sub("", value)
