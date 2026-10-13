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


def test_no_sys_exit_calls_in_commands_package():
    """
    [Local] exit contract: verifies no commands/*.py module calls sys.exit directly.

    Scenario:
        Given every Python source file under src/bookscraper/commands/
        When each is parsed and scanned for a sys.exit(...) call
        Then none is found - the commands return an exitcodes value and main_sync owns the one real
        process exit.

    Boundaries:
        - Scans only the commands package; main_sync's single sys.exit is intentional and elsewhere.

    On failure, first check:
        - A command's run() reintroduced sys.exit instead of returning an exitcodes constant.
    """
    offenders = {}
    for path in sorted(COMMANDS_DIR.glob("*.py")):
        lines = _sys_exit_calls(path.read_text(encoding="utf-8"))
        if lines:
            offenders[path.name] = lines
    assert offenders == {}, f"sys.exit found in commands package: {offenders}"
