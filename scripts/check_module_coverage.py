#!/usr/bin/env python3
"""Enforce a per-module coverage floor on ``src/bookscraper/`` (task 06.1 / 03).

A high *total* coverage number can hide a single badly-tested module: the aggregate gate
(``--cov-fail-under=90`` in ``pyproject.toml``) passes even when one module has rotted to
60%, because the well-covered modules average it back up. This script reads the
``coverage.json`` produced by ``uv run pytest --cov --cov-report=json`` and fails (exit 1)
when **any** in-scope module under ``src/bookscraper/`` falls below ``MODULE_FLOOR`` (90) on
its combined line+branch coverage, printing a table of the offenders so the regression is
named, not averaged away.

A module may be granted a *temporary* lower floor via the allow-list
``tests/coverage_floor_exceptions.toml``. Each entry must carry a ``floor`` plus a
human ``reason`` and a linked ``until`` task - an entry missing either is a hard
configuration error (exit 2), so an exception can never be added silently or left
un-tracked. The file ships empty: every module is expected to clear the floor on its own.

This is a *dev tool*, not product code: it is outside the coverage scope (``scripts/*.py``),
so it uses ``print`` for its report like the other ``scripts/`` CLIs.

Usage::

    uv run pytest --cov --cov-report=json -q
    uv run python scripts/check_module_coverage.py
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# The per-module combined line+branch coverage floor every in-scope module must clear on its own.
MODULE_FLOOR: int = 90

# Repo-relative default locations, resolved against this file's parent-parent (the repo root).
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COVERAGE_JSON = _REPO_ROOT / "coverage.json"
DEFAULT_EXCEPTIONS_TOML = _REPO_ROOT / "tests" / "coverage_floor_exceptions.toml"

# Only modules whose coverage path lives under this prefix are gated (scripts/ is out of scope).
_SCOPE_PREFIX = "src/bookscraper/"


@dataclass(frozen=True)
class ModuleException:
    """One validated allow-list entry: a module's temporary lower floor and why it exists."""

    module: str
    floor: float
    reason: str
    until: str


@dataclass(frozen=True)
class Offender:
    """A module that fell below its effective floor, carried into the report table."""

    module: str
    percent: float
    floor: float


def module_name_from_path(path: str) -> str:
    """Convert a coverage file key to a dotted module name used as the allow-list key.

    ``"src/bookscraper/scraping/search_utils.py"`` becomes
    ``"bookscraper.scraping.search_utils"``; a path outside ``src/`` is returned with only
    its separators normalized and the ``.py`` suffix dropped.

    :param path: The coverage.json file key (a repo-relative POSIX-style path).
    :returns: The dotted module name.
    """
    normalized = path.replace("\\", "/")
    marker = "src/"
    if marker in normalized:
        normalized = normalized.split(marker, 1)[1]
    if normalized.endswith(".py"):
        normalized = normalized[: -len(".py")]
    return normalized.replace("/", ".")


def is_in_scope(path: str) -> bool:
    """Return whether a coverage file key is an ``src/bookscraper/`` module this gate covers.

    :param path: The coverage.json file key.
    :returns: ``True`` for modules under ``src/bookscraper/``, ``False`` otherwise.
    """
    return _SCOPE_PREFIX in path.replace("\\", "/")


def load_exceptions(data: dict[str, Any]) -> dict[str, ModuleException]:
    """Validate a parsed exceptions table, returning it keyed by module name.

    Each entry must be a table carrying a numeric ``floor`` plus a non-empty ``reason`` and
    ``until`` (the linked task). A missing or blank field is a hard configuration error so an
    exception can never be added without a tracked justification.

    :param data: The parsed TOML document (module name -> entry table).
    :returns: A mapping of module name to its validated :class:`ModuleException`.
    :raises ValueError: If any entry is malformed or missing a required field.
    """
    result: dict[str, ModuleException] = {}
    for module, entry in data.items():
        if not isinstance(entry, dict):
            raise ValueError(f"exception for {module!r} must be a table, got {type(entry).__name__}")
        if "floor" not in entry:
            raise ValueError(f"exception for {module!r} must set a numeric 'floor'")
        floor = entry["floor"]
        if not isinstance(floor, (int, float)) or isinstance(floor, bool):
            raise ValueError(f"exception for {module!r} 'floor' must be a number, got {floor!r}")
        reason = str(entry.get("reason", "")).strip()
        if not reason:
            raise ValueError(f"exception for {module!r} must have a non-empty 'reason'")
        until = str(entry.get("until", "")).strip()
        if not until:
            raise ValueError(f"exception for {module!r} must have a non-empty 'until' (linked task)")
        result[module] = ModuleException(module=module, floor=float(floor), reason=reason, until=until)
    return result


