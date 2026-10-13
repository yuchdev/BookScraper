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
from pathlib import Path
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
    :ivar dry_run: Whether the run reads for dedup but saves nothing (no ``save_books`` call).
    :ivar output_dir: Directory the diagnostic CSVs are written to (``.`` by default).
    :ivar json_store: Explicit ``json`` backend store-file path, or ``None`` to use the default
        ``books.json``. Always ``None`` unless ``--store-backend json`` is in use.
    """

    headless: bool
    concurrency: int
    nav_timeout_ms: int
    ignore_robots: bool
    dry_run: bool
    output_dir: str
    json_store: Optional[Path]


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
    :raises SystemExit: With usage code ``2`` when ``--json-store`` is combined with
        ``--store-backend mongo`` (the json store file has no meaning for the mongo backend).
    """
    headless = _resolve_headless(getattr(args, "headless", None))
    concurrency = getattr(args, "concurrency", DEFAULT_CONCURRENCY)
    nav_timeout_s = getattr(args, "nav_timeout_s", DEFAULT_NAV_TIMEOUT_S)
    nav_timeout_ms = int(round(nav_timeout_s * 1000))
    ignore_robots = bool(getattr(args, "ignore_robots", False))
    dry_run = bool(getattr(args, "dry_run", False))
    output_dir = getattr(args, "output_dir", None) or "."
    json_store = _resolve_json_store(getattr(args, "json_store", None), getattr(args, "store_backend", None))
    return RuntimeOptions(
        headless=headless,
        concurrency=concurrency,
        nav_timeout_ms=nav_timeout_ms,
        ignore_robots=ignore_robots,
        dry_run=dry_run,
        output_dir=output_dir,
        json_store=json_store,
    )


def _resolve_json_store(raw: Optional[str], store_backend: Optional[str]) -> Optional[Path]:
    """Resolve ``--json-store`` into a path, rejecting its use with the mongo backend.

    :param raw: The raw ``--json-store`` value (``None`` when the flag was not supplied).
    :param store_backend: The resolved ``--store-backend`` value, used only to reject the
        meaningless ``--json-store`` + ``mongo`` combination.
    :returns: The store-file :class:`~pathlib.Path` when the flag was given, else ``None`` so the
        json backend falls back to its default ``books.json``.
    :raises SystemExit: With usage code ``2`` when ``raw`` is given alongside the mongo backend.
    """
    if raw is None:
        return None
    if store_backend == "mongo":
        print_log(
            "--json-store only applies to --store-backend json; it cannot be combined with --store-backend mongo.",
            "error",
        )
        raise SystemExit(2)
    return Path(raw)
