"""Mock-tier tests for bookscraper.commands.doctor.

Every external the command touches is redirected or mocked, so no test ever reads or writes the real
.env, ~/.bookscrapper, or repo-root books.json: ``isolated_config`` / ``isolated_local_store`` point
config and the JSON store at tmp_path, ``doctor.ENV_FILE`` and ``book_utils.LOG_DIR`` are
monkeypatched to tmp_path, every MongoDB credential env var is stripped, ``load_dotenv`` and
``async_playwright`` are patched, ``database.check_mongodb_connection`` is a mock (no network), and
``print_log`` is captured. Certificates come from throwaway self-signed PEMs generated at test time
(never committed).
"""

import pathlib
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from bookscraper import book_utils, exitcodes
from bookscraper.commands import doctor

MODULE = "bookscraper.commands.doctor"

_TEST_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

_MONGO_ENV_VARS = (
    "MONGODB_URI",
    "TLS_CERT_FILE",
    "TLS_CA_FILE",
    "MONGODB_DB_NAME",
    "MONGODB_COLLECTION_NAME",
    "MONGODB_SERVER_SELECTION_TIMEOUT_MS",
    "ANTHROPIC_API_KEY",
    "ATLAS_CLIENT_ID",
    "ATLAS_CLIENT_SECRET",
    "ATLAS_PROJECT_ID",
    "ATLAS_DB_USER",
)


def _write_cert(path: pathlib.Path, expiry: datetime) -> pathlib.Path:
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


def _args(online: bool = False, store_backend=None) -> SimpleNamespace:
    """Build an argparse-style namespace for doctor.run()."""
    return SimpleNamespace(command="doctor", online=online, store_backend=store_backend, log_severity="info")


def _playwright_with_executable(path: str) -> MagicMock:
    """Build an async_playwright() stand-in whose ``chromium.executable_path`` is ``path``."""
    playwright_obj = MagicMock()
    playwright_obj.chromium.executable_path = path
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=playwright_obj)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


def _text(messages: list) -> str:
    """Join every captured print_log message body into one searchable string."""
    return "\n".join(body for body, _status in messages)


def _row(messages: list, name: str) -> str:
    """Return the single captured table row whose body contains ``name``."""
    return next(body for body, _status in messages if name in body)


