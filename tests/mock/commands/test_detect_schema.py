"""Mock-tier tests for bookscraper.commands.detect_schema.run().

The schema_detection pipeline is mocked out entirely; these tests only exercise the
command's wiring: printing the report to stdout, the optional --output write, and the
fail-loud exit paths. No browser, no network, no storage backend is ever touched.
load_dotenv is stubbed so the tests neither read the repo .env nor collide with a global
builtins.open patch.
"""

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
        "output": None,
        "model": None,
        "max_input_tokens": 40000,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_run_prints_report_and_usage_line(capsys) -> None:
    args = _args(model="claude-sonnet-5", max_input_tokens=12345)
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(
            detect_schema,
            "detect_schema",
            new=AsyncMock(return_value=("THE REPORT", "Anthropic usage: model=claude-sonnet-5 requests=1")),
        ) as detect,
    ):
        await detect_schema.run(args)

    detect.assert_awaited_once_with("amazon", ["https://x/1"], model="claude-sonnet-5", max_input_tokens=12345)
    out = capsys.readouterr().out
    assert "THE REPORT" in out
    assert "Anthropic usage: model=claude-sonnet-5 requests=1" in out


async def test_run_exits_one_on_detection_error() -> None:
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(detect_schema, "detect_schema", new=AsyncMock(side_effect=SchemaDetectionError("boom"))),
        pytest.raises(SystemExit) as exc_info,
    ):
        await detect_schema.run(_args())
    assert exc_info.value.code == 1


async def test_run_writes_report_to_output_file(tmp_path) -> None:
    out_path = tmp_path / "report.txt"
    args = _args(site="leanpub", output=str(out_path))
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(detect_schema, "detect_schema", new=AsyncMock(return_value=("REPORT BODY", "usage line"))),
    ):
        await detect_schema.run(args)

    assert out_path.read_text(encoding="utf-8").strip() == "REPORT BODY"


async def test_run_exits_one_when_output_write_fails(tmp_path) -> None:
    args = _args(site="leanpub", output=str(tmp_path / "report.txt"))
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(detect_schema, "detect_schema", new=AsyncMock(return_value=("REPORT BODY", "usage line"))),
        patch("builtins.open", side_effect=OSError("read-only fs")),
        pytest.raises(SystemExit) as exc_info,
    ):
        await detect_schema.run(args)
    assert exc_info.value.code == 1
