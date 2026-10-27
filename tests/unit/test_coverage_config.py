"""Guard the coverage gate declared in ``pyproject.toml``.

The ≥90% combined line+branch coverage bar CLAUDE.md promises is only real if it is enforced in
configuration, not just written in a doc command line. ``[tool.coverage.report] fail_under = 90`` plus
``[tool.coverage.run] source = ["bookscraper"]`` and ``branch = true`` is what makes the canonical
``uv run pytest --cov`` fail below the bar on the combined figure; this test pins those values so a
silent edit (or deletion) of the gate is caught by the suite itself rather than discovered the day
coverage regresses unnoticed.
"""

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _coverage_config() -> dict:
    """Parse ``pyproject.toml`` and return its ``[tool.coverage]`` table.

    :returns: The ``tool.coverage`` mapping (``run`` and ``report`` sub-tables).
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return data["tool"]["coverage"]


def test_pyproject_declares_coverage_fail_under_90():
    """
    [Unit] coverage config: verifies pyproject.toml enforces the 90% combined line+branch gate against
    the bookscraper source.

    Scenario:
        Given the committed pyproject.toml parsed with tomllib
        When the [tool.coverage.run] and [tool.coverage.report] tables are read
        Then source is exactly ["bookscraper"], branch is True (so the gated figure is combined
             line+branch, not line-only), fail_under is 90, show_missing is enabled, and skip_covered
             is True, so the canonical `uv run pytest --cov` measures the package and fails below the bar.

    Boundaries:
        - Pure config parse; no coverage run is invoked here (the manual fail_under=99 check proves enforcement)
        - Asserts the exact fail_under integer, not merely its presence, so a weakened gate is caught
        - Asserts branch and skip_covered are the literal True, not merely truthy, so a silent flip is caught

    On failure, first check:
        - That [tool.coverage.report] fail_under is still 90 (not removed or lowered)
        - That [tool.coverage.run] branch is still True so the gate measures combined line+branch coverage
        - That [tool.coverage.report] skip_covered is still True so the report only lists under-covered files
        - That [tool.coverage.run] source still names the bookscraper package so `--cov` alone measures it
    """
    coverage = _coverage_config()

    assert coverage["run"]["source"] == ["bookscraper"]
    assert coverage["run"]["branch"] is True
    assert coverage["report"]["fail_under"] == 90
    assert coverage["report"]["show_missing"] is True
    assert coverage["report"]["skip_covered"] is True
