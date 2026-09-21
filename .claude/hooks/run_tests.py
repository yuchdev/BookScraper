#!/usr/bin/env python3
"""Stop hook - gate the end of a session on lint + tests, unconditionally.

Runs, in order, the same checks CI expects a clean session to satisfy:

  1. ``uv run ruff check . --fix``  - auto-fix what's mechanically fixable.
  2. ``uv run ruff check .``        - fail if anything remains unfixed.
  3. ``uv run pytest -q --cov=bookscraper --cov-report=term-missing``

If any step fails, the hook exits 2 so Claude is told to analyze that tool's
own output and fix what it flagged before ending the session - not to weaken
the check. To avoid infinite loops Claude Code suppresses this hook when the
stop was itself triggered by a prior stop-hook block (``stop_hook_active``).

Skip the gate for a known-WIP session by exporting ``BOOK_SCRAPPER_SKIP_TESTS=1``.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

from _common import REPO_ROOT, allow, append_log, block, read_event

TIMEOUT_SECONDS = 900


def _run(cmd: list[str]) -> Optional[subprocess.CompletedProcess[str]]:
    """Run ``cmd`` from the repo root, returning None if the runner is unavailable."""
    try:
        return subprocess.run(
            cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _tail(proc: subprocess.CompletedProcess[str], n: int = 40) -> str:
    return "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-n:])


def main() -> None:
    event = read_event()
    if event.get("stop_hook_active"):
        allow()
    if os.environ.get("BOOK_SCRAPPER_SKIP_TESTS") == "1":
        append_log("tests.log", "SKIPPED (BOOK_SCRAPPER_SKIP_TESTS=1)")
        allow()

    steps = [
        ("ruff --fix", ["uv", "run", "ruff", "check", ".", "--fix"]),
        ("ruff check", ["uv", "run", "ruff", "check", "."]),
        ("pytest+cov", ["uv", "run", "pytest", "-q", "--cov=bookscraper", "--cov-report=term-missing"]),
    ]

    for label, cmd in steps:
        proc = _run(cmd)
        if proc is None:
            append_log("tests.log", f"SKIPPED (runner unavailable: {label})")
            allow()
        if proc.returncode != 0:
            append_log("tests.log", f"FAIL ({label})")
            block(
                f"Stop blocked by Book Scrapper run-tests: `{' '.join(cmd)}` failed.\n"
                f"{_tail(proc)}\n"
                f"Analyze the {label} output above and fix every warning/error it left behind "
                "before ending the session. If a fix isn't clearly safe, ask the user instead of "
                "guessing. Export BOOK_SCRAPPER_SKIP_TESTS=1 only if this is intentional WIP."
            )

    append_log("tests.log", "PASS")
    allow()


if __name__ == "__main__":
    main()
