"""Guard against bare ``print()`` calls in ``src/bookscraper/``.

Console output must go through ``book_utils.print_log`` (console plus run log) or a module logger,
so nothing bypasses the per-run log file and concurrent scrapes stay readable. The only allowed
``print`` calls are ``detect-schema``'s intentional report/status output to stdout/stderr.
"""

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "bookscraper"

# Module path (relative to src/bookscraper) -> why it may call print().
ALLOWED_PRINT_MODULES = {
    "commands/detect_schema.py": "the report goes to stdout and status lines to stderr by design (--format json)",
}


def _bare_print_calls(path: Path) -> list[int]:
    """Return the line numbers of every ``print(...)`` call in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
    ]


def test_no_bare_print_outside_allow_list():
    """
    [Unit] no bare print: verifies no module under src/bookscraper calls print() outside the allow-list.

    Scenario:
        Given every .py file under src/bookscraper
        When each file is parsed and scanned for calls to the builtin name print
        Then only modules in ALLOWED_PRINT_MODULES contain such calls.

    Boundaries:
        - Static AST scan only; attribute calls such as sys.stdout.write are not print calls
        - book_utils.print_log writes via sys.stdout.write so it needs no allow-list entry

    On failure, first check:
        - The reported file:line; replace the call with print_log(...) or module_logger.<level>(...)
        - Whether a genuinely intentional stdout writer should be added to ALLOWED_PRINT_MODULES with a reason
    """
    offenders = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(SRC_ROOT).as_posix()
        if relative in ALLOWED_PRINT_MODULES:
            continue
        offenders.extend(f"{relative}:{lineno}" for lineno in _bare_print_calls(path))
    assert offenders == [], f"bare print() calls outside the allow-list: {offenders}"
