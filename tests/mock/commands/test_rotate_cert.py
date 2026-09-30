"""Mock-tier tests for bookscraper.commands.rotate_cert.

cert_rotation.rotate_certificate, load_dotenv, print_log, and (for --check)
config.resolve_mongo_connection are mocked, so no network, .env, or real
~/.bookscrapper is touched. Certificates used by the expiry paths are throwaway
self-signed PEMs generated at test time under tmp_path (never committed).
"""

import json
import pathlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from bookscraper.backends.mongo.cert_rotation import RotationError
from bookscraper.backends.mongo.client import MongoConnectionError
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


def _args(
    months=None,
    output=None,
    check=False,
    warn_days=30,
    no_verify=False,
    verify_attempts=5,
    keep=2,
    no_prune=False,
) -> SimpleNamespace:
    return SimpleNamespace(
        command="rotate-cert",
        months=months,
        output=output,
        check=check,
        warn_days=warn_days,
        no_verify=no_verify,
        verify_attempts=verify_attempts,
        keep=keep,
        no_prune=no_prune,
        log_severity="info",
    )


# --------------------------------------------------------------------------- #
# rotation mode
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_run_success_passes_args_through_and_reports_notes(tmp_path) -> None:
    cert = _write_combined_cert(tmp_path / "X509-cert-new.pem", datetime(2027, 6, 1, tzinfo=UTC))

    with (
        patch(f"{MODULE}.load_dotenv") as mock_dotenv,
        patch(f"{MODULE}.cert_rotation.rotate_certificate", return_value=(cert, ["note one"], True)) as mock_rotate,
        patch(f"{MODULE}.print_log") as mock_print,
    ):
        await rotate_cert.run(_args(months=9, output=str(tmp_path / "out.pem")))

    mock_dotenv.assert_called_once_with()
    mock_rotate.assert_called_once_with(9, tmp_path / "out.pem", verify=True, verify_attempts=5, keep=2, prune=True)
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
        patch(f"{MODULE}.cert_rotation.rotate_certificate", return_value=(missing, [], True)),
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
        patch(f"{MODULE}.cert_rotation.rotate_certificate", return_value=(cert, [], True)) as mock_rotate,
        patch(f"{MODULE}.print_log"),
    ):
        await rotate_cert.run(_args())

    mock_rotate.assert_called_once_with(None, None, verify=True, verify_attempts=5, keep=2, prune=True)


@pytest.mark.asyncio
async def test_run_expands_user_in_output_flag(tmp_path) -> None:
    cert = _write_combined_cert(tmp_path / "c.pem", datetime(2027, 6, 1, tzinfo=UTC))
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.cert_rotation.rotate_certificate", return_value=(cert, [], True)) as mock_rotate,
        patch(f"{MODULE}.print_log"),
    ):
        await rotate_cert.run(_args(output="~/certs/x.pem"))

    assert mock_rotate.call_args.args[1] == pathlib.Path("~/certs/x.pem").expanduser()


@pytest.mark.asyncio
async def test_no_prune_flag_disables_pruning(tmp_path) -> None:
    """
    Scenario: --no-prune and --keep map onto rotate_certificate's prune/keep arguments.
    Boundaries: rotate_certificate mocked; only the forwarded call arguments are asserted.
    On failure: the CLI flags never reach the pruning logic, so retention can't be controlled.
    """
    cert = _write_combined_cert(tmp_path / "c.pem", datetime(2027, 6, 1, tzinfo=UTC))
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.cert_rotation.rotate_certificate", return_value=(cert, [], True)) as mock_rotate,
        patch(f"{MODULE}.print_log"),
    ):
        await rotate_cert.run(_args(no_prune=True, keep=5))

    mock_rotate.assert_called_once_with(None, None, verify=True, verify_attempts=5, keep=5, prune=False)


