"""Insert standardized documentation docstrings into pytest test functions.

For every ``test_*`` function/method under a target directory, generates a
docstring that classifies the test as one of ``Unit``, ``Local``,
``Integration``, or ``E2E`` and fills a fixed narrative template (Scenario /
Boundaries / On-failure-first-check) derived mechanically from the test's
name, enclosing class, parameters, and body - no test logic is read for
"meaning", only scanned for structural signals (mocking, CLI/HTTP runners,
directory placement).

Classification heuristic (path-first, body-refined):

- path contains ``e2e`` (dir or filename) -> ``E2E``
- else path under ``.../integration/``:
    - body uses ``CliRunner``/``TestClient``/``playwright`` -> ``E2E``
    - else -> ``Integration``
- else path under ``.../unit/`` (or unrecognised dir):
    - body uses ``mock.patch``/``MagicMock``/``Mock(``/``monkeypatch``/``mocker.`` -> ``Local``
(the label flakeforge's X005 accepts for this project's ``tests/mock/`` tier; older
generated ``[Mock]`` docstrings are still recognised and re-generated as ``[Local]``)
    - else -> ``Unit``
    - tests in an unrecognised directory are additionally flagged ``ambiguous``
      for human/agent review, since no directory convention backs the guess.

A generated docstring is recognisable by its first content line matching
``r"^\\[(Unit|Local|Mock|Integration|E2E)\\] .+: verifies .+\\.$"``. Re-running this
script re-generates (and overwrites) only docstrings matching that pattern,
so it is safe to run repeatedly as tests change. A pre-existing hand-written
docstring that does NOT match the pattern is left alone and reported as
skipped, unless ``--force`` is passed.

Usage::

    python scripts/document_tests.py                  # apply to tests/
    python scripts/document_tests.py tests/unit        # scope to a directory
    python scripts/document_tests.py --check           # report only, no writes
    python scripts/document_tests.py --force           # overwrite hand-written docstrings too

Exit status is ``1`` in ``--check`` mode when any test would change, else ``0``.
"""

from __future__ import annotations

import argparse
import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

TestFunc = Union[ast.FunctionDef, ast.AsyncFunctionDef]

REPO_ROOT = Path(__file__).resolve().parent.parent


def _display_path(file_path: Path) -> str:
    try:
        return str(file_path.relative_to(REPO_ROOT))
    except ValueError:
        return str(file_path)


DEFAULT_TARGET = "tests"

EXCLUDED_FILENAMES = {"__init__.py", "conftest.py"}

CLASSIFICATIONS = ("Unit", "Local", "Integration", "E2E")

_GENERATED_TITLE_RE = re.compile(r"^\[(Unit|Local|Mock|Integration|E2E)\] .+: verifies .+\.$")

_E2E_BODY_MARKERS = ("clirunner", "testclient(", "playwright", "async_playwright")
_MOCK_BODY_MARKERS = ("mock.patch", "@patch", "magicmock", "mock(", "monkeypatch", "mocker.")

_INDENT_UNIT = "    "


@dataclass
class TestCase:
    """One discovered ``test_*`` function/method and its rendered docstring."""

    file_path: Path
    qualname: str
    test_name: str
    classification: str
    ambiguous: bool
    insert_line: int  # 1-indexed line to insert before (post any deletion)
    delete_start: Optional[int]  # 1-indexed inclusive start of existing generated docstring
    delete_end: Optional[int]  # 1-indexed inclusive end of existing generated docstring
    skip_custom_docstring: bool
    indent: str
    rendered: str = field(default="")


def _humanize(identifier: str) -> str:
    return identifier.replace("_", " ").strip()


def _humanize_class(class_name: str) -> str:
    name = re.sub(r"^Test", "", class_name)
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", name)
    return " ".join(w.lower() for w in words) if words else class_name.lower()


def _humanize_filename(file_path: Path) -> str:
    stem = file_path.stem
    stem = re.sub(r"^test_", "", stem)
    stem = re.sub(r"_test$", "", stem)
    return _humanize(stem)


