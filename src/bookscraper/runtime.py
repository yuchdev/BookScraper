"""Runtime options shared by the scraping commands.

``RuntimeOptions`` collects the cross-cutting, run-wide knobs the two scraping subcommands
(``scrape-urls``/``search``) and the ``detect-schema`` maintenance tool resolve the same way:
whether the browser runs headless, how many scrapes run concurrently, the per-page navigation
timeout, and whether ``robots.txt`` is consulted. :func:`runtime_options` is the single place that
applies the flag -> env -> ``parameters.py`` precedence, so a command passes a resolved
``RuntimeOptions`` down instead of importing ``HEADLESS_BROWSER`` (or re-reading the environment)
itself.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Optional

from .book_utils import print_log
from .scraping.parameters import HEADLESS_BROWSER

#: Default number of detail scrapes in flight at once when ``--concurrency`` is not supplied. Must
#: match the ``--concurrency`` default in ``cli.py``; it is the fallback :func:`runtime_options`
#: uses for a Namespace (e.g. ``detect-schema``) that carries no ``concurrency`` attribute.
DEFAULT_CONCURRENCY = 4

#: Default per-page navigation timeout in seconds when ``--nav-timeout`` is not supplied. Must match
#: the ``--nav-timeout`` default in ``cli.py``; it converts to ``NAVIGATION_TIMEOUT_MS`` (60 000 ms).
DEFAULT_NAV_TIMEOUT_S = 60.0

#: Environment-variable spellings :func:`_parse_bool_env` accepts as truthy (case-insensitive).
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})

#: Environment-variable spellings :func:`_parse_bool_env` accepts as falsy (case-insensitive).
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class RuntimeOptions:
    """Resolved, run-wide runtime knobs threaded from the CLI down to the scraping layer.

    :ivar headless: Whether the Playwright browser runs headless.
    :ivar concurrency: Maximum number of detail scrapes in flight at once (1-32).
    :ivar nav_timeout_ms: Per-page navigation timeout in milliseconds (``--nav-timeout`` seconds
        converted to ms), fed to ``scrape_details.NAVIGATION_TIMEOUT_MS``'s call sites.
    :ivar ignore_robots: Whether ``robots.txt`` is skipped for the run.
    """

    headless: bool
    concurrency: int
    nav_timeout_ms: int
    ignore_robots: bool


def _parse_bool_env(raw: str) -> Optional[bool]:
    """Parse a boolean environment value, accepting common spellings case-insensitively.

    :param raw: The raw environment-variable value (e.g. ``"1"``, ``"true"``, ``"off"``).
    :returns: ``True`` / ``False`` for a recognized spelling, or ``None`` when unrecognized so the
        caller can fall back to the configured default rather than guessing.
    """
    token = raw.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return None


def _resolve_headless(flag: Optional[bool]) -> bool:
    """Resolve the headless setting by the ``flag -> env -> parameters.py`` precedence.

    :param flag: The ``--headless`` / ``--no-headless`` value (``None`` when neither was passed).
    :returns: The CLI flag when given, else the ``BOOKSCRAPER_HEADLESS`` env var when it names a
        recognized spelling, else the :data:`~bookscraper.scraping.parameters.HEADLESS_BROWSER`
        default. An unrecognized env value warns once and falls through to the default.
    """
    if flag is not None:
        return flag
    raw = os.environ.get("BOOKSCRAPER_HEADLESS")
    if raw is not None:
        parsed = _parse_bool_env(raw)
        if parsed is not None:
            return parsed
        print_log(
            f"Ignoring unrecognized BOOKSCRAPER_HEADLESS value {raw!r}; using the configured default.",
            "warning",
        )
    return HEADLESS_BROWSER


def runtime_options(args: argparse.Namespace) -> RuntimeOptions:
    """Resolve the run-wide runtime knobs from parsed CLI arguments.

    Applies the documented precedence for each field and converts ``--nav-timeout`` seconds to
    milliseconds. Fields a given subcommand's Namespace does not carry (e.g. ``detect-schema`` has
    no ``concurrency`` / ``ignore_robots``) fall back to their module defaults, so one helper serves
    every command.

    :param args: The parsed subcommand arguments.
    :returns: The resolved :class:`RuntimeOptions`.
    """
    headless = _resolve_headless(getattr(args, "headless", None))
    concurrency = getattr(args, "concurrency", DEFAULT_CONCURRENCY)
    nav_timeout_s = getattr(args, "nav_timeout_s", DEFAULT_NAV_TIMEOUT_S)
    nav_timeout_ms = int(round(nav_timeout_s * 1000))
    ignore_robots = bool(getattr(args, "ignore_robots", False))
    return RuntimeOptions(
        headless=headless,
        concurrency=concurrency,
        nav_timeout_ms=nav_timeout_ms,
        ignore_robots=ignore_robots,
    )