def find_offenders(
    coverage_data: dict[str, Any],
    exceptions: dict[str, ModuleException],
    floor: float = MODULE_FLOOR,
) -> list[Offender]:
    """Return the in-scope modules below their effective floor, sorted worst-first.

    A module's effective floor is ``floor`` unless an allow-list exception grants it a lower
    one; a module is an offender only when it is below that effective floor.

    :param coverage_data: The parsed ``coverage.json`` document.
    :param exceptions: The validated allow-list, keyed by module name.
    :param floor: The default per-module floor (``MODULE_FLOOR``).
    :returns: The offending modules, ordered by ascending coverage percentage.
    :raises ValueError: If a file entry lacks the ``summary.percent_covered`` number (a
        malformed or truncated ``coverage.json``).
    """
    offenders: list[Offender] = []
    for path, info in coverage_data.get("files", {}).items():
        if not is_in_scope(path):
            continue
        module = module_name_from_path(path)
        try:
            percent = float(info["summary"]["percent_covered"])
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed coverage entry for {path!r}: missing summary.percent_covered") from exc
        effective_floor = exceptions[module].floor if module in exceptions else floor
        if percent < effective_floor:
            offenders.append(Offender(module=module, percent=percent, floor=effective_floor))
    offenders.sort(key=lambda o: o.percent)
    return offenders


def format_table(offenders: list[Offender]) -> str:
    """Render the offenders as a fixed-width table naming each module, its coverage, and floor.

    :param offenders: The offending modules (see :func:`find_offenders`).
    :returns: A multi-line table string (empty offenders yields a single "all clear" line).
    """
    if not offenders:
        return "All src/bookscraper/ modules meet the per-module coverage floor."
    module_width = max(len("Module"), *(len(o.module) for o in offenders))
    header = f"{'Module':<{module_width}}  {'Coverage':>8}  {'Floor':>6}"
    separator = "-" * len(header)
    rows = [f"{o.module:<{module_width}}  {o.percent:>7.2f}%  {o.floor:>5.0f}%" for o in offenders]
    return "\n".join([header, separator, *rows])


def _load_json(path: Path) -> dict[str, Any]:
    """Read and parse a ``coverage.json`` file.

    :param path: The path to ``coverage.json``.
    :returns: The parsed document.
    :raises FileNotFoundError: If the file does not exist.
    """
    with path.open("rb") as handle:
        return json.load(handle)


def _load_exceptions_file(path: Path) -> dict[str, ModuleException]:
    """Read and validate the exceptions TOML (an absent file means no exceptions).

    :param path: The path to ``coverage_floor_exceptions.toml``.
    :returns: The validated allow-list, keyed by module name.
    :raises ValueError: If any entry is malformed (see :func:`load_exceptions`).
    """
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return load_exceptions(data)


def main(argv: Optional[list[str]] = None) -> int:
    """Run the per-module coverage gate and return a process exit code.

    :param argv: Argument vector (defaults to ``sys.argv[1:]``).
    :returns: ``0`` when every in-scope module clears its floor, ``1`` when any is below it,
        ``2`` on a configuration error (missing coverage.json, a malformed exception, or a
        malformed/truncated coverage.json lacking summary.percent_covered).
    """
    parser = argparse.ArgumentParser(description="Enforce a per-module coverage floor on src/bookscraper/.")
    parser.add_argument(
        "--coverage-json",
        type=Path,
        default=DEFAULT_COVERAGE_JSON,
        help="Path to coverage.json (default: repo-root coverage.json).",
    )
    parser.add_argument(
        "--exceptions",
        type=Path,
        default=DEFAULT_EXCEPTIONS_TOML,
        help="Path to the coverage floor exceptions TOML (default: tests/coverage_floor_exceptions.toml).",
    )
    args = parser.parse_args(argv)

    try:
        coverage_data = _load_json(args.coverage_json)
    except FileNotFoundError:
        print(
            f"coverage.json not found at {args.coverage_json}; run 'uv run pytest --cov --cov-report=json' first.",
            file=sys.stderr,
        )
        return 2

    try:
        exceptions = _load_exceptions_file(args.exceptions)
    except ValueError as exc:
        print(f"invalid {args.exceptions}: {exc}", file=sys.stderr)
        return 2

    try:
        offenders = find_offenders(coverage_data, exceptions, MODULE_FLOOR)
    except ValueError as exc:
        print(f"invalid {args.coverage_json}: {exc}", file=sys.stderr)
        return 2

    print(format_table(offenders))
    if offenders:
        print(
            f"\n{len(offenders)} module(s) below the per-module floor of {MODULE_FLOOR}%.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
