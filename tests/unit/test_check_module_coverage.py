"""Unit tests for scripts/check_module_coverage.py (task 06.1 / 03).

The helper tests feed a synthetic ``coverage.json`` document (a plain dict) and/or a synthetic
exceptions table to the script's parsing/validation helpers, so no pytest run and no real
coverage data is involved. The ``main`` exit-code tests additionally write synthetic
``coverage.json`` / exceptions TOML files under ``tmp_path`` (the only file I/O here) to drive
``main`` end-to-end through its documented 0/1/2 contract. The script is a dev tool outside the
package, so it is loaded by path with importlib (the same pattern as
``tests/unit/scraping/test_fixture_drift.py``).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_module_coverage.py"


def _load_module() -> ModuleType:
    """Load scripts/check_module_coverage.py by path so it can be tested without being a package.

    The module is registered in ``sys.modules`` before execution so that its frozen dataclasses
    (whose annotations are strings under ``from __future__ import annotations``) can resolve their
    own module namespace during ``@dataclass`` processing.
    """
    spec = importlib.util.spec_from_file_location("check_module_coverage", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load_module()


def _coverage_doc(modules: dict[str, float]) -> dict[str, Any]:
    """Build a synthetic coverage.json document from ``path -> percent_covered`` pairs.

    :param modules: Coverage file key -> percent_covered float.
    :returns: A minimal coverage.json-shaped dict carrying only the summaries the script reads.
    """
    return {"files": {path: {"summary": {"percent_covered": percent}} for path, percent in modules.items()}}


def test_floor_script_passes_when_all_modules_above_floor():
    """
    [Unit] coverage gate: verifies no offenders when every in-scope module meets the floor.

    Scenario:
        Given a synthetic coverage.json where three src/bookscraper modules sit at 90, 95.5
        and 100 percent (and an out-of-scope scripts/ file at 10%), with no exceptions
        When find_offenders runs at MODULE_FLOOR
        Then it returns an empty offender list (90 is inclusive, the scripts/ file is ignored)
        and format_table reports the all-clear line.

    Boundaries:
        - 90.0 is exactly at the floor and must pass (inclusive); out-of-scope paths are skipped.

    On failure, first check:
        - The ``percent < effective_floor`` comparison and is_in_scope's src/bookscraper/ filter.
    """
    doc = _coverage_doc(
        {
            "src/bookscraper/scraping/search_utils.py": 90.0,
            "src/bookscraper/backends/storage.py": 95.5,
            "src/bookscraper/models.py": 100.0,
            "scripts/check_doc_links.py": 10.0,
        }
    )

    offenders = check.find_offenders(doc, {}, check.MODULE_FLOOR)

    assert offenders == []
    assert check.format_table(offenders) == "All src/bookscraper/ modules meet the per-module coverage floor."


def test_floor_script_fails_and_lists_offenders():
    """
    [Unit] coverage gate: verifies sub-floor modules are reported worst-first in the table.

    Scenario:
        Given a synthetic coverage.json with two modules below 90 (80.0 and 88.5) and one at
        100, with no exceptions
        When find_offenders runs
        Then exactly the two sub-floor modules are returned, ordered by ascending coverage
        (80.0 before 88.5), each carrying the default floor of 90, and format_table names both
        with their percentages.

    Boundaries:
        - Only modules strictly below the floor are offenders; the 100% module is excluded and
          the worst offender sorts first.

    On failure, first check:
        - find_offenders' sort key and the dotted module_name_from_path conversion.
    """
    doc = _coverage_doc(
        {
            "src/bookscraper/scraping/search_utils.py": 88.5,
            "src/bookscraper/scraping/scrape_details.py": 80.0,
            "src/bookscraper/models.py": 100.0,
        }
    )

    offenders = check.find_offenders(doc, {}, check.MODULE_FLOOR)

    assert [o.module for o in offenders] == [
        "bookscraper.scraping.scrape_details",
        "bookscraper.scraping.search_utils",
    ]
    assert [o.percent for o in offenders] == [80.0, 88.5]
    assert all(o.floor == 90 for o in offenders)

    table = check.format_table(offenders)
    assert "bookscraper.scraping.scrape_details" in table
    assert "80.00%" in table
    assert "bookscraper.scraping.search_utils" in table
    assert "88.50%" in table


def test_floor_exception_requires_reason_and_until():
    """
    [Unit] coverage gate: verifies an allow-list entry missing reason/until is rejected, and a
    complete entry raises a module's effective floor so a tracked gap is not an offender.

    Scenario:
        Given exception tables that (a) omit 'until', (b) omit 'reason', and (c) carry a blank
        reason, When load_exceptions runs on each, Then each raises ValueError naming the
        offending module. And given a complete entry {floor=85, reason, until} for a module
        sitting at 86%, When find_offenders runs, Then that module is NOT an offender (86 >= its
        85 floor) while a 100% module stays clear - proving a valid exception lowers only its
        own module's floor.

    Boundaries:
        - Validation is all-or-nothing per entry: floor+reason+until are all mandatory and a
          whitespace-only reason counts as missing.

    On failure, first check:
        - load_exceptions' non-empty reason/until checks and find_offenders' per-module
          effective-floor lookup.
    """
    with pytest.raises(ValueError, match="until"):
        check.load_exceptions({"bookscraper.scraping.search_utils": {"floor": 85, "reason": "WIP"}})

    with pytest.raises(ValueError, match="reason"):
        check.load_exceptions({"bookscraper.scraping.search_utils": {"floor": 85, "until": "06.1/03"}})

    with pytest.raises(ValueError, match="reason"):
        check.load_exceptions({"bookscraper.scraping.search_utils": {"floor": 85, "reason": "   ", "until": "06.1/03"}})

    exceptions = check.load_exceptions(
        {
            "bookscraper.scraping.search_utils": {
                "floor": 85,
                "reason": "live-markup branches move to the golden tier",
                "until": "06.1/03",
            }
        }
    )
    assert exceptions["bookscraper.scraping.search_utils"].floor == 85.0

    doc = _coverage_doc(
        {
            "src/bookscraper/scraping/search_utils.py": 86.0,
            "src/bookscraper/models.py": 100.0,
        }
    )
    offenders = check.find_offenders(doc, exceptions, check.MODULE_FLOOR)
    assert offenders == []


def _write_coverage_json(path: Path, doc: dict[str, Any]):
    """Serialize a synthetic coverage.json document to ``path``.

    :param path: Destination file (created/overwritten).
    :param doc: A coverage.json-shaped dict (see :func:`_coverage_doc`).
    """
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_main_missing_coverage_json_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """
    [Unit] coverage gate: verifies main() exits 2 when --coverage-json points at no file.

    Scenario:
        Given a --coverage-json path under tmp_path that was never created
        When main runs with it
        Then it returns 2 (configuration error) and names the missing path on stderr, telling the
        operator to run pytest --cov --cov-report=json first.

    Boundaries:
        - A missing coverage.json is a configuration error (2), never silently treated as "all clear".

    On failure, first check:
        - main's FileNotFoundError branch around _load_json and that it returns (not raises).
    """
    missing = tmp_path / "coverage.json"

    exit_code = check.main(["--coverage-json", str(missing), "--exceptions", str(tmp_path / "none.toml")])

    assert exit_code == 2
    assert "coverage.json not found" in capsys.readouterr().err


def test_main_malformed_exceptions_toml_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """
    [Unit] coverage gate: verifies main() exits 2 on an exceptions TOML entry missing 'until'.

    Scenario:
        Given a valid coverage.json (one in-scope module at 100%) and an exceptions TOML whose sole
        entry supplies floor+reason but omits the mandatory 'until' linked task
        When main runs
        Then it returns 2 and the stderr names the offending exceptions file, because an untracked
        exception must never be accepted silently.

    Boundaries:
        - A malformed allow-list is a configuration error (2), distinct from an offender (1).

    On failure, first check:
        - main's ValueError branch around _load_exceptions_file and load_exceptions' 'until' check.
    """
    coverage_json = tmp_path / "coverage.json"
    _write_coverage_json(coverage_json, _coverage_doc({"src/bookscraper/models.py": 100.0}))
    exceptions = tmp_path / "exceptions.toml"
    exceptions.write_text(
        '[bookscraper.models]\nfloor = 85\nreason = "WIP"\n',
        encoding="utf-8",
    )

    exit_code = check.main(["--coverage-json", str(coverage_json), "--exceptions", str(exceptions)])

    assert exit_code == 2
    assert str(exceptions) in capsys.readouterr().err


def test_main_malformed_coverage_json_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """
    [Unit] coverage gate: verifies main() exits 2 on a coverage.json entry missing summary.

    Scenario:
        Given a coverage.json whose in-scope file entry carries no 'summary' key (a truncated or
        malformed export) and no exceptions file
        When main runs
        Then it returns 2 and names the coverage file on stderr, rather than crashing with an
        unhandled KeyError or silently passing.

    Boundaries:
        - A truncated coverage.json is a configuration error (2); the guard catches KeyError/TypeError.

    On failure, first check:
        - find_offenders' guarded percent access and main's ValueError branch around it.
    """
    coverage_json = tmp_path / "coverage.json"
    _write_coverage_json(coverage_json, {"files": {"src/bookscraper/models.py": {}}})

    exit_code = check.main(["--coverage-json", str(coverage_json), "--exceptions", str(tmp_path / "none.toml")])

    assert exit_code == 2
    assert str(coverage_json) in capsys.readouterr().err


def test_main_passing_run_exits_0(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """
    [Unit] coverage gate: verifies main() exits 0 when every in-scope module clears the floor.

    Scenario:
        Given a coverage.json with two in-scope modules at 90% (inclusive) and 100% and no
        exceptions file
        When main runs
        Then it returns 0 and prints the all-clear line to stdout with nothing on stderr.

    Boundaries:
        - 90.0 is exactly at the floor and passes; an absent exceptions file means no exceptions.

    On failure, first check:
        - main's success path and format_table's all-clear line.
    """
    coverage_json = tmp_path / "coverage.json"
    _write_coverage_json(
        coverage_json,
        _coverage_doc(
            {
                "src/bookscraper/models.py": 90.0,
                "src/bookscraper/backends/storage.py": 100.0,
            }
        ),
    )

    exit_code = check.main(["--coverage-json", str(coverage_json), "--exceptions", str(tmp_path / "none.toml")])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "All src/bookscraper/ modules meet the per-module coverage floor." in captured.out
    assert captured.err == ""


def test_main_offender_run_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """
    [Unit] coverage gate: verifies main() exits 1 and names the offender below the floor.

    Scenario:
        Given a coverage.json with one in-scope module at 80% (below the 90 floor) alongside a
        100% module, and no exceptions file
        When main runs
        Then it returns 1, the offender table on stdout names the sub-floor module and its 80.00%,
        and stderr reports the count below the floor.

    Boundaries:
        - A real offender is exit 1, distinct from a configuration error (2); the 100% module is clear.

    On failure, first check:
        - main's `if offenders` branch and find_offenders' percent < floor comparison.
    """
    coverage_json = tmp_path / "coverage.json"
    _write_coverage_json(
        coverage_json,
        _coverage_doc(
            {
                "src/bookscraper/scraping/scrape_details.py": 80.0,
                "src/bookscraper/models.py": 100.0,
            }
        ),
    )

    exit_code = check.main(["--coverage-json", str(coverage_json), "--exceptions", str(tmp_path / "none.toml")])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "bookscraper.scraping.scrape_details" in captured.out
    assert "80.00%" in captured.out
    assert "below the per-module floor" in captured.err
