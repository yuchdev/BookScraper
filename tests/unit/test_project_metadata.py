"""Guards that keep the declared Python floor consistent across project metadata."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> dict:
    """Parse a repo-root TOML file.

    :param name: File name relative to the repository root.
    :returns: The parsed TOML document.
    """
    with (ROOT / name).open("rb") as handle:
        return tomllib.load(handle)


def _floor(requires_python: str) -> tuple[int, int]:
    """Parse a simple ``>=X.Y`` requires-python floor.

    :param requires_python: The ``requires-python`` string.
    :returns: ``(major, minor)``.
    """
    match = re.fullmatch(r">=\s*(\d+)\.(\d+)(?:\.\d+)?", requires_python.strip())
    assert match, f"requires-python must be a simple '>=X.Y' floor, got {requires_python!r}"
    return int(match.group(1)), int(match.group(2))


def test_requires_python_matches_ruff_target_version():
    """
    [Unit] project metadata: pyproject's requires-python floor equals ruff's target-version.

    Scenario:
        Given pyproject.toml and ruff.toml on disk
        When both Python floors are parsed
        Then they name the same minor version.

    Boundaries:
        - Pure TOML parsing; no tool is executed

    On failure, first check:
        - ruff.toml target-version and pyproject.toml requires-python were bumped together
    """
    major, minor = _floor(_load("pyproject.toml")["project"]["requires-python"])
    target = _load("ruff.toml")["target-version"]
    assert target == f"py{major}{minor}"


def test_classifiers_declare_the_floor_minor_version():
    """
    [Unit] project metadata: a trove classifier exists for the declared floor version.

    Scenario:
        Given pyproject.toml classifiers and requires-python
        When the floor is parsed
        Then a "Programming Language :: Python :: X.Y" classifier is present for it.

    Boundaries:
        - Pure TOML parsing

    On failure, first check:
        - The classifiers list in pyproject.toml after a requires-python bump
    """
    project = _load("pyproject.toml")["project"]
    major, minor = _floor(project["requires-python"])
    assert f"Programming Language :: Python :: {major}.{minor}" in project["classifiers"]
