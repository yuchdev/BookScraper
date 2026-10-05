"""Mock-tier tests for bookscraper.commands.detect_schema.run().

The schema_detection pipeline is mocked out entirely; these tests only exercise the
command's wiring: printing the report to stdout, the optional --output write, and the
fail-loud exit paths. No browser, no network, no storage backend is ever touched.
load_dotenv is stubbed so the tests neither read the repo .env nor collide with a global
builtins.open patch.
"""

import json as _json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bookscraper.commands import detect_schema
from bookscraper.scraping.schema_detection import SchemaDetectionError

MODULE = "bookscraper.commands.detect_schema"


def _args(**overrides) -> SimpleNamespace:
    """A detect-schema Namespace with the subtask-05 defaults, overridable per test."""
    base = {
        "site": "amazon",
        "url": ["https://x/1"],
        "html_file": None,
        "output": None,
        "model": None,
        "max_input_tokens": 40000,
        "page_type": "detail",
        "replay": None,
        "record": None,
        "format": "text",
        "check_current_only": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_run_prints_report_and_usage_line(capsys) -> None:
    """
    [Local] detect-schema command: verifies run() forwards every CLI option to the engine and prints the report plus the usage line.

    Scenario:
        Given parsed args with a model and a max-input-tokens override, and detect_schema() mocked to return a report and a usage line
        When detect_schema.run(args) is awaited
        Then the engine is called once with the site, URLs, model, budget and default options, and both the report and the usage line reach stdout.

    Boundaries:
        - Engine is mocked: no Playwright, no Anthropic call
        - load_dotenv is patched, so no .env is read

    On failure, first check:
        - Argument mapping between cli.py dests and the detect_schema() keyword arguments
        - Whether run() still prints the usage line after the report
    """
    args = _args(model="claude-sonnet-5", max_input_tokens=12345)
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(
            detect_schema,
            "detect_schema",
            new=AsyncMock(return_value=("THE REPORT", "Anthropic usage: model=claude-sonnet-5 requests=1", 0)),
        ) as detect,
    ):
        await detect_schema.run(args)

    detect.assert_awaited_once_with(
        "amazon",
        urls=["https://x/1"],
        html_files=None,
        model="claude-sonnet-5",
        max_input_tokens=12345,
        page_type="detail",
        replay=None,
        record=None,
        report_format="text",
        check_current_only=False,
    )
    out = capsys.readouterr().out
    assert "THE REPORT" in out
    assert "Anthropic usage: model=claude-sonnet-5 requests=1" in out


async def test_run_threads_search_page_type(capsys) -> None:
    """
    [Local] detect schema: verifies run threads search page type.
    
    Scenario:
        --page-type search is forwarded verbatim to detect_schema.
    
    Boundaries:
        - detect_schema is mocked; only the wiring of page_type is asserted.
    
    On failure, first check:
        - a search run would silently fall back to detail-page selectors.
    """
    args = _args(page_type="search")
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(
            detect_schema,
            "detect_schema",
            new=AsyncMock(return_value=("SEARCH REPORT", "usage line", 0)),
        ) as detect,
    ):
        await detect_schema.run(args)

    detect.assert_awaited_once_with(
        "amazon",
        urls=["https://x/1"],
        html_files=None,
        model=None,
        max_input_tokens=40000,
        page_type="search",
        replay=None,
        record=None,
        report_format="text",
        check_current_only=False,
    )
    assert "SEARCH REPORT" in capsys.readouterr().out


async def test_run_threads_offline_html_file_replay_record(capsys) -> None:
    """
    [Local] detect schema: verifies run threads offline html file replay record.
    
    Scenario:
        --html-file / --replay / --record are forwarded verbatim to detect_schema.
    
    Boundaries:
        - detect_schema is mocked; only the offline-mode wiring is asserted.
    
    On failure, first check:
        - an offline run could silently fetch live URLs or require an API key.
    """
    args = _args(url=None, html_file=["a.html", "b.html"], replay="r.json", record="rec.json")
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(
            detect_schema,
            "detect_schema",
            new=AsyncMock(return_value=("OFFLINE REPORT", "usage line", 0)),
        ) as detect,
    ):
        await detect_schema.run(args)

    detect.assert_awaited_once_with(
        "amazon",
        urls=None,
        html_files=["a.html", "b.html"],
        model=None,
        max_input_tokens=40000,
        page_type="detail",
        replay="r.json",
        record="rec.json",
        report_format="text",
        check_current_only=False,
    )
    assert "OFFLINE REPORT" in capsys.readouterr().out


async def test_run_exits_one_on_detection_error() -> None:
    """
    [Local] detect-schema command: verifies a SchemaDetectionError ends the run with exit code 1.

    Scenario:
        Given detect_schema() mocked to raise SchemaDetectionError
        When detect_schema.run() is awaited with default args
        Then SystemExit is raised with code 1 (fail loudly, never partial output).

    Boundaries:
        - Only the error-to-exit-code mapping; the error message text is not asserted

    On failure, first check:
        - The except clause around detect_schema() in commands/detect_schema.py
        - Whether the exit code contract (0/4/1) changed
    """
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(detect_schema, "detect_schema", new=AsyncMock(side_effect=SchemaDetectionError("boom"))),
        pytest.raises(SystemExit) as exc_info,
    ):
        await detect_schema.run(_args())
    assert exc_info.value.code == 1


