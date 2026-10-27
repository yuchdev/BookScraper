"""Unit guard that every bookscraper module names its logger via ``logging.getLogger(__name__)``.

Task 07.0/02 replaced the ad hoc literal logger names (``"scrape_details"``, ``"bookscraper_app"``,
``"deduplicate"``, ...) with ``logging.getLogger(__name__)`` so the whole package forms one
``bookscraper.*`` hierarchy whose level ``configure_logging`` can set in one place. This test parses
the source with ``ast`` (no import side effects) and fails if any module-level logger assignment is
built from a literal name again, which would silently escape the hierarchy's --log-severity control.

Only module-level logger *definitions* are checked. In-function ``logging.getLogger("bookscraper")``
/ ``logging.getLogger("pymongo")`` / ``logging.getLogger()`` calls inside ``configure_logging`` are
intentionally not definitions and are left alone.
"""

import ast
from pathlib import Path

import pytest

_SRC_ROOT = Path(__file__).resolve().parents[1].parent / "src" / "bookscraper"


def _python_modules() -> list[Path]:
    """Return every ``.py`` file under ``src/bookscraper`` as a sorted list of paths."""
    return sorted(_SRC_ROOT.rglob("*.py"))


def _is_get_logger_call(node: ast.AST) -> bool:
    """Return ``True`` if *node* is a call to ``logging.getLogger`` or a bare ``getLogger``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == "getLogger"
    if isinstance(func, ast.Name):
        return func.id == "getLogger"
    return False


def _module_level_logger_violations(tree: ast.Module) -> list[str]:
    """Collect module-level ``X = logging.getLogger(...)`` assignments not passing ``__name__``.

    :param tree: The parsed module AST.
    :returns: A list of human-readable descriptions of each offending assignment target.
    """
    violations: list[str] = []
    for stmt in tree.body:
        targets = stmt.targets if isinstance(stmt, ast.Assign) else []
        value = getattr(stmt, "value", None)
        if not targets or not _is_get_logger_call(value):
            continue
        args = value.args
        ok = len(args) == 1 and isinstance(args[0], ast.Name) and args[0].id == "__name__"
        if not ok:
            names = [getattr(t, "id", "<target>") for t in targets]
            violations.append(", ".join(names))
    return violations


def test_all_modules_use_dunder_name_logger():
    """
    [Unit] Every module-level logger in src/bookscraper is ``logging.getLogger(__name__)``.

    Scenario:
        Given every ``.py`` file under src/bookscraper parsed to an AST
        When each module-level logger assignment is inspected
        Then each getLogger definition is called with the single argument ``__name__``.

    Boundaries:
        - Pure static analysis; no module is imported and no logging state is touched
        - Only module-level definitions are checked, not in-function getLogger() calls

    On failure, first check:
        - The named module reverted to a literal logger name instead of ``__name__``
    """
    offenders: dict[str, list[str]] = {}
    for path in _python_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        bad = _module_level_logger_violations(tree)
        if bad:
            offenders[str(path.relative_to(_SRC_ROOT))] = bad

    assert not offenders, f"Modules with non-__name__ loggers: {offenders}"


def test_guard_sees_src_modules():
    """
    [Unit] Sanity check that the guard actually scanned the package (no empty-pass false green).

    Scenario:
        Given the module collector over src/bookscraper
        When it is invoked
        Then it returns a non-trivial number of modules, including book_utils.py.

    Boundaries:
        - Protects against a path typo turning test_all_modules into a vacuous pass

    On failure, first check:
        - _SRC_ROOT resolves to the installed src/bookscraper package directory
    """
    modules = _python_modules()
    assert len(modules) > 10
    assert any(p.name == "book_utils.py" for p in modules)


@pytest.mark.parametrize("snippet", ['logging.getLogger("scrape_details")', "logging.getLogger('x')"])
def test_guard_rejects_literal_names(snippet):
    """
    [Unit] The violation detector flags a literal-name module-level logger assignment.

    Scenario:
        Given a one-line module assigning a logger from a literal name
        When the violation detector runs on its AST
        Then it reports the assignment as a violation.

    Boundaries:
        - Exercises the detector directly so the guard cannot silently stop detecting

    On failure, first check:
        - _module_level_logger_violations' ``__name__`` argument check
    """
    tree = ast.parse(f"import logging\nlogger = {snippet}\n")
    assert _module_level_logger_violations(tree) == ["logger"]