def _context_label(file_path: Path, class_name: Optional[str]) -> str:
    if class_name:
        return _humanize_class(class_name)
    return _humanize_filename(file_path)


def _fixtures_line(node: TestFunc) -> str:
    names = [a.arg for a in node.args.args if a.arg not in ("self", "cls")]
    names += [a.arg for a in node.args.kwonlyargs]
    line = ", ".join(names) if names else "none"

    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        attr = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if attr != "parametrize":
            continue
        cases_arg = decorator.args[1] if len(decorator.args) > 1 else None
        if isinstance(cases_arg, (ast.List, ast.Tuple)):
            line += f" (parametrized, {len(cases_arg.elts)} cases)"
        else:
            line += " (parametrized)"
    return line


def _source_segment(source_lines: list[str], node: TestFunc) -> str:
    start = node.lineno - 1
    end = node.end_lineno or node.lineno
    return "\n".join(source_lines[start:end]).lower()


def _classify(file_path: Path, body_text: str) -> tuple[str, bool]:
    rel_parts = {p.lower() for p in file_path.parts}
    stem = file_path.stem.lower()

    if "e2e" in rel_parts or "e2e" in stem:
        return "E2E", False

    if "integration" in rel_parts:
        if any(marker in body_text for marker in _E2E_BODY_MARKERS):
            return "E2E", False
        return "Integration", False

    if "mock" in rel_parts:
        # This project's tests/mock/ tier: every external dependency is replaced by a local
        # double (including the offline real-Chromium corpus harness), so the tier is the label.
        return "Local", False

    if "unit" in rel_parts:
        if any(marker in body_text for marker in _MOCK_BODY_MARKERS):
            return "Local", False
        return "Unit", False

    # Unrecognised directory layout: best-effort cascade, flagged for review.
    if any(marker in body_text for marker in _E2E_BODY_MARKERS):
        return "E2E", True
    if any(marker in body_text for marker in _MOCK_BODY_MARKERS):
        return "Local", True
    return "Unit", True


def _existing_docstring_range(node: TestFunc) -> tuple[Optional[int], Optional[int], Optional[str]]:
    if not node.body:
        return None, None, None
    first = node.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        return first.lineno, first.end_lineno, first.value.value
    return None, None, None


def _render_docstring(indent: str, classification: str, context_label: str, test_name: str) -> str:
    subject = _humanize(re.sub(r"^test_", "", test_name))
    inner = indent + _INDENT_UNIT

    lines = [
        f'{indent}"""',
        f"{indent}[{classification}] {context_label}: verifies {subject}.",
        "",
        f"{indent}Scenario:",
        f"{inner}Given test inputs and fixtures for {subject}",
        f"{inner}When {test_name} executes the target flow",
        f"{inner}Then the expected outcome for {subject} is confirmed.",
        "",
        f"{indent}Boundaries:",
        f"{inner}- Focus: {subject}",
        f"{inner}- Fixtures/params: {{fixtures}}",
        f"{inner}- Scope: assertions and setup in this test case only",
        "",
        f"{indent}On failure, first check:",
        f"{inner}- Assertion details tied to {subject}",
        f"{inner}- Fixture or parameter values used by this test",
        f"{inner}- Recent changes in code paths exercised by {context_label}",
        f'{indent}"""',
    ]
    return "\n".join(lines)