async def test_run_writes_report_to_output_file(tmp_path) -> None:
    """
    [Local] detect-schema command: verifies --output writes the report body to the given file.

    Scenario:
        Given a tmp_path output file and detect_schema() mocked to return 'REPORT BODY'
        When detect_schema.run() is awaited with --site leanpub and --output
        Then the file contains exactly the report body.

    Boundaries:
        - Real file I/O under tmp_path only
        - The usage line is not part of the written file

    On failure, first check:
        - How run() opens and writes --output (encoding, what is written)
    """
    out_path = tmp_path / "report.txt"
    args = _args(site="leanpub", output=str(out_path))
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(detect_schema, "detect_schema", new=AsyncMock(return_value=("REPORT BODY", "usage line", 0))),
    ):
        await detect_schema.run(args)

    assert out_path.read_text(encoding="utf-8").strip() == "REPORT BODY"


async def test_run_exits_one_when_output_write_fails(tmp_path) -> None:
    """
    [Local] detect-schema command: verifies a failure writing --output exits with code 1.

    Scenario:
        Given detect_schema() mocked to succeed and builtins.open patched to raise OSError
        When detect_schema.run() is awaited with --output set
        Then SystemExit is raised with code 1 instead of an unhandled OSError.

    Boundaries:
        - open() is patched globally for the duration of the call

    On failure, first check:
        - The OSError handling around the --output write in commands/detect_schema.py
    """
    args = _args(site="leanpub", output=str(tmp_path / "report.txt"))
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(detect_schema, "detect_schema", new=AsyncMock(return_value=("REPORT BODY", "usage line", 0))),
        patch("builtins.open", side_effect=OSError("read-only fs")),
        pytest.raises(SystemExit) as exc_info,
    ):
        await detect_schema.run(args)
    assert exc_info.value.code == 1


async def test_run_threads_format_and_check_current_only(capsys) -> None:
    """
    [Local] detect schema: verifies run threads format and check current only.
    
    Scenario:
        --format and --check-current-only are forwarded verbatim to detect_schema.
    
    Boundaries:
        - detect_schema is mocked; only the new-flag wiring is asserted.
    
    On failure, first check:
        - a JSON/drift-check run would silently fall back to a text AI run.
    """
    args = _args(format="json", check_current_only=True)
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(
            detect_schema,
            "detect_schema",
            new=AsyncMock(return_value=('{"site": "amazon"}', "usage", 0)),
        ) as detect,
    ):
        await detect_schema.run(args)

    detect.assert_awaited_once_with(
        "amazon",
        urls=["https://x/1"],
        html_files=None,
        model=None,
        max_input_tokens=40000,
        page_type="detail",
        replay=None,
        record=None,
        report_format="json",
        check_current_only=True,
    )


async def test_run_json_format_stdout_is_pure_json(capsys) -> None:
    """
    [Local] detect schema: verifies run json format stdout is pure json.
    
    Scenario:
        --format json keeps stdout a single parseable JSON document (status to stderr).
    
    Boundaries:
        - detect_schema is mocked to return a JSON body; capsys captures both streams.
    
    On failure, first check:
        - human status lines in stdout would break a CI drift job's json.loads.
    """

    body = '{"site": "amazon", "summary": {"current_broken": 0}}'
    args = _args(format="json")
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(detect_schema, "detect_schema", new=AsyncMock(return_value=(body, "USAGE LINE", 0))),
    ):
        await detect_schema.run(args)

    captured = capsys.readouterr()
    assert _json.loads(captured.out) == {"site": "amazon", "summary": {"current_broken": 0}}
    # The usage line and completion notice went to stderr, not polluting the JSON on stdout.
    assert "USAGE LINE" not in captured.out
    assert "USAGE LINE" in captured.err


async def test_run_exits_four_on_drift() -> None:
    """
    [Local] detect schema: verifies run exits four on drift.
    
    Scenario:
        detect_schema reporting drift (exit 4) propagates as SystemExit(4).
    
    Boundaries:
        - detect_schema is mocked to return exit_code 4; text format.
    
    On failure, first check:
        - a scheduled drift job could not gate on the exit status.
    """
    args = _args()
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(detect_schema, "detect_schema", new=AsyncMock(return_value=("REPORT", "usage", 4))),
        pytest.raises(SystemExit) as exc_info,
    ):
        await detect_schema.run(args)
    assert exc_info.value.code == 4


async def test_run_exits_four_on_drift_json_format(capsys) -> None:
    """
    [Local] detect schema: verifies run exits four on drift json format.
    
    Scenario:
        drift (exit 4) propagates in JSON mode with the drift notice on stderr.
    
    Boundaries:
        - detect_schema mocked to return exit 4 with a JSON body; format json.
    
    On failure, first check:
        - a JSON drift artifact would be corrupted by the drift notice, or exit 4 lost.
    """

    args = _args(format="json")
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(detect_schema, "detect_schema", new=AsyncMock(return_value=('{"ok": true}', "usage", 4))),
        pytest.raises(SystemExit) as exc_info,
    ):
        await detect_schema.run(args)
    assert exc_info.value.code == 4
    captured = capsys.readouterr()
    assert _json.loads(captured.out) == {"ok": True}
    assert "drift detected" in captured.err.lower()
