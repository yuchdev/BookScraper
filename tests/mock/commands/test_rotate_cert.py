"""Mock-tier tests for bookscraper.commands.rotate_cert.

cert_rotation.rotate_certificate, load_dotenv, and print_log are mocked, so no
network, .env, or real ~/.bookscrapper is touched.
"""

import pathlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bookscraper.backends.mongo.cert_rotation import RotationError
from bookscraper.commands import rotate_cert

MODULE = "bookscraper.commands.rotate_cert"


def _args(months=None, output=None) -> SimpleNamespace:
    return SimpleNamespace(command="rotate-cert", months=months, output=output, log_severity="info")


@pytest.mark.asyncio
async def test_run_success_passes_args_through_and_reports_notes(tmp_path) -> None:
    cert = tmp_path / "X509-cert-new.pem"

    with (
        patch(f"{MODULE}.load_dotenv") as mock_dotenv,
        patch(f"{MODULE}.cert_rotation.rotate_certificate", return_value=(cert, ["note one"])) as mock_rotate,
        patch(f"{MODULE}.print_log") as mock_print,
    ):
        await rotate_cert.run(_args(months=9, output=str(tmp_path / "out.pem")))

    mock_dotenv.assert_called_once_with()
    mock_rotate.assert_called_once_with(9, tmp_path / "out.pem")
    logged = [(c.args[0], c.args[1]) for c in mock_print.call_args_list]
    assert (f"Certificate written to {cert}", "success") in logged
    assert ("note one", "info") in logged


@pytest.mark.asyncio
async def test_run_without_output_flag_passes_none() -> None:
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.cert_rotation.rotate_certificate", return_value=(pathlib.Path("c.pem"), [])) as mock_rotate,
        patch(f"{MODULE}.print_log"),
    ):
        await rotate_cert.run(_args())

    mock_rotate.assert_called_once_with(None, None)


@pytest.mark.asyncio
async def test_run_expands_user_in_output_flag() -> None:
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.cert_rotation.rotate_certificate", return_value=(pathlib.Path("c.pem"), [])) as mock_rotate,
        patch(f"{MODULE}.print_log"),
    ):
        await rotate_cert.run(_args(output="~/certs/x.pem"))

    assert mock_rotate.call_args.args[1] == pathlib.Path("~/certs/x.pem").expanduser()


@pytest.mark.asyncio
async def test_run_settings_warning_is_logged_as_warning() -> None:
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(
            f"{MODULE}.cert_rotation.rotate_certificate",
            return_value=(pathlib.Path("c.pem"), ["WARNING: could not read settings"]),
        ),
        patch(f"{MODULE}.print_log") as mock_print,
    ):
        await rotate_cert.run(_args())

    assert mock_print.call_args_list[-1].args == ("WARNING: could not read settings", "warning")


@pytest.mark.asyncio
async def test_run_rotation_error_logs_and_exits_nonzero() -> None:
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.cert_rotation.rotate_certificate", side_effect=RotationError("ATLAS_PROJECT_ID not set")),
        patch(f"{MODULE}.print_log") as mock_print,
        pytest.raises(SystemExit) as excinfo,
    ):
        await rotate_cert.run(_args())

    assert excinfo.value.code == 1
    message, status = mock_print.call_args_list[-1].args
    assert status == "error"
    assert "ATLAS_PROJECT_ID not set" in message
