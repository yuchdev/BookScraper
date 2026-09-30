"""Mock-tier tests for bookscraper.commands.rotate_cert.

cert_rotation.rotate_certificate, load_dotenv, print_log, and (for --check)
config.resolve_mongo_connection are mocked, so no network, .env, or real
~/.bookscrapper is touched. Certificates used by the expiry paths are throwaway
self-signed PEMs generated at test time under tmp_path (never committed).
"""

import pathlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from bookscraper.backends.mongo.cert_rotation import RotationError
from bookscraper.commands import rotate_cert

MODULE = "bookscraper.commands.rotate_cert"

_TEST_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write_combined_cert(path: pathlib.Path, expiry: datetime) -> pathlib.Path:
    """Write a throwaway combined key+cert PEM expiring at ``expiry`` and return the path."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bookscraper-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(_TEST_KEY.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(expiry - timedelta(days=365))
        .not_valid_after(expiry)
        .sign(_TEST_KEY, hashes.SHA256())
    )
    key_pem = _TEST_KEY.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(key_pem + cert.public_bytes(serialization.Encoding.PEM))
    return path


def _args(months=None, output=None, check=False, warn_days=30) -> SimpleNamespace:
    return SimpleNamespace(
        command="rotate-cert", months=months, output=output, check=check, warn_days=warn_days, log_severity="info"
    )


# --------------------------------------------------------------------------- #
# rotation mode
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_run_success_passes_args_through_and_reports_notes(tmp_path) -> None:
    cert = _write_combined_cert(tmp_path / "X509-cert-new.pem", datetime(2027, 6, 1, tzinfo=UTC))

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
    # The post-rotation expiry line reports the new cert's expiry date.
    assert ("New certificate expires on 2027-06-01.", "info") in logged


@pytest.mark.asyncio
async def test_run_reports_when_new_cert_expiry_unreadable(tmp_path) -> None:
    # rotate_certificate reports a path that isn't a real cert; reading its expiry fails,
    # which is reported as a warning rather than crashing the (already-succeeded) rotation.
    missing = tmp_path / "not-a-cert.pem"
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.cert_rotation.rotate_certificate", return_value=(missing, [])),
        patch(f"{MODULE}.print_log") as mock_print,
    ):
        await rotate_cert.run(_args())

    logged = [(c.args[0], c.args[1]) for c in mock_print.call_args_list]
    assert any("Could not read the new certificate's expiry" in msg and status == "warning" for msg, status in logged)


@pytest.mark.asyncio
async def test_run_without_output_flag_passes_none(tmp_path) -> None:
    cert = _write_combined_cert(tmp_path / "c.pem", datetime(2027, 6, 1, tzinfo=UTC))
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.cert_rotation.rotate_certificate", return_value=(cert, [])) as mock_rotate,
        patch(f"{MODULE}.print_log"),
    ):
        await rotate_cert.run(_args())

    mock_rotate.assert_called_once_with(None, None)


@pytest.mark.asyncio
async def test_run_expands_user_in_output_flag(tmp_path) -> None:
    cert = _write_combined_cert(tmp_path / "c.pem", datetime(2027, 6, 1, tzinfo=UTC))
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.cert_rotation.rotate_certificate", return_value=(cert, [])) as mock_rotate,
        patch(f"{MODULE}.print_log"),
    ):
        await rotate_cert.run(_args(output="~/certs/x.pem"))

    assert mock_rotate.call_args.args[1] == pathlib.Path("~/certs/x.pem").expanduser()


@pytest.mark.asyncio
async def test_run_settings_warning_is_logged_as_warning(tmp_path) -> None:
    cert = _write_combined_cert(tmp_path / "c.pem", datetime(2027, 6, 1, tzinfo=UTC))
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(
            f"{MODULE}.cert_rotation.rotate_certificate",
            return_value=(cert, ["WARNING: could not read settings"]),
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


# --------------------------------------------------------------------------- #
# --check mode
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_check_mode_exit_zero_when_far_from_expiry(tmp_path) -> None:
    cert = _write_combined_cert(tmp_path / "X509-cert.pem", datetime.now(UTC) + timedelta(days=180))
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.config.resolve_mongo_connection", return_value=("mongodb+srv://x", str(cert))),
        patch(f"{MODULE}.cert_rotation.rotate_certificate") as mock_rotate,
        patch(f"{MODULE}.print_log") as mock_print,
        pytest.raises(SystemExit) as excinfo,
    ):
        await rotate_cert.run(_args(check=True))

    assert excinfo.value.code == 0
    mock_rotate.assert_not_called()
    logged = [c.args[0] for c in mock_print.call_args_list]
    assert any(line.startswith("expires:") for line in logged)
    assert any(line.startswith("days remaining:") for line in logged)


@pytest.mark.asyncio
async def test_check_mode_exit_three_within_warn_window(tmp_path) -> None:
    cert = _write_combined_cert(tmp_path / "X509-cert.pem", datetime.now(UTC) + timedelta(days=10))
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.config.resolve_mongo_connection", return_value=("mongodb+srv://x", str(cert))),
        patch(f"{MODULE}.print_log"),
        pytest.raises(SystemExit) as excinfo,
    ):
        await rotate_cert.run(_args(check=True, warn_days=30))

    assert excinfo.value.code == 3


@pytest.mark.asyncio
async def test_check_mode_exit_one_when_expired(tmp_path) -> None:
    cert = _write_combined_cert(tmp_path / "X509-cert.pem", datetime.now(UTC) - timedelta(days=3))
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.config.resolve_mongo_connection", return_value=("mongodb+srv://x", str(cert))),
        patch(f"{MODULE}.print_log") as mock_print,
        pytest.raises(SystemExit) as excinfo,
    ):
        await rotate_cert.run(_args(check=True))

    assert excinfo.value.code == 1
    assert any("expired" in c.args[0] for c in mock_print.call_args_list)


@pytest.mark.asyncio
async def test_check_mode_exit_one_when_unreadable(tmp_path) -> None:
    missing = tmp_path / "gone.pem"
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.config.resolve_mongo_connection", return_value=("mongodb+srv://x", str(missing))),
        patch(f"{MODULE}.print_log") as mock_print,
        pytest.raises(SystemExit) as excinfo,
    ):
        await rotate_cert.run(_args(check=True))

    assert excinfo.value.code == 1
    assert any("Certificate check failed" in c.args[0] for c in mock_print.call_args_list)


@pytest.mark.asyncio
async def test_check_mode_without_x509_config_is_noop_zero() -> None:
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.config.resolve_mongo_connection", return_value=("mongodb+srv://x", None)),
        patch(f"{MODULE}.print_log") as mock_print,
        pytest.raises(SystemExit) as excinfo,
    ):
        await rotate_cert.run(_args(check=True))

    assert excinfo.value.code == 0
    assert any("nothing to check" in c.args[0] for c in mock_print.call_args_list)


@pytest.mark.asyncio
async def test_check_mode_config_error_exits_one() -> None:
    from bookscraper.backends.mongo.config import ConfigError

    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.config.resolve_mongo_connection", side_effect=ConfigError("bad settings")),
        patch(f"{MODULE}.print_log") as mock_print,
        pytest.raises(SystemExit) as excinfo,
    ):
        await rotate_cert.run(_args(check=True))

    assert excinfo.value.code == 1
    assert any("Certificate check failed" in c.args[0] for c in mock_print.call_args_list)


@pytest.mark.asyncio
async def test_check_mode_returns_without_rotating() -> None:
    """
    Scenario: in --check mode run() delegates to _run_check and returns without rotating,
    even if _run_check itself does not exit the process.
    Boundaries: _run_check patched to a no-op so the post-delegation guard return is exercised.
    On failure: --check falls through into the rotation path and contacts Atlas.
    """
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}._run_check") as mock_check,
        patch(f"{MODULE}.cert_rotation.rotate_certificate") as mock_rotate,
        patch(f"{MODULE}.print_log"),
    ):
        await rotate_cert.run(_args(check=True, warn_days=7))

    mock_check.assert_called_once_with(7)
    mock_rotate.assert_not_called()


@pytest.mark.asyncio
async def test_check_mode_never_requires_atlas_env(tmp_path, monkeypatch) -> None:
    """
    Scenario: --check works with no ATLAS_* variables and never calls the rotation client.
    Boundaries: every ATLAS_* var stripped; a valid, far-from-expiry cert configured.
    On failure: the offline health check depends on Atlas credentials it should never need.
    """
    for var in ("ATLAS_CLIENT_ID", "ATLAS_CLIENT_SECRET", "ATLAS_PROJECT_ID", "ATLAS_DB_USER"):
        monkeypatch.delenv(var, raising=False)
    cert = _write_combined_cert(tmp_path / "X509-cert.pem", datetime.now(UTC) + timedelta(days=200))

    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.config.resolve_mongo_connection", return_value=("mongodb+srv://x", str(cert))),
        patch(f"{MODULE}.cert_rotation.rotate_certificate") as mock_rotate,
        patch(f"{MODULE}.cert_rotation.get_access_token") as mock_token,
        patch(f"{MODULE}.print_log"),
        pytest.raises(SystemExit) as excinfo,
    ):
        await rotate_cert.run(_args(check=True))

    assert excinfo.value.code == 0
    mock_rotate.assert_not_called()
    mock_token.assert_not_called()
