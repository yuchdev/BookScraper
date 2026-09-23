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


async def test_run_prints_report_to_stdout(capsys) -> None:
    args = SimpleNamespace(site="amazon", url=["https://x/1"], output=None)
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(detect_schema, "detect_schema", new=AsyncMock(return_value="THE REPORT")) as detect,
    ):
        await detect_schema.run(args)

    detect.assert_awaited_once_with("amazon", ["https://x/1"])
    out = capsys.readouterr().out
    assert "THE REPORT" in out


async def test_run_exits_one_on_detection_error() -> None:
    args = SimpleNamespace(site="amazon", url=["https://x/1"], output=None)
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(detect_schema, "detect_schema", new=AsyncMock(side_effect=SchemaDetectionError("boom"))),
        pytest.raises(SystemExit) as exc_info,
    ):
        await detect_schema.run(args)
    assert exc_info.value.code == 1


async def test_run_writes_report_to_output_file(tmp_path) -> None:
    out_path = tmp_path / "report.txt"
    args = SimpleNamespace(site="leanpub", url=["https://x/1"], output=str(out_path))
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(detect_schema, "detect_schema", new=AsyncMock(return_value="REPORT BODY")),
    ):
        await detect_schema.run(args)

    assert out_path.read_text(encoding="utf-8").strip() == "REPORT BODY"


async def test_run_exits_one_when_output_write_fails(tmp_path) -> None:
    args = SimpleNamespace(site="leanpub", url=["https://x/1"], output=str(tmp_path / "report.txt"))
    with (
        patch.object(detect_schema, "load_dotenv", MagicMock()),
        patch.object(detect_schema, "detect_schema", new=AsyncMock(return_value="REPORT BODY")),
        patch("builtins.open", side_effect=OSError("read-only fs")),
        pytest.raises(SystemExit) as exc_info,
    ):
        await detect_schema.run(args)
    assert exc_info.value.code == 1