@pytest.fixture
def doctor_env(monkeypatch, tmp_path, isolated_config, isolated_local_store) -> SimpleNamespace:
    """Redirect every real path/credential doctor reads at tmp_path and a clean environment.

    Points config/store (via ``isolated_config`` / ``isolated_local_store``), the log directory and
    the ``.env`` probe at tmp_path, and strips all MongoDB/Atlas/Anthropic env vars so presence
    checks are deterministic. Returns the resolved paths plus monkeypatch for per-test tweaks.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(book_utils, "LOG_DIR", log_dir)
    env_file = tmp_path / "dotenv-probe"
    monkeypatch.setattr(doctor, "ENV_FILE", env_file)
    for var in _MONGO_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return SimpleNamespace(
        config_dir=isolated_config,
        store=isolated_local_store,
        log_dir=log_dir,
        env_file=env_file,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )


async def _run(
    *, online: bool = False, store_backend=None, executable=None, ping: bool = True
) -> tuple[int, list, MagicMock]:
    """Run doctor.run() with its externals mocked; return (exit_code, messages, ping_mock)."""
    executable = executable if executable is not None else sys.executable
    messages: list = []
    ping_mock = MagicMock(return_value=ping)
    with (
        patch(f"{MODULE}.load_dotenv"),
        patch(f"{MODULE}.async_playwright", _playwright_with_executable(executable)),
        patch(f"{MODULE}.print_log", side_effect=lambda body, status="info": messages.append((body, status))),
        patch(f"{MODULE}.database.check_mongodb_connection", ping_mock),
    ):
        code = await doctor.run(_args(online=online, store_backend=store_backend))
    return code, messages, ping_mock


# --------------------------------------------------------------------------- #
# run() - end-to-end
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_doctor_all_ok_exits_zero(doctor_env):
    """
    [Local] doctor command: a clean legacy setup with Chromium present passes with exit 0.

    Scenario:
        Given no settings.json, no client cert, a writable tmp store/log dir and a Chromium
        executable that exists
        When doctor.run() is awaited offline
        Then no check FAILs and the command returns OK (0) after loading .env once.

    Boundaries:
        - async_playwright, load_dotenv, print_log and the Mongo ping are mocked; no network.

    On failure, first check:
        - Whether a check unexpectedly returned FAIL (scan the captured rows).
    """
    code, messages, _ping = await _run()
    assert code == exitcodes.OK
    assert "legacy mode (no settings.json); MONGODB_URI not set" in _text(messages)


@pytest.mark.asyncio
async def test_doctor_missing_chromium_is_fail(doctor_env):
    """
    [Local] doctor command: a Chromium executable_path that does not exist is a FAIL and exits 1.

    Scenario:
        Given async_playwright reports an executable path that does not exist on disk
        When doctor.run() is awaited
        Then the Playwright Chromium row is FAIL and the command returns FATAL (1).

    Boundaries:
        - Only the chromium check drives the failure; everything else is a clean legacy setup.

    On failure, first check:
        - check_chromium()'s Path(executable).exists() branch in commands/doctor.py.
    """
    code, messages, _ping = await _run(executable="/nonexistent/path/to/chromium")
    assert code == exitcodes.FATAL
    row = _row(messages, "Playwright Chromium")
    assert "FAIL" in row
    assert "playwright install chromium" in row


@pytest.mark.asyncio
async def test_doctor_reports_settings_mode_and_auth_type(doctor_env):
    """
    [Local] doctor command: with a settings.json present it reports settings mode and the auth type.

    Scenario:
        Given an owner-only x509 settings.json whose uri is env-sourced (MONGODB_URI) and whose cert
        is a literal filename pointing at a valid, far-future cert under the config dir
        When doctor.run() is awaited
        Then the settings.json row names "settings mode", "auth_type=x509" and "uri from env
        MONGODB_URI", and the command returns OK.

    Boundaries:
        - resolve_mongo_connection runs against the isolated config dir; no network.

    On failure, first check:
        - check_settings() and _describe_uri_source() in commands/doctor.py.
    """
    cert_path = _write_cert(doctor_env.config_dir / "X509-cert-test.pem", datetime.now(UTC) + timedelta(days=200))
    settings_file = doctor_env.config_dir / "settings.json"
    settings_file.write_text(
        '{"auth_type": "x509", "x509": {"uri": {"source": "env", "value": "MONGODB_URI"}, '
        '"cert": {"source": "literal", "value": "' + cert_path.name + '"}}}'
    )
    settings_file.chmod(0o600)
    doctor_env.monkeypatch.setenv("MONGODB_URI", "mongodb+srv://u:p@host.example/db")

    code, messages, _ping = await _run()

    row = _row(messages, "settings.json")
    assert "settings mode" in row
    assert "auth_type=x509" in row
    assert "uri from env MONGODB_URI" in row
    assert code == exitcodes.OK


@pytest.mark.asyncio
async def test_doctor_never_prints_secret_values(doctor_env):
    """
    [Local] doctor command: seeded secret values never appear in any printed line.

    Scenario:
        Given sentinel secrets in MONGODB_URI (user/pass/host), ANTHROPIC_API_KEY and the ATLAS_*
        vars, legacy mode, and the online Mongo ping failing
        When doctor.run() is awaited with --online
        Then none of the sentinel secret substrings appear in any captured message.

    Boundaries:
        - Covers legacy mode and the online ping-failed path; the settings.json literal path is
          covered separately by test_doctor_settings_literal_uri_never_printed.

    On failure, first check:
        - Any detail string that interpolates a resolved URI/cert/credential value.
    """
    doctor_env.monkeypatch.setenv("MONGODB_URI", "mongodb+srv://secretuser:secretpass@secrethost.example/db")
    doctor_env.monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SENTINELKEY")
    doctor_env.monkeypatch.setenv("ATLAS_CLIENT_ID", "ATLAS-SENTINEL-ID")
    doctor_env.monkeypatch.setenv("ATLAS_CLIENT_SECRET", "ATLAS-SENTINEL-SECRET")
    doctor_env.monkeypatch.setenv("ATLAS_PROJECT_ID", "ATLAS-SENTINEL-PROJECT")
    doctor_env.monkeypatch.setenv("ATLAS_DB_USER", "ATLAS-SENTINEL-USER")

    _code, messages, _ping = await _run(online=True, ping=False)

    blob = _text(messages)
    for sentinel in (
        "secretuser",
        "secretpass",
        "secrethost",
        "sk-ant-SENTINELKEY",
        "ATLAS-SENTINEL-ID",
        "ATLAS-SENTINEL-SECRET",
        "ATLAS-SENTINEL-PROJECT",
        "ATLAS-SENTINEL-USER",
    ):
        assert sentinel not in blob
    assert blob  # sanity: the table was actually printed


@pytest.mark.asyncio
async def test_doctor_settings_literal_uri_never_printed(doctor_env):
    """
    [Local] doctor command: a literal connection URI in settings.json is never printed.

    Scenario:
        Given an owner-only pass-auth settings.json whose uri is a literal connection string
        carrying a sentinel password
        When doctor.run() is awaited
        Then the settings.json row says "uri from literal" and no captured line contains the
        sentinel value.

    Boundaries:
        - Exercises the settings-present + literal-source branch of _describe_uri_source.

    On failure, first check:
        - _describe_uri_source()'s literal branch must emit "uri from literal" and never the value.
    """
    settings_file = doctor_env.config_dir / "settings.json"
    settings_file.write_text(
        '{"auth_type": "pass", "pass": {"uri": {"source": "literal", '
        '"value": "mongodb+srv://lituser:LITERAL-SENTINEL-PW@lit.example/db"}}}'
    )
    settings_file.chmod(0o600)

    _code, messages, _ping = await _run()

    blob = _text(messages)
    assert "uri from literal" in _row(messages, "settings.json")
    assert "LITERAL-SENTINEL-PW" not in blob
    assert "lituser" not in blob


@pytest.mark.asyncio
async def test_doctor_mongo_ping_only_with_online_flag(doctor_env):
    """
    [Local] doctor command: the MongoDB ping runs only when --online is passed.

    Scenario:
        Given a clean legacy setup
        When doctor.run() is awaited first without --online, then with --online
        Then the offline run SKIPs the ping and never calls check_mongodb_connection, while the
        online run calls it once and reports OK.

    Boundaries:
        - check_mongodb_connection is mocked; no real connection either way.

    On failure, first check:
        - check_mongodb_ping()'s online gate in commands/doctor.py.
    """
    _code, offline_msgs, offline_ping = await _run(online=False)
    offline_ping.assert_not_called()
    assert "pass --online to test the connection" in _row(offline_msgs, "MongoDB ping")

    _code2, online_msgs, online_ping = await _run(online=True, ping=True)
    online_ping.assert_called_once_with()
    assert "connected" in _row(online_msgs, "MongoDB ping")


@pytest.mark.asyncio
async def test_doctor_mongo_ping_skipped_for_json_backend(doctor_env):
    """
    [Local] doctor command: --store-backend json skips the (mongo-only) ping even with --online.

    Scenario:
        Given --store-backend json and --online
        When doctor.run() is awaited
        Then the ping is SKIPped as not applicable and check_mongodb_connection is never called.

    Boundaries:
        - Only the store_backend gate is exercised.

    On failure, first check:
        - check_mongodb_ping()'s store_backend=="json" branch.
    """
    _code, messages, ping = await _run(online=True, store_backend="json")
    ping.assert_not_called()
    assert "not applicable to the json backend" in _row(messages, "MongoDB ping")


@pytest.mark.asyncio
async def test_doctor_warn_does_not_fail_exit_code(doctor_env):
    """
    [Local] doctor command: a WARN (soon-expiring cert) does not change the exit code from 0.

    Scenario:
        Given a legacy setup whose TLS_CERT_FILE points at a cert expiring in 5 days (inside the
        30-day warn window) and no other failing check
        When doctor.run() is awaited
        Then the Client cert row is WARN but the command still returns OK (0).

    Boundaries:
        - Only the cert check warns; everything else is clean.

    On failure, first check:
        - The exit-code rule in run(): only a FAIL changes the code (WARN never does).
    """
    cert = _write_cert(doctor_env.tmp_path / "soon.pem", datetime.now(UTC) + timedelta(days=5))
    doctor_env.monkeypatch.setenv("TLS_CERT_FILE", str(cert))

    code, messages, _ping = await _run()

    assert code == exitcodes.OK
    assert "WARN" in _row(messages, "Client cert")


# --------------------------------------------------------------------------- #
# individual check functions
# --------------------------------------------------------------------------- #
def test_check_python_version_fail_when_below_minimum():
    """
    [Local] doctor check: an interpreter below Requires-Python is a FAIL.

    Scenario:
        Given package metadata declaring Requires-Python ">=99.0"
        When check_python_version() runs on the current (far older) interpreter
        Then it returns a FAIL naming the requirement.

    Boundaries:
        - Only the version comparison; metadata lookup is mocked.

    On failure, first check:
        - _MIN_PYTHON_RE parsing and the sys.version_info comparison.
    """
    meta = MagicMock()
    meta.get.return_value = ">=99.0"
    with patch(f"{MODULE}.importlib.metadata.metadata", return_value=meta):
        result = doctor.check_python_version()
    assert result.status == doctor.FAIL
    assert "99.0" in result.detail


def test_check_python_version_ok_when_metadata_missing():
    """
    [Local] doctor check: absent package metadata still reports OK with the running version.

    Scenario:
        Given metadata() raising PackageNotFoundError
        When check_python_version() runs
        Then it returns OK with the running interpreter version.

    Boundaries:
        - Only the PackageNotFoundError fallback branch.

    On failure, first check:
        - The except PackageNotFoundError branch of check_python_version().
    """
    with patch(f"{MODULE}.importlib.metadata.metadata", side_effect=doctor.importlib.metadata.PackageNotFoundError):
        result = doctor.check_python_version()
    assert result.status == doctor.OK


def test_check_package_version_warn_when_not_installed():
    """
    [Local] doctor check: a missing distribution reports WARN, not FAIL.

    Scenario:
        Given importlib.metadata.version raising PackageNotFoundError
        When check_package_version() runs
        Then it returns WARN (a source checkout is usable, not broken).

    Boundaries:
        - Only the version() lookup is mocked.

    On failure, first check:
        - The except branch of check_package_version().
    """
    with patch(f"{MODULE}.importlib.metadata.version", side_effect=doctor.importlib.metadata.PackageNotFoundError):
        result = doctor.check_package_version()
    assert result.status == doctor.WARN


def test_check_env_file_reports_presence(doctor_env):
    """
    [Local] doctor check: the .env probe reports present/not present without reading contents.

    Scenario:
        Given the monkeypatched ENV_FILE first absent then present
        When check_env_file() runs in each state
        Then it reports "not present" then "present", both at OK.

    Boundaries:
        - Only existence is checked; the file is never opened.

    On failure, first check:
        - check_env_file() and the ENV_FILE module constant.
    """
    assert doctor.check_env_file().detail == "not present"
    doctor_env.env_file.write_text("SECRET=should-not-be-read")
    assert doctor.check_env_file().detail == "present"


def test_check_settings_malformed_is_fail(doctor_env):
    """
    [Local] doctor check: a settings.json with an unknown top-level key is a FAIL.

    Scenario:
        Given a settings.json carrying a stray top-level key
        When check_settings() runs
        Then it returns FAIL and the detail names only the offending key (never a value).

    Boundaries:
        - Uses the isolated config dir; load_settings raises ConfigError.

    On failure, first check:
        - check_settings()'s ConfigError handling of load_settings().
    """
    (doctor_env.config_dir / "settings.json").write_text('{"auth_type": "pass", "bogus_key": 1}')
    result = doctor.check_settings()
    assert result.status == doctor.FAIL
    assert "bogus_key" in result.detail


def test_check_settings_resolve_failure_is_fail(doctor_env):
    """
    [Local] doctor check: a settings.json that parses but cannot resolve is a FAIL.

    Scenario:
        Given an x509 settings.json whose uri is env-sourced but the env var is unset
        When check_settings() runs
        Then it returns FAIL from the resolve step, naming the env var name (not a secret).

    Boundaries:
        - MONGODB_URI is stripped by the fixture, so resolve raises ConfigError.

    On failure, first check:
        - check_settings()'s resolve_mongo_connection() ConfigError branch.
    """
    (doctor_env.config_dir / "settings.json").write_text(
        '{"auth_type": "x509", "x509": {"uri": {"source": "env", "value": "MONGODB_URI"}, '
        '"cert": {"source": "literal", "value": "x.pem"}}}'
    )
    result = doctor.check_settings()
    assert result.status == doctor.FAIL
    assert "settings mode" in result.detail


def test_check_settings_malformed_block_is_clean_fail(doctor_env):
    """
    [Local] doctor check: an x509 block missing its cert subkey is a clean FAIL, no traceback.

    Scenario:
        Given a settings.json whose x509 block has a uri but no cert subkey
        When check_settings() runs
        Then resolve_mongo_connection raises KeyError, which is caught and reported as a FAIL naming
        only the exception type (never a key name or value), rather than escaping as a traceback.

    Boundaries:
        - Exercises the broadened (KeyError, TypeError) guard; load_settings itself still parses.

    On failure, first check:
        - check_settings()'s (KeyError, TypeError) branch around resolve_mongo_connection().
    """
    (doctor_env.config_dir / "settings.json").write_text(
        '{"auth_type": "x509", "x509": {"uri": {"source": "literal", "value": "mongodb+srv://x/y"}}}'
    )
    result = doctor.check_settings()
    assert result.status == doctor.FAIL
    assert "malformed settings.json" in result.detail
    assert "KeyError" in result.detail
    assert "cert" not in result.detail


def test_check_client_cert_missing_file_is_fail(doctor_env):
    """
    [Local] doctor check: a configured cert path that does not exist is a FAIL.

    Scenario:
        Given TLS_CERT_FILE pointing at a non-existent file (legacy mode)
        When check_client_cert() runs
        Then it returns FAIL naming the missing path.

    Boundaries:
        - Only the exists() branch; no PEM parsing.

    On failure, first check:
        - check_client_cert()'s cert_path.exists() branch.
    """
    doctor_env.monkeypatch.setenv("TLS_CERT_FILE", str(doctor_env.tmp_path / "nope.pem"))
    result = doctor.check_client_cert()
    assert result.status == doctor.FAIL
    assert "not found" in result.detail


def test_check_client_cert_unparseable_is_fail(doctor_env):
    """
    [Local] doctor check: a cert file with no CERTIFICATE block is a FAIL naming only the path.

    Scenario:
        Given TLS_CERT_FILE pointing at a file that is not a PEM certificate
        When check_client_cert() runs
        Then it returns FAIL and the detail does not contain the file's bytes.

    Boundaries:
        - read_certificate_expiry raises RotationError (path-only message).

    On failure, first check:
        - check_client_cert()'s RotationError branch.
    """
    bad = doctor_env.tmp_path / "bad.pem"
    bad.write_text("not a certificate at all")
    doctor_env.monkeypatch.setenv("TLS_CERT_FILE", str(bad))
    result = doctor.check_client_cert()
    assert result.status == doctor.FAIL
    assert "not a certificate at all" not in result.detail


def test_check_client_cert_expired_is_fail(doctor_env):
    """
    [Local] doctor check: an already-expired cert is a FAIL.

    Scenario:
        Given TLS_CERT_FILE pointing at a cert that expired yesterday
        When check_client_cert() runs
        Then it returns FAIL with the expiry date.

    Boundaries:
        - Throwaway cert generated at test time.

    On failure, first check:
        - check_client_cert()'s days < 0 branch.
    """
    cert = _write_cert(doctor_env.tmp_path / "expired.pem", datetime.now(UTC) - timedelta(days=1))
    doctor_env.monkeypatch.setenv("TLS_CERT_FILE", str(cert))
    result = doctor.check_client_cert()
    assert result.status == doctor.FAIL


def test_check_client_cert_valid_is_ok(doctor_env):
    """
    [Local] doctor check: a cert far from expiry is OK and reports its path and date.

    Scenario:
        Given TLS_CERT_FILE pointing at a cert valid for ~200 days
        When check_client_cert() runs
        Then it returns OK with the cert path and the ISO expiry date.

    Boundaries:
        - Throwaway cert generated at test time.

    On failure, first check:
        - check_client_cert()'s OK branch and the expiry arithmetic.
    """
    cert = _write_cert(doctor_env.tmp_path / "good.pem", datetime.now(UTC) + timedelta(days=200))
    doctor_env.monkeypatch.setenv("TLS_CERT_FILE", str(cert))
    result = doctor.check_client_cert()
    assert result.status == doctor.OK
    assert str(cert) in result.detail


def test_check_client_cert_config_error_is_skip(doctor_env):
    """
    [Local] doctor check: an unresolvable Mongo config SKIPs the cert check.

    Scenario:
        Given a malformed settings.json so resolve_mongo_connection raises ConfigError
        When check_client_cert() runs
        Then it returns SKIP (the settings.json check already reports the real failure).

    Boundaries:
        - Only the ConfigError branch; no cert file involved.

    On failure, first check:
        - check_client_cert()'s ConfigError branch.
    """
    (doctor_env.config_dir / "settings.json").write_text('{"auth_type": "nonsense"}')
    result = doctor.check_client_cert()
    assert result.status == doctor.SKIP


def test_check_json_store_corrupt_is_fail(doctor_env):
    """
    [Local] doctor check: a store file that is not valid JSON is a FAIL.

    Scenario:
        Given the store file containing invalid JSON
        When check_json_store() runs
        Then it returns FAIL saying the store is not valid JSON.

    Boundaries:
        - store._load_books raises LocalStoreCorruptError.

    On failure, first check:
        - check_json_store()'s LocalStoreCorruptError branch.
    """
    doctor_env.store.write_text("{not json")
    result = doctor.check_json_store()
    assert result.status == doctor.FAIL
    assert "not valid JSON" in result.detail


def test_check_json_store_counts_books(doctor_env):
    """
    [Local] doctor check: a valid store reports its book count at OK.

    Scenario:
        Given a store file with two documents
        When check_json_store() runs
        Then it returns OK reporting "2 book(s)".

    Boundaries:
        - Reads the tmp store via store._load_books.

    On failure, first check:
        - check_json_store()'s count path.
    """
    doctor_env.store.write_text('[{"hash": "a"}, {"hash": "b"}]')
    result = doctor.check_json_store()
    assert result.status == doctor.OK
    assert "2 book(s)" in result.detail


def test_check_json_store_missing_not_writable_is_fail(doctor_env):
    """
    [Local] doctor check: an absent store in a non-writable directory is a FAIL.

    Scenario:
        Given no store file and os.access reporting the directory non-writable
        When check_json_store() runs
        Then it returns FAIL.

    Boundaries:
        - os.access is patched so the branch is deterministic regardless of the test uid.

    On failure, first check:
        - check_json_store()'s "no store yet" + not-writable branch.
    """
    with patch(f"{MODULE}.os.access", return_value=False):
        result = doctor.check_json_store()
    assert result.status == doctor.FAIL


def test_check_json_store_existing_not_writable_is_warn(doctor_env):
    """
    [Local] doctor check: an existing store in a non-writable directory is a WARN.

    Scenario:
        Given a valid store file and os.access reporting the directory non-writable
        When check_json_store() runs
        Then it returns WARN (the store is readable, just not updatable).

    Boundaries:
        - os.access is patched so the branch is deterministic.

    On failure, first check:
        - check_json_store()'s existing-store not-writable branch.
    """
    doctor_env.store.write_text("[]")
    with patch(f"{MODULE}.os.access", return_value=False):
        result = doctor.check_json_store()
    assert result.status == doctor.WARN


def test_check_log_dir_missing_is_warn(doctor_env):
    """
    [Local] doctor check: a non-existent log dir is a WARN.

    Scenario:
        Given book_utils.LOG_DIR pointing at a directory that does not exist
        When check_log_dir() runs
        Then it returns WARN.

    Boundaries:
        - Only the exists() branch.

    On failure, first check:
        - check_log_dir()'s not-exists branch.
    """
    doctor_env.monkeypatch.setattr(book_utils, "LOG_DIR", doctor_env.tmp_path / "no-such-logs")
    result = doctor.check_log_dir()
    assert result.status == doctor.WARN


def test_check_log_dir_counts_logs(doctor_env):
    """
    [Local] doctor check: an existing writable log dir reports the retained-log count at OK.

    Scenario:
        Given a log dir containing two bookscraper_*.log files
        When check_log_dir() runs
        Then it returns OK reporting "2 log(s)".

    Boundaries:
        - Reads the tmp log dir.

    On failure, first check:
        - check_log_dir()'s glob count and writable branch.
    """
    (doctor_env.log_dir / "bookscraper_1.log").write_text("x")
    (doctor_env.log_dir / "bookscraper_2.log").write_text("x")
    result = doctor.check_log_dir()
    assert result.status == doctor.OK
    assert "2 log(s)" in result.detail


def test_check_log_dir_not_writable_is_warn(doctor_env):
    """
    [Local] doctor check: an existing but non-writable log dir is a WARN.

    Scenario:
        Given an existing log dir and os.access reporting it non-writable
        When check_log_dir() runs
        Then it returns WARN.

    Boundaries:
        - os.access is patched so the branch is deterministic.

    On failure, first check:
        - check_log_dir()'s not-writable branch.
    """
    with patch(f"{MODULE}.os.access", return_value=False):
        result = doctor.check_log_dir()
    assert result.status == doctor.WARN


def test_check_anthropic_key_presence(doctor_env):
    """
    [Local] doctor check: ANTHROPIC_API_KEY is reported presence-only, never its value.

    Scenario:
        Given ANTHROPIC_API_KEY unset then set to a sentinel
        When check_anthropic_key() runs in each state
        Then it reports "not set" then "set", and never the sentinel value.

    Boundaries:
        - Reads the env var directly.

    On failure, first check:
        - check_anthropic_key().
    """
    assert doctor.check_anthropic_key().detail == "not set"
    doctor_env.monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-XYZ")
    result = doctor.check_anthropic_key()
    assert result.detail == "set"
    assert "XYZ" not in result.detail


def test_check_atlas_vars_presence(doctor_env):
    """
    [Local] doctor check: each ATLAS_* var is reported presence-only.

    Scenario:
        Given one ATLAS_* var set and the rest unset
        When check_atlas_vars() runs
        Then it returns one result per variable, set/not set, never a value.

    Boundaries:
        - Reads the env vars directly.

    On failure, first check:
        - check_atlas_vars() and the _ATLAS_VARS tuple.
    """
    doctor_env.monkeypatch.setenv("ATLAS_CLIENT_ID", "ATLAS-ID-SENTINEL")
    results = doctor.check_atlas_vars()
    assert len(results) == len(doctor._ATLAS_VARS)
    by_name = {r.name: r for r in results}
    assert by_name["ATLAS_CLIENT_ID"].detail == "set"
    assert by_name["ATLAS_CLIENT_SECRET"].detail == "not set"
    for result in results:
        assert "SENTINEL" not in result.detail


@pytest.mark.asyncio
async def test_check_mongodb_ping_failure_is_fail(doctor_env):
    """
    [Local] doctor check: an online ping that fails is a FAIL with a redacted detail.

    Scenario:
        Given --online and check_mongodb_connection returning False
        When check_mongodb_ping() runs
        Then it returns FAIL with a detail that points at the run log (no raw reason).

    Boundaries:
        - check_mongodb_connection is mocked to False.

    On failure, first check:
        - check_mongodb_ping()'s online-failure branch.
    """
    with patch(f"{MODULE}.database.check_mongodb_connection", return_value=False):
        result = await doctor.check_mongodb_ping(online=True, store_backend=None)
    assert result.status == doctor.FAIL
    assert "redacted" in result.detail


@pytest.mark.asyncio
async def test_check_chromium_driver_failure_is_fail():
    """
    [Local] doctor check: a Playwright driver error surfaces as a FAIL, not a crash.

    Scenario:
        Given async_playwright() raising when entered
        When check_chromium() runs
        Then it returns FAIL naming only the exception class (never a traceback).

    Boundaries:
        - async_playwright is patched to raise; no real driver.

    On failure, first check:
        - check_chromium()'s broad-except branch.
    """
    with patch(f"{MODULE}.async_playwright", MagicMock(side_effect=RuntimeError("boom"))):
        result = await doctor.check_chromium()
    assert result.status == doctor.FAIL
    assert "Playwright unavailable" in result.detail
    assert "boom" not in result.detail


def test_settings_permission_note_flags_group_readable(doctor_env):
    """
    [Local] doctor helper: a group/other-readable settings.json yields a permission note.

    Scenario:
        Given a settings.json at mode 0644
        When _settings_permission_note() runs on POSIX
        Then it returns a note naming the group/other-readable problem and the chmod fix.

    Boundaries:
        - POSIX only; the note names only the path, never a field value.

    On failure, first check:
        - The mode & 0o077 branch of _settings_permission_note().
    """
    settings_file = doctor_env.config_dir / "settings.json"
    settings_file.write_text("{}")
    settings_file.chmod(0o644)
    note = doctor._settings_permission_note()
    assert note is not None
    assert "group/other-readable" in note


def test_settings_permission_note_none_when_missing(doctor_env):
    """
    [Local] doctor helper: a missing settings.json produces no permission note.

    Scenario:
        Given no settings.json at config.SETTINGS_FILE
        When _settings_permission_note() runs
        Then path.stat() raises OSError and the helper returns None.

    Boundaries:
        - Only the OSError branch.

    On failure, first check:
        - _settings_permission_note()'s stat() OSError handling.
    """
    assert doctor._settings_permission_note() is None


def test_settings_permission_note_flags_symlink(doctor_env, tmp_path):
    """
    [Local] doctor helper: a symlinked settings.json is flagged.

    Scenario:
        Given config.SETTINGS_FILE pointing at a symlink to a real settings file
        When _settings_permission_note() runs
        Then the note names the symlink problem.

    Boundaries:
        - The stat follows the link; is_symlink() sees the link.

    On failure, first check:
        - _settings_permission_note()'s is_symlink() branch.
    """
    target = tmp_path / "real-settings.json"
    target.write_text("{}")
    link = doctor_env.config_dir / "settings-link.json"
    link.symlink_to(target)
    doctor_env.monkeypatch.setattr(doctor.config, "SETTINGS_FILE", link)
    note = doctor._settings_permission_note()
    assert note is not None
    assert "is a symlink" in note


def test_settings_permission_note_flags_foreign_owner(doctor_env):
    """
    [Local] doctor helper: a settings.json not owned by the current user is flagged.

    Scenario:
        Given an owner-only settings.json and os.getuid reporting a different uid
        When _settings_permission_note() runs
        Then the note names the ownership problem (not the mode one).

    Boundaries:
        - os.getuid is patched; the file's real owner is unchanged.

    On failure, first check:
        - _settings_permission_note()'s st_uid branch.
    """
    settings_file = doctor_env.config_dir / "settings.json"
    settings_file.write_text("{}")
    settings_file.chmod(0o600)
    doctor_env.monkeypatch.setattr(doctor.os, "getuid", lambda: -1)
    note = doctor._settings_permission_note()
    assert note is not None
    assert "is not owned by you" in note


def test_settings_permission_note_skipped_on_windows(doctor_env):
    """
    [Local] doctor helper: the permission check is a no-op on Windows.

    Scenario:
        Given os.name reporting "nt"
        When _settings_permission_note() runs
        Then it returns None without touching the filesystem.

    Boundaries:
        - POSIX permission bits do not apply on Windows.

    On failure, first check:
        - The os.name == "nt" guard in _settings_permission_note().
    """
    doctor_env.monkeypatch.setattr(doctor.os, "name", "nt")
    assert doctor._settings_permission_note() is None


def test_check_settings_warns_on_insecure_permissions(doctor_env):
    """
    [Local] doctor check: a resolvable but group-readable settings.json is a WARN.

    Scenario:
        Given a valid x509 settings.json at mode 0644 with a real cert and MONGODB_URI set
        When check_settings() runs
        Then it returns WARN with both the auth type and the permission warning.

    Boundaries:
        - resolve_mongo_connection succeeds; only the permission note downgrades OK to WARN.

    On failure, first check:
        - check_settings()'s WARN branch when _settings_permission_note() is non-None.
    """
    cert_path = _write_cert(doctor_env.config_dir / "c.pem", datetime.now(UTC) + timedelta(days=200))
    settings_file = doctor_env.config_dir / "settings.json"
    settings_file.write_text(
        '{"auth_type": "x509", "x509": {"uri": {"source": "env", "value": "MONGODB_URI"}, '
        '"cert": {"source": "literal", "value": "' + cert_path.name + '"}}}'
    )
    settings_file.chmod(0o644)
    doctor_env.monkeypatch.setenv("MONGODB_URI", "mongodb+srv://u:p@host.example/db")
    result = doctor.check_settings()
    assert result.status == doctor.WARN
    assert "permission warning" in result.detail


def test_describe_uri_source_handles_unknown_shapes():
    """
    [Local] doctor helper: an unexpected uri spec is reported as "uri source unknown".

    Scenario:
        Given a settings dict with no uri spec, and one with an unrecognized source
        When _describe_uri_source() runs
        Then both yield "uri source unknown" (never a value).

    Boundaries:
        - Pure function; no filesystem or network.

    On failure, first check:
        - _describe_uri_source()'s non-dict and unrecognized-source fallbacks.
    """
    assert doctor._describe_uri_source({}, "x509") == "uri source unknown"
    weird = {"x509": {"uri": {"source": "weird", "value": "x"}}}
    assert doctor._describe_uri_source(weird, "x509") == "uri source unknown"