@pytest.mark.asyncio
async def test_run_settings_warning_is_logged_as_warning(tmp_path) -> None:
    cert = _write_combined_cert(tmp_path / "c.pem", datetime(2027, 6, 1, tzinfo=UTC))
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(
            f"{MODULE}.cert_rotation.rotate_certificate",
            return_value=(cert, ["WARNING: could not read settings"], True),
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
# verification (rotation mode, full flow through cert_rotation.rotate_certificate)
# --------------------------------------------------------------------------- #
def _atlas_env(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_CLIENT_ID", "client-id")
    monkeypatch.setenv("ATLAS_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("ATLAS_PROJECT_ID", "proj123")
    monkeypatch.setenv("ATLAS_DB_USER", "svc-user")
    for var in ("ATLAS_CERT_MONTHS", "ATLAS_CERT_FILE", "TLS_CA_FILE"):
        monkeypatch.delenv(var, raising=False)


def _atlas_responses() -> list[httpx.Response]:
    token = httpx.Response(200, json={"access_token": "tok"}, request=httpx.Request("POST", "https://x"))
    pem = httpx.Response(200, content=b"NEW PEM", request=httpx.Request("POST", "https://x"))
    return [token, pem]


@pytest.mark.asyncio
async def test_verify_final_failure_leaves_settings_untouched_and_exits_one(monkeypatch, isolated_config) -> None:
    """
    Scenario: the new cert never authenticates, so settings.json is not repointed and the
    command exits 1 while the previously configured cert keeps working (ROT-12).
    Boundaries: real rotate_certificate; Atlas HTTP + build_mongo_client faked; ping always fails.
    On failure: a broken identity is committed to settings.json, or the command exits 0.
    """
    _atlas_env(monkeypatch)
    settings = {
        "auth_type": "x509",
        "x509": {
            "uri": {"source": "literal", "value": "mongodb+srv://x"},
            "cert": {"source": "literal", "value": "X509-cert-old.pem"},
        },
    }
    (isolated_config / "settings.json").write_text(json.dumps(settings))
    err = MongoConnectionError("OperationFailure 8000 AtlasError")

    with (
        patch(f"{MODULE}.load_dotenv"),
        patch("bookscraper.backends.mongo.cert_rotation.httpx.post", side_effect=_atlas_responses()),
        patch(
            "bookscraper.backends.mongo.cert_rotation.config.resolve_mongo_connection",
            return_value=("mongodb+srv://x", "old.pem"),
        ),
        patch("bookscraper.backends.mongo.cert_rotation.build_mongo_client"),
        patch("bookscraper.backends.mongo.cert_rotation.ping", side_effect=err),
        patch(f"{MODULE}.print_log") as mock_print,
        pytest.raises(SystemExit) as excinfo,
    ):
        await rotate_cert.run(_args(verify_attempts=1))

    assert excinfo.value.code == 1
    # settings.json still points at the old cert - never repointed at the unverified one.
    assert json.loads((isolated_config / "settings.json").read_text()) == settings
    warnings = [c.args[0] for c in mock_print.call_args_list if "failed verification" in c.args[0]]
    assert warnings and "8000" in warnings[0]


@pytest.mark.asyncio
async def test_no_verify_flag_skips_ping(monkeypatch, isolated_config) -> None:
    """
    Scenario: --no-verify repoints settings.json without ever pinging Atlas.
    Boundaries: real rotate_certificate; build_mongo_client patched to detect any ping attempt.
    On failure: the offline path still contacts Atlas, defeating --no-verify.
    """
    _atlas_env(monkeypatch)
    (isolated_config / "settings.json").write_text(
        json.dumps(
            {
                "auth_type": "x509",
                "x509": {
                    "uri": {"source": "literal", "value": "mongodb+srv://x"},
                    "cert": {"source": "literal", "value": "X509-cert-old.pem"},
                },
            }
        )
    )

    with (
        patch(f"{MODULE}.load_dotenv"),
        patch("bookscraper.backends.mongo.cert_rotation.httpx.post", side_effect=_atlas_responses()),
        patch("bookscraper.backends.mongo.cert_rotation.build_mongo_client") as mock_build,
        patch(f"{MODULE}.cert_rotation.read_certificate_expiry"),
        patch(f"{MODULE}.print_log"),
    ):
        await rotate_cert.run(_args(no_verify=True))

    mock_build.assert_not_called()
    written = json.loads((isolated_config / "settings.json").read_text())
    assert written["x509"]["cert"]["value"].startswith("X509-cert-")
    assert written["x509"]["cert"]["value"] != "X509-cert-old.pem"


@pytest.mark.asyncio
async def test_verify_failure_message_has_no_password_or_host(monkeypatch, isolated_config, caplog) -> None:
    """
    Scenario: a pymongo auth failure carrying a URI+password is redacted everywhere - no status
    line, print_log call, or log record leaks the secret (ROT-11/13, SEC-3/4/8 inherited).
    Boundaries: real client.ping does the redaction; build_mongo_client faked to a client whose
    ping raises OperationFailure with an embedded password.
    On failure: the connection string / password reaches a user-facing sink or a log.
    """
    from pymongo.errors import OperationFailure

    _atlas_env(monkeypatch)
    (isolated_config / "settings.json").write_text(
        json.dumps(
            {
                "auth_type": "x509",
                "x509": {
                    "uri": {"source": "literal", "value": "mongodb+srv://x"},
                    "cert": {"source": "literal", "value": "X509-cert-old.pem"},
                },
            }
        )
    )
    secret_uri = "mongodb+srv://dbuser:SUPERSECRETPW@prod-cluster.abcd.mongodb.net/?authMechanism=MONGODB-X509"
    leaky = OperationFailure(f"auth failed for {secret_uri}", code=8000, details={"codeName": "AtlasError"})
    fake_client = SimpleNamespace(
        admin=SimpleNamespace(command=lambda *a, **k: (_ for _ in ()).throw(leaky)), close=lambda: None
    )

    printed: list[str] = []

    def _capture(msg, status="info") -> None:
        printed.append(msg)

    with (
        caplog.at_level("DEBUG"),
        patch(f"{MODULE}.load_dotenv"),
        patch("bookscraper.backends.mongo.cert_rotation.httpx.post", side_effect=_atlas_responses()),
        patch(
            "bookscraper.backends.mongo.cert_rotation.config.resolve_mongo_connection",
            return_value=(secret_uri, "old.pem"),
        ),
        patch("bookscraper.backends.mongo.cert_rotation.build_mongo_client", return_value=fake_client),
        patch(f"{MODULE}.print_log", side_effect=_capture),
        pytest.raises(SystemExit) as excinfo,
    ):
        await rotate_cert.run(_args(verify_attempts=1))

    assert excinfo.value.code == 1
    haystack = "\n".join(printed) + "\n" + caplog.text
    assert "SUPERSECRETPW" not in haystack
    assert "prod-cluster" not in haystack
    assert "dbuser" not in haystack
    # The redacted code still surfaces so the failure is diagnosable.
    assert any("8000" in line for line in printed)


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
