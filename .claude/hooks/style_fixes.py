#!/usr/bin/env python3
"""Python style-fix codemod - dual mode, extensible rule list.

Currently implements one rule: rewrite ``X | None`` type hints to
``Optional[X]`` (this project's convention is `Optional[T]` always, never
`T | None` - see `@docs/dev/python_coding_standard.md`). Add more rules by
appending to ``RULES`` below; each rule is an independent
``(name, check, fix)`` triple, so adding one is a small diff, not a rewrite.

Three modes:

1. **PostToolUse(Write|Edit|MultiEdit) hook** (no argv): auto-fixes the
   edited `.py` file in place, alongside `post_edit_format.py`.
2. **``--check <path> ...``**: reports rule violations without modifying
   anything, exit 1 if any found - used by the `implement-subtasks` loop's
   Step 5 gate.
3. **``<path> ...``** (no `--check`): applies all fixes to every `.py` file
   under the given path(s), like running the hook manually.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from _common import REPO_ROOT, allow, append_log, edited_path, read_event

_OPTIONAL_PIPE_RE = re.compile(r"\|\s*None\b")


def _scan_left_type(line: str, bar_pos: int) -> Optional[tuple[int, str]]:
    """Given the index of ``|`` in ``<type> | None``, return
    (start_index, type_text) for the type expression immediately before it,
    honoring bracket nesting (e.g. `dict[str, int] | None`)."""
    j = bar_pos
    while j > 0 and line[j - 1] in " \t":
        j -= 1
    end = j
    depth = 0
    k = j
    while k > 0:
        ch = line[k - 1]
        if ch in ")]}":
            depth += 1
        elif ch in "([{":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and ch in " \t,:=(":
            break
        k -= 1
    start = k
    type_text = line[start:end]
    if not type_text or not re.match(r"^[\w.\[\]'\", ]+$", type_text):
        return None
    return start, type_text


def _fix_line(line: str) -> tuple[str, int]:
    matches = list(_OPTIONAL_PIPE_RE.finditer(line))
    if not matches:
        return line, 0
    count = 0
    for m in reversed(matches):
        bar_pos = m.start()
        prefix = line[:bar_pos]
        if ":" not in prefix and "->" not in prefix:
            continue  # not an obvious annotation context - leave it alone
        found = _scan_left_type(line, bar_pos)
        if found is None:
            continue
        start, type_text = found
        line = line[:start] + f"Optional[{type_text}]" + line[m.end() :]
        count += 1
    return line, count


def _ensure_optional_import(text: str) -> str:
    if re.search(r"^\s*from typing import .*\bOptional\b", text, re.MULTILINE):
        return text

    def _extend(m: re.Match) -> str:
        names = m.group(1)
        return m.group(0) if "Optional" in names else f"from typing import {names.rstrip()}, Optional"

    new_text, n = re.subn(r"^from typing import (.+)$", _extend, text, count=1, flags=re.MULTILINE)
    if n:
        return new_text

    lines = text.splitlines(keepends=True)
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith(("import ", "from ")):
            insert_at = i + 1
        elif line.strip() and not line.startswith("#") and insert_at > 0:
            break
    lines.insert(insert_at, "from typing import Optional\n")
    return "".join(lines)


def _fix_optional_text(text: str) -> tuple[str, int]:
    total = 0
    new_lines = []
    for line in text.splitlines(keepends=True):
        if line.strip().startswith("#"):
            new_lines.append(line)
            continue
        fixed, n = _fix_line(line)
        new_lines.append(fixed)
        total += n
    new_text = "".join(new_lines)
    if total:
        new_text = _ensure_optional_import(new_text)
    return new_text, total


def _optional_check(text: str) -> int:
    return _fix_optional_text(text)[1]


def _optional_fix(text: str) -> str:
    return _fix_optional_text(text)[0]


# name -> (check: count violations, fix: return corrected text). Append more
# rules here as they're needed - each is independent of the others.
RULES: list[tuple[str, Callable[[str], int], Callable[[str], str]]] = [
    ("optional-not-pipe-none", _optional_check, _optional_fix),
]


def _apply_all(text: str) -> str:
    for _name, _check, fix in RULES:
        text = fix(text)
    return text


def _py_files(raw_paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in raw_paths:
        p = Path(raw)
        if not p.is_absolute():
            p = REPO_ROOT / p
        if p.is_dir():
            files.extend(sorted(p.rglob("*.py")))
        elif p.is_file():
            files.append(p)
    return files


def _hook_mode() -> None:
    event = read_event()
    target = edited_path(event)
    if target is None or target.suffix != ".py" or not target.exists():
        allow()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        allow()
    new_text = _apply_all(text)
    if new_text != text:
        target.write_text(new_text, encoding="utf-8")
        append_log("style-fixes.log", f"applied style fixes to {target}")
    allow()


def _check_mode(raw_paths: list[str]) -> None:
    problems: list[str] = []
    for f in _py_files(raw_paths or ["."]):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for name, check, _fix in RULES:
            n = check(text)
            if n:
                problems.append(f"{f}: {n} violation(s) of rule '{name}'")
    for p in problems:
        print(p)
    if problems:
        print(f"\nstyle_fixes --check: {len(problems)} file(s) with violations.")
        sys.exit(1)
    print("style_fixes --check: clean.")
    sys.exit(0)


def _fix_mode(raw_paths: list[str]) -> None:
    for f in _py_files(raw_paths):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        new_text = _apply_all(text)
        if new_text != text:
            f.write_text(new_text, encoding="utf-8")
            print(f"fixed {f}")


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "--check":
        _check_mode(argv[1:])
    elif argv:
        _fix_mode(argv)
    else:
        _hook_mode()


if __name__ == "__main__":
    main()