def _collect_file(file_path: Path, tree: ast.Module, source_lines: list[str]) -> list[TestCase]:
    cases: list[TestCase] = []

    def visit_function(node: ast.AST, class_name: Optional[str]) -> None:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return
        if not node.name.startswith("test_"):
            return

        body_text = _source_segment(source_lines, node)
        classification, ambiguous = _classify(file_path, body_text)
        context_label = _context_label(file_path, class_name)
        del_start, del_end, existing_text = _existing_docstring_range(node)

        skip_custom = False
        if existing_text is not None:
            first_line = existing_text.strip().splitlines()[0] if existing_text.strip() else ""
            if not _GENERATED_TITLE_RE.match(first_line):
                skip_custom = True

        first_stmt = node.body[0]
        indent = " " * first_stmt.col_offset

        qualname = f"{class_name}.{node.name}" if class_name else node.name
        cases.append(
            TestCase(
                file_path=file_path,
                qualname=qualname,
                test_name=node.name,
                classification=classification,
                ambiguous=ambiguous,
                insert_line=first_stmt.lineno,
                delete_start=del_start,
                delete_end=del_end,
                skip_custom_docstring=skip_custom,
                indent=indent,
            )
        )
        rendered = _render_docstring(indent, classification, context_label, node.name)
        fixtures = _fixtures_line(node)
        cases[-1].rendered = rendered.replace("{fixtures}", fixtures)

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for child in node.body:
                visit_function(child, node.name)
        else:
            visit_function(node, None)

    return cases


def _apply_edits(source_lines: list[str], cases: list[TestCase], force: bool) -> tuple[list[str], list[TestCase]]:
    lines = list(source_lines)
    applied: list[TestCase] = []
    # Apply bottom-to-top so earlier line numbers stay valid as we edit.
    for case in sorted(cases, key=lambda c: c.insert_line, reverse=True):
        if case.skip_custom_docstring and not force:
            continue
        rendered_lines = case.rendered.split("\n")
        if case.delete_start is not None and case.delete_end is not None:
            start, end = case.delete_start - 1, case.delete_end
            lines[start:end] = rendered_lines
        else:
            idx = case.insert_line - 1
            lines[idx:idx] = rendered_lines
        applied.append(case)
    return lines, applied


def _discover_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target] if target.name not in EXCLUDED_FILENAMES else []
    files: list[Path] = []
    for pattern in ("test_*.py", "*_test.py"):
        files.extend(target.rglob(pattern))
    return sorted(f for f in set(files) if f.name not in EXCLUDED_FILENAMES)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default=DEFAULT_TARGET, help="Test file or directory")
    parser.add_argument("--check", action="store_true", help="Report only; exit 1 if changes are pending")
    parser.add_argument("--force", action="store_true", help="Overwrite hand-written docstrings too")
    args = parser.parse_args()

    target = (REPO_ROOT / args.path).resolve() if not Path(args.path).is_absolute() else Path(args.path)
    files = _discover_files(target)

    counts = dict.fromkeys(CLASSIFICATIONS, 0)
    documented = 0
    skipped_custom: list[str] = []
    ambiguous: list[str] = []
    changed_files = 0

    for file_path in files:
        source = file_path.read_text(encoding="utf-8")
        source_lines = source.split("\n")
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError as exc:
            print(f"SKIP (syntax error): {_display_path(file_path)}: {exc}")
            continue

        cases = _collect_file(file_path, tree, source_lines)
        if not cases:
            continue

        for case in cases:
            counts[case.classification] += 1
            rel = f"{_display_path(file_path)}::{case.qualname}"
            if case.ambiguous:
                ambiguous.append(rel)
            if case.skip_custom_docstring:
                skipped_custom.append(rel)

        new_lines, applied = _apply_edits(source_lines, cases, args.force)
        if not applied:
            continue

        changed_files += 1
        documented += len(applied)
        if not args.check:
            file_path.write_text("\n".join(new_lines), encoding="utf-8")

    print(f"Scanned {len(files)} file(s), {sum(counts.values())} test function(s).")
    print("  " + "  ".join(f"[{c}] {n}" for c, n in counts.items()))
    verb = "would document" if args.check else "documented"
    print(f"{verb.capitalize()} {documented} test(s) across {changed_files} file(s).")
    if skipped_custom:
        print(f"Skipped (custom docstring present, use --force to overwrite): {len(skipped_custom)}")
        for rel in skipped_custom:
            print(f"  - {rel}")
    if ambiguous:
        print(f"Ambiguous classification (needs review, no directory convention matched): {len(ambiguous)}")
        for rel in ambiguous:
            print(f"  - {rel}")

    if args.check and documented:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
