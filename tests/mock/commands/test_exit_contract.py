"""Guard tests for the exit-code contract (task 05.0 subtask 05).

These assert the structural invariant that the command workflows no longer exit the process
themselves: every ``commands/*.run()`` returns an exitcodes value and lets ``main_sync`` perform the
single real ``sys.exit``. No external collaborators are involved - the test parses the source files.
"""

import ast
import pathlib

import bookscraper.commands as commands_pkg

COMMANDS_DIR = pathlib.Path(commands_pkg.__file__).parent


def _sys_exit_calls(source: str) -> list[int]:
    """Return the line numbers of any ``sys.exit(...)`` call in ``source``.

    :param source: Python source text to scan.
    :returns: Line numbers of each ``sys.exit`` call node found (empty if none).
    """
    tree = ast.parse(source)
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "exit"
            and isinstance(func.value, ast.Name)
            and func.value.id == "sys"
        ):
            hits.append(node.lineno)
    return hits


def _is_usage_exit_code(node: ast.expr) -> bool:
    """Return whether an exit-code expression is the sanctioned usage code (``2``).

    :param node: The single argument expression passed to ``SystemExit(...)``.
    :returns: ``True`` when it is the literal ``2`` or an ``exitcodes.USAGE`` reference, else
        ``False``.
    """
    if isinstance(node, ast.Constant) and node.value == 2:
        return True
    return isinstance(node, ast.Attribute) and node.attr == "USAGE"


def _bad_systemexit_raises(source: str) -> list[int]:
    """Return line numbers of ``raise SystemExit(...)`` sites not using the usage code.

    A bare ``raise SystemExit()`` (no argument) or one whose argument is neither the literal ``2``
    nor ``exitcodes.USAGE`` is an offender; the known-intentional usage-error sites all pass ``2``.

    :param source: Python source text to scan.
    :returns: Line numbers of each offending ``raise SystemExit`` node (empty if all are sanctioned).
    """
    tree = ast.parse(source)
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise):
            continue
        exc = node.exc
        if not (isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) and exc.func.id == "SystemExit"):
            continue
        if not exc.args or not _is_usage_exit_code(exc.args[0]):
            hits.append(node.lineno)
    return hits


def test_no_sys_exit_calls_in_commands_package():
    """
    [Local] exit contract: verifies no commands/*.py module exits with a non-usage code directly.

    Scenario:
        Given every Python source file under src/bookscraper/commands/
        When each is parsed and scanned for both a sys.exit(...) call and a raise SystemExit(...)
        Then no sys.exit(...) call exists at all, and every raise SystemExit(...) passes the usage
        code (literal 2 or exitcodes.USAGE) - so the deliberate USAGE=2 sites (search.py's
        _target_error) stay allowed while a smuggled-in raise SystemExit(1) would be caught, and
        commands otherwise return an exitcodes value for main_sync to turn into the one real exit.

    Boundaries:
        - Scans only the commands package; main_sync's single sys.exit is intentional and elsewhere.
        - A raise SystemExit is tolerated only for the argparse usage convention (code 2).

    On failure, first check:
        - A command's run() reintroduced sys.exit, or raised SystemExit with a non-usage code,
          instead of returning an exitcodes constant.
    """
    offenders = {}
    for path in sorted(COMMANDS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = _sys_exit_calls(source) + _bad_systemexit_raises(source)
        if lines:
            offenders[path.name] = sorted(lines)
    assert offenders == {}, f"non-usage process exit found in commands package: {offenders}"
