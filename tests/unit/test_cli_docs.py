"""Guard tests ensuring docs/cli.md documents every CLI flag exposed by build_parser().

A new flag added to cli.py without a matching entry in docs/cli.md will cause
test_every_cli_flag_documented_in_docs_cli_md to fail, making the documentation gap
visible in CI before it reaches a reviewer.
"""

from __future__ import annotations

import pathlib
from typing import Set

from bookscraper.cli import build_parser

#: Path to the CLI reference document, relative to the repository root.
_DOCS_CLI_MD = pathlib.Path(__file__).resolve().parents[2] / "docs" / "cli.md"

#: Argparse boilerplate flags that exist on every parser by default and do not
#: need per-subcommand documentation entries.
_ARGPARSE_BUILTINS: frozenset[str] = frozenset({"--help", "-h"})


def _collect_all_option_strings() -> Set[str]:
    """Collect every non-boilerplate option string from all parsers built by build_parser().

    Walks the top-level parser's own actions and then every registered subcommand
    parser's actions (including flags inherited from shared parent parsers), returning
    the union of all ``option_strings`` after removing argparse builtins.

    :returns: A set of every unique flag string (e.g. ``"--store-backend"``, ``"-f"``)
        that the CLI accepts across all subcommands.
    """
    parser = build_parser()
    result: Set[str] = set()

    # Top-level flags (e.g. -V/--version)
    for action in parser._actions:
        result.update(action.option_strings)

    # Subcommand flags (including those inherited from shared parent parsers)
    for action_group in parser._subparsers._group_actions:
        if not hasattr(action_group, "choices") or not action_group.choices:
            continue
        for subparser in action_group.choices.values():
            for sub_action in subparser._actions:
                result.update(sub_action.option_strings)

    result -= _ARGPARSE_BUILTINS
    return result


def test_every_cli_flag_documented_in_docs_cli_md():
    """[Unit] cli-docs: every option_string exposed by build_parser() appears in docs/cli.md.

    Scenario:
        Given the complete set of option strings from build_parser() (top-level and all
        subcommand parsers, including inherited flags from shared parents) and the text of
        docs/cli.md read from disk
        When each non-boilerplate option string is checked as a literal substring of the doc
        Then no option string is absent from the documentation, so a new flag added to cli.py
        without a docs entry causes this test to fail in CI.

    Boundaries:
        - The doc text is read at test time, not hardcoded, so adding flags to the doc
          without running the test does not cause false passes.
        - argparse builtins (--help/-h) are excluded: they appear on every subparser but
          do not need individual documentation entries.
        - The test checks literal substring presence only; it does not validate accuracy
          of descriptions or default values (those are the author's responsibility).

    On failure, first check:
        - Which flag string is missing from docs/cli.md (the assertion message names it).
        - Add a row for that flag to the appropriate subcommand's flag table in docs/cli.md.
    """
    assert _DOCS_CLI_MD.exists(), f"docs/cli.md not found at {_DOCS_CLI_MD}"
    doc_text = _DOCS_CLI_MD.read_text(encoding="utf-8")

    option_strings = _collect_all_option_strings()
    missing = sorted(flag for flag in option_strings if flag not in doc_text)

    assert not missing, (
        f"The following CLI flags are not documented in docs/cli.md: {missing}\n"
        "Add a row for each flag to the appropriate subcommand's flag table."
    )


def test_docs_cli_md_exists_and_is_non_empty():
    """[Unit] cli-docs: docs/cli.md exists and contains content.

    Scenario:
        Given the path docs/cli.md relative to the repository root
        When the file is read
        Then it exists and contains at least one non-empty line, confirming the reference
        document was not accidentally deleted or truncated.

    Boundaries:
        - Does not validate the document's content, only its presence and non-emptiness.
        - Complements test_every_cli_flag_documented_in_docs_cli_md, which checks content.

    On failure, first check:
        - Whether docs/cli.md was accidentally deleted or its path changed.
        - The _DOCS_CLI_MD constant in this test module.
    """
    assert _DOCS_CLI_MD.exists(), f"docs/cli.md not found at {_DOCS_CLI_MD}"
    content = _DOCS_CLI_MD.read_text(encoding="utf-8").strip()
    assert content, "docs/cli.md exists but is empty"
