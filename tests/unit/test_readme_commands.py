"""Guard: every bookscraper command in README.md fenced code blocks parses without error.

Extracts every line starting with ``bookscraper`` or ``uv run bookscraper`` from fenced blocks,
joins continuation lines (trailing backslash), skips shell comment lines (leading ``#``), and
asserts that each command's argument string parses via ``cli.build_parser().parse_args()``.

No command is executed; only argparse's resolution path is exercised.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from bookscraper.cli import build_parser

ROOT = Path(__file__).resolve().parents[2]
_README = ROOT / "README.md"


def _fenced_block_lines(text: str) -> list[str]:
    """Return every line that appears inside a fenced code block in *text*.

    Opening fence lines (e.g. ``\\`\\`\\`bash``) and closing fence lines (bare ``\\`\\`\\```)
    are excluded; only the body lines are returned.

    :param text: Full Markdown document text.
    :returns: List of raw lines found inside fenced blocks.
    """
    result: list[str] = []
    in_block = False
    for line in text.splitlines():
        if not in_block:
            if line.startswith("```"):
                in_block = True
        else:
            if line.strip() == "```":
                in_block = False
            else:
                result.append(line)
    return result


def _extract_bookscraper_commands(text: str) -> list[tuple[str, str]]:
    """Extract bookscraper argument strings from all fenced blocks in *text*.

    Handles:

    - Continuation lines ending with ``\\``: joined into one logical command.
    - Comment-only lines (stripped value starts with ``#``): skipped entirely.
    - Both ``bookscraper <args>`` and ``uv run bookscraper <args>`` prefixes.

    :param text: Full Markdown document text.
    :returns: List of ``(full_command, args_string)`` pairs where ``args_string`` has
        the ``bookscraper `` prefix stripped and is ready for
        ``build_parser().parse_args(shlex.split(args_string))``.
    """
    results: list[tuple[str, str]] = []
    pending = ""

    for line in _fenced_block_lines(text):
        stripped = line.strip()

        if stripped.endswith("\\"):
            # Accumulate continuation without the trailing backslash
            pending = (pending + " " + stripped[:-1].rstrip()).strip()
            continue

        full = (pending + " " + stripped).strip()
        pending = ""

        # Skip blank lines and shell comment lines
        if not full or full.startswith("#"):
            continue

        # Normalise: strip the 'uv run ' wrapper if present
        if full.startswith("uv run bookscraper "):
            args_str = full[len("uv run bookscraper") :]
        elif full.startswith("bookscraper "):
            args_str = full[len("bookscraper") :]
        else:
            continue

        results.append((full, args_str.strip()))

    return results


def test_every_readme_bookscraper_command_parses():
    """[Unit] readme-commands: every bookscraper command in README.md fenced blocks parses cleanly.

    Scenario:
        Given README.md at the repository root
        When every line starting with 'bookscraper ...' or 'uv run bookscraper ...' is extracted
        from fenced code blocks (continuation lines joined; '#'-comment lines skipped)
        Then each parses without error via cli.build_parser().parse_args(shlex.split(args_string))
        so a README example that diverges from the real CLI fails in CI.

    Boundaries:
        - No command is executed; only argparse argument resolution is exercised.
        - Lines starting with '#' inside fenced blocks are skipped as shell comments.
        - Trailing-backslash continuation lines are joined before parsing.
        - --help and --version cause sys.exit(0); that is treated as a passing parse.
        - Commands not starting with 'bookscraper' or 'uv run bookscraper' are ignored.

    On failure, first check:
        - Which command string is reported in the assertion message.
        - Update the README.md example to match the actual CLI flags in cli.py.
    """
    assert _README.exists(), f"README.md not found at {_README}"
    text = _README.read_text(encoding="utf-8")

    commands = _extract_bookscraper_commands(text)
    assert commands, "No bookscraper commands found in README.md fenced blocks — check extraction logic"

    failures: list[str] = []
    for full_cmd, args_str in commands:
        try:
            tokens = shlex.split(args_str)
        except ValueError as exc:
            failures.append(f"{full_cmd!r} — shlex split failed: {exc}")
            continue
        try:
            build_parser().parse_args(tokens)
        except SystemExit as exc:
            # exit(0) means --help or --version: argparse ran cleanly
            if exc.code != 0:
                failures.append(f"{full_cmd!r} — argparse exited with code {exc.code}")

    assert not failures, "README.md commands that do not parse:\n" + "\n".join(f"  {f}" for f in failures)
