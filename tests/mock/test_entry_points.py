"""Entry-point tests for the ``bookscraper`` CLI - the real process boundaries a user hits.

Two kinds of test live here:

* **Subprocess smoke tests** launch a real interpreter (``sys.executable -m bookscraper`` and the
  installed console script) and assert on exit code and ``--help`` text. They prove the packaging
  wiring (``[project.scripts]``, ``__main__.py``) actually launches and that argparse rejects a
  missing required flag with the conventional exit 2. They make **no network call** (``--help`` and
  an argparse usage error both short-circuit before any command body runs). A child process has its
  own ``coverage`` tracer that this suite does not start, so these tests deliberately do **not**
  contribute to the measured coverage of ``main.py`` / ``__main__.py`` - they guard behavior, not
  coverage.
* One **in-process ``runpy`` test** (``test_runpy_main_module_invokes_main_sync``) executes the
  package's ``__main__`` submodule *inside the test interpreter* under a patched ``sys.argv``, so the
  already-running coverage tracer records ``__main__.py``'s ``main_sync()`` call and ``main.py``'s
  ``migrate`` dispatch (line 38). This is the simpler of the two options the subtask spec lists: it
  needs no ``COVERAGE_PROCESS_START`` / ``sitecustomize`` plumbing to measure a child process. The
  command body (``migrate.run``) and ``configure_logging`` are patched out so the run touches no
  network and never writes to the real ``~/.bookscrapper`` tree.
"""

from __future__ import annotations

import runpy
import shutil
import subprocess
import sys
from unittest.mock import AsyncMock, patch

import pytest

from bookscraper import exitcodes, main

SUBCOMMANDS = ("scrape-urls", "search", "rotate-cert", "detect-schema", "migrate", "doctor")


def test_python_m_bookscraper_help_lists_subcommands():
    """
    [Local] entry point: verifies `python -m bookscraper --help` exits 0 and lists every subcommand.

    Scenario:
        Given a real child interpreter launched as `sys.executable -m bookscraper --help`
        When it runs to completion
        Then it exits 0 and its stdout names all six subcommands, proving __main__.py launches the
             parser and the help wiring is intact.

    Boundaries:
        - Real subprocess, no network (`--help` short-circuits before any command body).
        - Exit code and the presence of each subcommand name are asserted, not the full help layout.

    On failure, first check:
        - That `__main__.py` -> `main_sync` -> `build_parser` still runs and the subparser names match.
    """
    result = subprocess.run(
        [sys.executable, "-m", "bookscraper", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    for name in SUBCOMMANDS:
        assert name in result.stdout, f"{name!r} missing from --help output"


def test_console_script_help():
    """
    [Local] entry point: verifies the installed `bookscraper` console script runs and lists subcommands.

    Scenario:
        Given the `bookscraper` console script resolvable on PATH (skipped if the package is not
             installed in the active environment)
        When it is invoked as `bookscraper --help`
        Then it exits 0 and its stdout names every subcommand, proving the `[project.scripts]` entry
             point reaches the same parser as `python -m bookscraper`.

    Boundaries:
        - Real subprocess, no network; skips cleanly when the console script is absent.
        - Shares the subcommand-name assertions with the `-m` smoke test by design.

    On failure, first check:
        - The `[project.scripts] bookscraper = "bookscraper.main:main_sync"` wiring in pyproject.toml.
    """
    script = shutil.which("bookscraper")
    if script is None:
        pytest.skip("bookscraper console script is not installed in this environment")

    result = subprocess.run(
        [script, "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    for name in SUBCOMMANDS:
        assert name in result.stdout, f"{name!r} missing from console-script --help output"


def test_missing_required_flag_exits_two():
    """
    [Local] entry point: verifies `search` without `--store-backend` fails with argparse exit 2.

    Scenario:
        Given `python -m bookscraper search` invoked with the required `--store-backend` omitted
        When argparse validates the arguments
        Then the process exits 2 (the conventional argparse usage code) and its stderr names the
             missing `--store-backend` option, so a malformed invocation fails loudly before any
             scraping or storage work begins.

    Boundaries:
        - Real subprocess, no network (argparse errors out before the command body).
        - Asserts the exit code and that the message points at `--store-backend`, not the full usage text.

    On failure, first check:
        - That `--store-backend` is still `required=True` on the `search` subparser in cli.py.
    """
    result = subprocess.run(
        [sys.executable, "-m", "bookscraper", "search"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 2
    assert "--store-backend" in result.stderr


def test_runpy_main_module_invokes_main_sync():
    """
    [Local] entry point: verifies running the `__main__` module dispatches through main_sync to a command.

    Scenario:
        Given `sys.argv` set to a `migrate --store-backend json` invocation, with `migrate.run`
             stubbed to return exitcodes.OK and `configure_logging` stubbed out
        When the `bookscraper` package's `__main__` submodule is executed in-process via
             `runpy.run_module(..., run_name="__main__")` (so its `if __name__ == "__main__"` guard
             fires and `main_sync()` runs under the live coverage tracer)
        Then `main_sync` drives `main()`, which dispatches to the stubbed `migrate.run` exactly once,
             and the process-exit it raises carries exitcodes.OK.

    Why in-process runpy: the subtask spec offers either child-process coverage plumbing
    (COVERAGE_PROCESS_START / sitecustomize) or an in-process runpy run; runpy is chosen because the
    already-running tracer records `__main__.py` and `main.py`'s migrate branch with no extra config.

    Boundaries:
        - `migrate.run` and `configure_logging` are patched out: no network, no real log-file writes.
        - Exercises the `migrate` dispatch branch specifically (main.py line 38), the one `main()`
          branch no other test covers, so both entry-point modules reach 100%.

    On failure, first check:
        - `__main__.py`'s `main_sync()` call and `main.py`'s `migrate` dispatch branch.
    """
    argv = ["bookscraper", "migrate", "--store-backend", "json"]

    with (
        patch.object(sys, "argv", argv),
        patch.object(main, "configure_logging"),
        patch.object(main.migrate, "run", new_callable=AsyncMock, return_value=exitcodes.OK) as mock_migrate_run,
        pytest.raises(SystemExit) as exc_info,
    ):
        runpy.run_module("bookscraper", run_name="__main__")

    assert exc_info.value.code == exitcodes.OK
    mock_migrate_run.assert_awaited_once()
