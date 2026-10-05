"""Unit tests for bookscraper.backends.mongo.cert_rotation's pure/filesystem logic.

Real file I/O via tmp_path (through the isolated_config fixture, so the real
~/.bookscrapper/settings.json is never read or written); no network.
"""

import json
import os
import pathlib
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from bookscraper.backends.mongo import cert_rotation

# One throwaway key generated once and reused across the cert fixtures below - the material is
# never committed and never leaves the test process (spec: "Never commit a key").
_TEST_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_TEST_KEY_PEM = _TEST_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)


def _cert_pem(not_valid_after: datetime) -> bytes:
    """A self-signed CERTIFICATE PEM block expiring at ``not_valid_after``."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bookscraper-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(_TEST_KEY.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_valid_after - timedelta(days=365))
        .not_valid_after(not_valid_after)
        .sign(_TEST_KEY, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def _combined_pem(not_valid_after: datetime, *, key_first: bool = True) -> bytes:
    """A combined private-key + certificate PEM, in either block ordering."""
    cert_pem = _cert_pem(not_valid_after)
    return (_TEST_KEY_PEM + cert_pem) if key_first else (cert_pem + _TEST_KEY_PEM)


def _write_settings(config_dir, settings: dict):
    """Write a settings dict as JSON into ``config_dir/settings.json``."""
    (config_dir / "settings.json").write_text(json.dumps(settings))


# --------------------------------------------------------------------------- #
# resolve_months
# --------------------------------------------------------------------------- #
def test_resolve_months_defaults_to_six(monkeypatch):
    """
    [Local] cert rotation: verifies resolve_months returns 6 when ATLAS_CERT_MONTHS is unset.

    Scenario:
        Given ATLAS_CERT_MONTHS deleted from the environment
        When resolve_months() is called with no argument
        Then 6 is returned

    Boundaries:
        - No CLI argument; env var absent; DEFAULT_MONTHS is 6
        - ATLAS_CERT_MONTHS must be explicitly deleted in case a prior test set it

    On failure, first check:
        - Whether DEFAULT_MONTHS constant is still 6 in cert_rotation
        - Recent changes to the env-unset branch in resolve_months
    """
    monkeypatch.delenv("ATLAS_CERT_MONTHS", raising=False)
    assert cert_rotation.resolve_months() == 6


def test_resolve_months_reads_env(monkeypatch):
    """
    [Local] cert rotation: verifies ATLAS_CERT_MONTHS env var is used when set and no argument is given.

    Scenario:
        Given ATLAS_CERT_MONTHS set to "12"
        When resolve_months() is called with no argument
        Then 12 is returned

    Boundaries:
        - Valid integer string in env; no CLI argument
        - env var takes effect only when no explicit argument overrides it

    On failure, first check:
        - Whether the env-var lookup key is exactly "ATLAS_CERT_MONTHS"
        - Recent changes to the env-read branch in resolve_months
    """
    monkeypatch.setenv("ATLAS_CERT_MONTHS", "12")
    assert cert_rotation.resolve_months() == 12


def test_resolve_months_argument_beats_env(monkeypatch):
    """
    [Local] cert rotation: verifies an explicit argument takes precedence over ATLAS_CERT_MONTHS.

    Scenario:
        Given ATLAS_CERT_MONTHS set to "12"
        When resolve_months(3) is called with an explicit argument
        Then 3 is returned, not 12

    Boundaries:
        - Precedence rule: CLI flag > env var > default
        - ATLAS_CERT_MONTHS is set so the env-var path is exercised but overridden

    On failure, first check:
        - Whether resolve_months checks the argument before reading the env var
        - Recent changes to the precedence logic in resolve_months
    """
    monkeypatch.setenv("ATLAS_CERT_MONTHS", "12")
    assert cert_rotation.resolve_months(3) == 3


def test_resolve_months_rejects_non_integer_env(monkeypatch):
    """
    [Local] cert rotation: verifies a non-integer ATLAS_CERT_MONTHS raises RotationError naming the var.

    Scenario:
        Given ATLAS_CERT_MONTHS set to "soon" (not an integer)
        When resolve_months() is called
        Then RotationError is raised and the message contains "ATLAS_CERT_MONTHS"

    Boundaries:
        - Only the non-integer case; out-of-range integers are tested separately
        - The var name must appear in the error, not just a bare ValueError

    On failure, first check:
        - Whether int() parse failure is wrapped in RotationError or propagates as ValueError
        - Whether the error message names "ATLAS_CERT_MONTHS"
    """
    monkeypatch.setenv("ATLAS_CERT_MONTHS", "soon")
    with pytest.raises(cert_rotation.RotationError, match="ATLAS_CERT_MONTHS"):
        cert_rotation.resolve_months()


@pytest.mark.parametrize("months", [0, -1, 25])
def test_resolve_months_rejects_out_of_range(months):
    """
    [Unit] cert rotation: verifies months outside the 1-24 range raise RotationError.

    Scenario:
        Given an out-of-range months value (0, -1, or 25) passed directly
        When resolve_months(months) is called
        Then RotationError is raised with "1-24" in the message

    Boundaries:
        - Parametrized over 0, -1, 25; boundary values 1 and 24 are tested separately
        - Error message must contain "1-24" so the user knows the valid range

    On failure, first check:
        - Whether the range check is inclusive on both ends (1 <= months <= 24)
        - Whether the error message contains the range hint "1-24"
    """
    with pytest.raises(cert_rotation.RotationError, match="1-24"):
        cert_rotation.resolve_months(months)


@pytest.mark.parametrize("months", [1, 24])
def test_resolve_months_accepts_range_boundaries(months):
    """
    [Unit] cert rotation: verifies the boundary months 1 and 24 are accepted without error.

    Scenario:
        Given months 1 or 24 (fence-post values) passed directly
        When resolve_months(months) is called
        Then the same value is returned without error

    Boundaries:
        - Exact boundary values; 0 and 25 (first out-of-range) are tested separately
        - No env var involved; pure range-validation logic

    On failure, first check:
        - Whether the range check uses strict inequality (< 1 or > 24) vs. <= / >=
        - Recent changes to the validation logic in resolve_months
    """
    assert cert_rotation.resolve_months(months) == months


# --------------------------------------------------------------------------- #
# atomic_write / default_output_path
# --------------------------------------------------------------------------- #
def test_atomic_write_creates_parents_and_owner_only_file(tmp_path):
    """
    [Unit] cert rotation: verifies atomic_write creates parent directories and sets mode 0o600.

    Scenario:
        Given a target path under a non-existent subdirectory
        When atomic_write(target, b"PEM DATA") is called
        Then the file exists with the written bytes and mode 0o600

    Boundaries:
        - Parent directory created via mkdir(parents=True)
        - Mode 0o600 means owner-read-write only; no group or other permissions

    On failure, first check:
        - Whether parent creation is missing and raises FileNotFoundError
        - Whether the file is created with a permissive mode (e.g. 0o644)
    """
    target = tmp_path / "nested" / "cert.pem"
    cert_rotation.atomic_write(target, b"PEM DATA")

    assert target.read_bytes() == b"PEM DATA"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_atomic_write_replaces_existing_file_and_leaves_no_temp_files(tmp_path):
    """
    [Unit] cert rotation: verifies atomic_write overwrites an existing file and leaves no temp files behind.

    Scenario:
        Given a pre-existing cert.pem with b"old"
        When atomic_write(target, b"new") is called
        Then target contains b"new" and only cert.pem remains in the directory

    Boundaries:
        - Atomicity via temp-file + os.replace; temp is cleaned up on success
        - Directory scan after write confirms no leftover temp file

    On failure, first check:
        - Whether the temp file is unlinked after os.replace completes
        - Whether the write is a direct overwrite (non-atomic) rather than rename
    """
    target = tmp_path / "cert.pem"
    target.write_bytes(b"old")

    cert_rotation.atomic_write(target, b"new")

    assert target.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["cert.pem"]


def test_default_output_path_is_timestamped_under_config_dir(isolated_config):
    """
    [Unit] cert rotation: verifies default_output_path returns a timestamped .pem path under CONFIG_DIR.

    Scenario:
        Given isolated_config patched as CONFIG_DIR
        When default_output_path() is called
        Then the path's parent equals isolated_config and the name matches "X509-cert-YYYYMMDD_HHMMSS.pem"

    Boundaries:
        - Timestamp comes from current UTC time; not pinned
        - isolated_config fixture prevents any write to ~/.bookscrapper

    On failure, first check:
        - Whether CONFIG_DIR is used as the parent directory
        - Whether the name format regex "X509-cert-\\d{8}_\\d{6}\\.pem" still matches the output
    """
    path = cert_rotation.default_output_path()

    assert path.parent == isolated_config
    assert re.fullmatch(r"X509-cert-\d{8}_\d{6}\.pem", path.name)


# --------------------------------------------------------------------------- #
# update_settings_after_rotation
# --------------------------------------------------------------------------- #
def test_update_settings_no_file_reports_legacy_fallback(isolated_config):
    """
    [Unit] cert rotation: verifies update_settings_after_rotation reports "nothing to update" when settings.json is absent.

    Scenario:
        Given no settings.json in isolated_config
        When update_settings_after_rotation is called with a new cert path
        Then exactly one note containing "nothing to update" is returned and no file is created

    Boundaries:
        - Absent settings.json means legacy mode; rotation must never create the file
        - Returned list has exactly one element; no file side effect

    On failure, first check:
        - Whether the function creates an empty settings.json on missing-file path
        - Whether the note text matches "nothing to update"
    """
    notes = cert_rotation.update_settings_after_rotation(isolated_config / "X509-cert-new.pem")

    assert len(notes) == 1
    assert "nothing to update" in notes[0]
    assert not (isolated_config / "settings.json").exists()


def test_update_settings_malformed_file_warns_and_is_untouched(isolated_config):
    """
    [Unit] cert rotation: verifies update_settings_after_rotation warns and leaves a malformed settings.json unchanged.

    Scenario:
        Given settings.json containing invalid JSON ("{not json")
        When update_settings_after_rotation is called
        Then note[0] starts with "WARNING" and the file content is byte-for-byte unchanged

    Boundaries:
        - File exists but is not parseable; parse error must never trigger an overwrite
        - Byte-for-byte comparison after the call confirms no write occurred

    On failure, first check:
        - Whether the parse error is caught and converted to a WARNING note
        - Whether the malformed file is overwritten when it should not be
    """
    settings_file = isolated_config / "settings.json"
    settings_file.write_text("{not json")

    notes = cert_rotation.update_settings_after_rotation(isolated_config / "X509-cert-new.pem")

    assert notes[0].startswith("WARNING")
    assert settings_file.read_text() == "{not json"


def test_update_settings_pass_auth_is_untouched(isolated_config):
    """
    [Unit] cert rotation: verifies update_settings_after_rotation is a no-op for password auth.

    Scenario:
        Given settings.json with auth_type "pass"
        When update_settings_after_rotation is called
        Then a note says "not configured for x509" and the file content is unchanged

    Boundaries:
        - Password auth has no cert to repoint; the file must not be modified
        - Exact dict equality check ensures no field is added or mutated

    On failure, first check:
        - Whether the auth_type check correctly skips non-x509 settings
        - Whether the note message contains "not configured for x509"
    """
    settings = {"auth_type": "pass", "pass": {"uri": {"source": "literal", "value": "mongodb+srv://x"}}}
    _write_settings(isolated_config, settings)

    notes = cert_rotation.update_settings_after_rotation(isolated_config / "X509-cert-new.pem")

    assert "not configured for x509" in notes[0]
    assert json.loads((isolated_config / "settings.json").read_text()) == settings


def test_update_settings_literal_cert_is_repointed(isolated_config):
    """
    [Unit] cert rotation: verifies update_settings_after_rotation rewrites the cert filename for a literal source.

    Scenario:
        Given x509 settings with cert source "literal" pointing to "X509-cert-old.pem"
        When update_settings_after_rotation(new_cert_path) is called
        Then settings.json has the new cert filename, URI is unchanged, file is mode 0o600, and note names the new cert

    Boundaries:
        - Only the cert value changes; URI and auth_type must be preserved intact
        - Written file is owner-only (0o600) after the atomic rewrite

    On failure, first check:
        - Whether the URI or other fields are clobbered during the rewrite
        - Whether the file mode after writing is 0o600
    """
    _write_settings(
        isolated_config,
        {
            "auth_type": "x509",
            "x509": {
                "uri": {"source": "literal", "value": "mongodb+srv://x"},
                "cert": {"source": "literal", "value": "X509-cert-old.pem"},
            },
        },
    )

    notes = cert_rotation.update_settings_after_rotation(isolated_config / "X509-cert-new.pem")

    written = json.loads((isolated_config / "settings.json").read_text())
    assert written["x509"]["cert"] == {"source": "literal", "value": "X509-cert-new.pem"}
    # Everything else in the file is preserved, and the write is owner-only.
    assert written["x509"]["uri"] == {"source": "literal", "value": "mongodb+srv://x"}
    assert stat.S_IMODE((isolated_config / "settings.json").stat().st_mode) == 0o600
    assert "X509-cert-new.pem" in notes[0]


def test_update_settings_env_cert_only_reminds(isolated_config):
    """
    [Unit] cert rotation: verifies update_settings_after_rotation prints a reminder for an env-sourced cert.

    Scenario:
        Given x509 settings with cert source "env" pointing to "MY_CERT_VAR"
        When update_settings_after_rotation(new_cert) is called
        Then the note names "MY_CERT_VAR" and the new cert path, and settings.json is unchanged

    Boundaries:
        - env-sourced cert cannot be automatically repointed; only a human can update the variable
        - Exact dict equality check confirms the file was not modified

    On failure, first check:
        - Whether the note contains both the env var name and the new cert path
        - Whether the function incorrectly modifies an env-sourced cert entry
    """
    settings = {
        "auth_type": "x509",
        "x509": {
            "uri": {"source": "literal", "value": "mongodb+srv://x"},
            "cert": {"source": "env", "value": "MY_CERT_VAR"},
        },
    }
    _write_settings(isolated_config, settings)
    new_cert = isolated_config / "X509-cert-new.pem"

    notes = cert_rotation.update_settings_after_rotation(new_cert)

    assert "MY_CERT_VAR" in notes[0]
    assert str(new_cert) in notes[0]
    assert json.loads((isolated_config / "settings.json").read_text()) == settings


def test_update_settings_unrecognized_cert_shape_is_untouched(isolated_config):
    """
    [Unit] cert rotation: verifies update_settings_after_rotation reports "unrecognized shape" for an unknown cert source.

    Scenario:
        Given x509 settings with cert source "vault" (not "literal" or "env")
        When update_settings_after_rotation is called
        Then "unrecognized shape" appears in the note and settings.json is unchanged

    Boundaries:
        - Only "literal" and "env" are acted on; any other source is reported and left alone
        - Exact dict equality check confirms no modification occurred

    On failure, first check:
        - Whether the source check has a default branch that silently modifies the file
        - Whether the note text contains "unrecognized shape"
    """
    settings = {"auth_type": "x509", "x509": {"cert": {"source": "vault", "value": "x"}}}
    _write_settings(isolated_config, settings)

    notes = cert_rotation.update_settings_after_rotation(isolated_config / "X509-cert-new.pem")

    assert "unrecognized shape" in notes[0]
    assert json.loads((isolated_config / "settings.json").read_text()) == settings


# --------------------------------------------------------------------------- #
# read_certificate_expiry
# --------------------------------------------------------------------------- #
def test_read_expiry_from_combined_key_and_cert_pem(tmp_path):
    """
    [Unit] cert rotation: verifies read_certificate_expiry parses the expiry from a key-first combined PEM.

    Scenario:
        Given a combined PEM file with the private key first and the certificate second
        When read_certificate_expiry is called
        Then the returned datetime matches the certificate's not-valid-after date and is tz-aware UTC.

    Boundaries:
        - Key-first ordering (rotation's own write order); expiry must be tz-aware
        - Real file I/O via tmp_path; real cryptography parse

    On failure, first check:
        - Whether read_certificate_expiry scans for the CERTIFICATE block regardless of file position
        - Recent changes to the PEM-block regex or cryptography parse path
    """
    expiry = datetime(2027, 6, 1, 12, 0, 0, tzinfo=UTC)
    cert_file = tmp_path / "X509-cert.pem"
    cert_file.write_bytes(_combined_pem(expiry, key_first=True))

    result = cert_rotation.read_certificate_expiry(cert_file)

    assert result == expiry
    assert result.tzinfo is not None


def test_read_expiry_parses_first_cert_when_cert_precedes_key(tmp_path):
    """
    [Unit] cert rotation: verifies read_certificate_expiry still parses when the cert block precedes the key.

    Scenario:
        Given a combined PEM file with the certificate first and the private key second
        When read_certificate_expiry is called
        Then the correct expiry is returned regardless of block order.

    Boundaries:
        - Reversed block ordering (cert-first); the parser must key on block type, not file offset
        - Real file I/O via tmp_path

    On failure, first check:
        - Whether the PEM-block scan is position-dependent (reads only the first block)
        - Recent changes to the regex or loop that finds the CERTIFICATE block
    """
    expiry = datetime(2028, 1, 15, 0, 0, 0, tzinfo=UTC)
    cert_file = tmp_path / "X509-cert.pem"
    cert_file.write_bytes(_combined_pem(expiry, key_first=False))

    assert cert_rotation.read_certificate_expiry(cert_file) == expiry


def test_read_expiry_missing_file_raises_rotation_error(tmp_path):
    """
    [Unit] cert rotation: verifies a missing cert file raises RotationError naming only the path.

    Scenario:
        Given a path that does not exist
        When read_certificate_expiry is called with that path
        Then RotationError is raised and its message contains the file path.

    Boundaries:
        - File does not exist; no file I/O succeeds
        - The error message must name the path but not contain file contents

    On failure, first check:
        - Whether the missing-file case is caught and re-raised as RotationError
        - Whether a raw OSError escapes instead
    """
    missing = tmp_path / "nope.pem"
    with pytest.raises(cert_rotation.RotationError, match=re.escape(str(missing))):
        cert_rotation.read_certificate_expiry(missing)


def test_read_expiry_pem_without_certificate_block_raises(tmp_path):
    """
    [Unit] cert rotation: verifies a key-only PEM raises RotationError with no certificate block.

    Scenario:
        Given a PEM file that contains only a private key and no CERTIFICATE block
        When read_certificate_expiry is called
        Then RotationError is raised with a message matching "No X.509 CERTIFICATE block".

    Boundaries:
        - Key-only input; the key bytes must not appear in the error message
        - Real file I/O via tmp_path

    On failure, first check:
        - Whether the parser guards against mistaking a PRIVATE KEY block for a CERTIFICATE block
        - Whether private key bytes leak into the exception text
    """
    key_only = tmp_path / "key-only.pem"
    key_only.write_bytes(_TEST_KEY_PEM)

    with pytest.raises(cert_rotation.RotationError, match="No X.509 CERTIFICATE block"):
        cert_rotation.read_certificate_expiry(key_only)


def test_read_expiry_garbage_certificate_block_raises(tmp_path):
    """
    [Unit] cert rotation: verifies garbage inside a CERTIFICATE block raises RotationError.

    Scenario:
        Given a PEM file with a CERTIFICATE header but non-base64 garbage body
        When read_certificate_expiry is called
        Then RotationError is raised with a message matching "Could not parse".

    Boundaries:
        - The regex matches the block but cryptography cannot parse the body
        - Raw cryptography ValueError must be wrapped, not propagated

    On failure, first check:
        - Whether the cryptography parse exception is caught and re-raised as RotationError
        - Whether "Could not parse" is still the error prefix in the exception message
    """
    garbage = tmp_path / "garbage.pem"
    garbage.write_bytes(b"-----BEGIN CERTIFICATE-----\nnot base64 at all\n-----END CERTIFICATE-----\n")

    with pytest.raises(cert_rotation.RotationError, match="Could not parse"):
        cert_rotation.read_certificate_expiry(garbage)


def test_read_expiry_never_leaks_key_bytes_on_error(tmp_path):
    """
    [Unit] cert rotation: verifies private key bytes do not appear in RotationError messages (CWE-532/CWE-312).

    Scenario:
        Given a combined PEM with a private key and a corrupted certificate block
        When read_certificate_expiry raises RotationError
        Then the exception message contains neither "PRIVATE KEY" nor the raw key bytes.

    Boundaries:
        - Combined key + corrupted cert; the key bytes themselves must be absent from the message
        - SEC-3/SEC-4 redaction contract

    On failure, first check:
        - Whether RotationError formatting includes the raw file content in the message
        - Whether the exception wraps the ValueError with the PEM body attached
    """
    corrupt = tmp_path / "X509-cert.pem"
    corrupt.write_bytes(_TEST_KEY_PEM + b"-----BEGIN CERTIFICATE-----\nZZZZ\n-----END CERTIFICATE-----\n")

    with pytest.raises(cert_rotation.RotationError) as excinfo:
        cert_rotation.read_certificate_expiry(corrupt)

    message = str(excinfo.value)
    assert "PRIVATE KEY" not in message
    assert _TEST_KEY_PEM.decode() not in message


# --------------------------------------------------------------------------- #
# cert_expiry_warning
# --------------------------------------------------------------------------- #
def test_cert_expiry_warning_none_cert_returns_none():
    """
    [Unit] cert rotation: verifies cert_expiry_warning returns None when no cert is configured.

    Scenario:
        Given cert_path is None (no x509 cert in use)
        When cert_expiry_warning is called
        Then None is returned, indicating nothing to warn about.

    Boundaries:
        - cert_path=None only; no file I/O
        - Guards against a crash in the pre-flight path when password auth is in use

    On failure, first check:
        - Whether cert_expiry_warning short-circuits on None input before any file access
        - Whether the return type changed from None to an empty string
    """
    assert cert_rotation.cert_expiry_warning(None) is None


def test_cert_expiry_warning_far_expiry_returns_none(tmp_path):
    """
    [Unit] cert rotation: verifies cert_expiry_warning returns None for a cert expiring far in the future.

    Scenario:
        Given a cert file with expiry ~180 days from now
        When cert_expiry_warning is called with the default 30-day window
        Then None is returned.

    Boundaries:
        - 180 days > 30-day warn window; real cert generated via cryptography in-test
        - Real file I/O via tmp_path

    On failure, first check:
        - Whether the warn-window threshold comparison is correct (> vs >=)
        - Whether the days-remaining calculation uses UTC consistently
    """
    cert_file = tmp_path / "X509-cert.pem"
    cert_file.write_bytes(_combined_pem(datetime.now(UTC) + timedelta(days=180)))

    assert cert_rotation.cert_expiry_warning(cert_file) is None


def test_cert_expiry_warning_within_window_warns(tmp_path):
    """
    [Unit] cert rotation: verifies cert_expiry_warning returns a renewal warning when expiry is within the window.

    Scenario:
        Given a cert file with expiry ~10 days from now
        When cert_expiry_warning is called with the default 30-day window
        Then a non-None warning is returned containing "expires on" and "rotate-cert".

    Boundaries:
        - 10 days < 30-day warn window; real cert generated in-test
        - Warning text must mention the expiry and the rotate-cert command

    On failure, first check:
        - Whether the warn window is 30 days and the comparison includes the boundary
        - Whether the warning string still contains "expires on" and "rotate-cert"
    """
    cert_file = tmp_path / "X509-cert.pem"
    cert_file.write_bytes(_combined_pem(datetime.now(UTC) + timedelta(days=10)))

    warning = cert_rotation.cert_expiry_warning(cert_file)

    assert warning is not None
    assert "expires on" in warning
    assert "rotate-cert" in warning


def test_cert_expiry_warning_expired_warns(tmp_path):
    """
    [Unit] cert rotation: verifies cert_expiry_warning returns an expired warning for an already-expired cert.

    Scenario:
        Given a cert file with expiry 5 days in the past
        When cert_expiry_warning is called
        Then a non-None warning is returned containing "expired on".

    Boundaries:
        - Expiry in the past; the warning must say "expired on", not "expires on"
        - Real cert generated in-test via cryptography

    On failure, first check:
        - Whether the "expired" vs "expires" branch distinguishes past expiry correctly
        - Whether a UTC offset mismatch causes a past-expiry cert to appear future
    """
    cert_file = tmp_path / "X509-cert.pem"
    cert_file.write_bytes(_combined_pem(datetime.now(UTC) - timedelta(days=5)))

    warning = cert_rotation.cert_expiry_warning(cert_file)

    assert warning is not None
    assert "expired on" in warning


def test_cert_expiry_warning_parse_failure_is_warning_not_raise(tmp_path):
    """
    [Unit] cert rotation: verifies cert_expiry_warning returns a warning string instead of raising on parse failure (ROT-17).

    Scenario:
        Given a key-only PEM file (no CERTIFICATE block)
        When cert_expiry_warning is called (the non-raising pre-flight path)
        Then a warning string is returned starting with "WARNING", without raising RotationError.

    Boundaries:
        - Non-raising path (cert_expiry_warning vs read_certificate_expiry)
        - Warning must not contain "PRIVATE KEY" or key bytes (SEC redaction)

    On failure, first check:
        - Whether cert_expiry_warning catches RotationError from read_certificate_expiry and returns a warning
        - Whether private key bytes appear in the warning string
    """
    key_only = tmp_path / "key-only.pem"
    key_only.write_bytes(_TEST_KEY_PEM)

    warning = cert_rotation.cert_expiry_warning(key_only)

    assert warning is not None
    assert warning.startswith("WARNING")
    assert "PRIVATE KEY" not in warning
    assert _TEST_KEY_PEM.decode() not in warning


# --------------------------------------------------------------------------- #
# prune_old_certificates
# --------------------------------------------------------------------------- #
@pytest.fixture
def prune_config(isolated_config) -> Path:
    """isolated_config plus a hard safety guard: refuse to run a prune test whose CONFIG_DIR was
    not redirected away from the real ~/.bookscrapper. A real prune would delete live private
    keys, so an unpatched CONFIG_DIR must fail the test, never fall through to the real directory.
    """
    real = pathlib.Path.home() / ".bookscrapper"
    assert cert_rotation.config.CONFIG_DIR != real, "CONFIG_DIR not isolated - refusing to prune real certs"
    assert isolated_config != real
    return isolated_config


def _make_cert(config_dir: pathlib.Path, name: str, mtime: float) -> pathlib.Path:
    """Write a throwaway ``name`` under ``config_dir`` with an explicit ``st_mtime`` for ordering."""
    path = config_dir / name
    path.write_bytes(b"pem")
    os.utime(path, (mtime, mtime))
    return path


def test_prune_keeps_newest_n(prune_config):
    """
    [Unit] cert rotation: verifies prune_old_certificates keeps only the newest N certs by mtime.

    Scenario:
        Given five cert files with strictly increasing mtimes and keep=2
        When prune_old_certificates is called with an empty protected set
        Then only the two newest by mtime survive and three "Pruned superseded" status lines are returned.

    Boundaries:
        - Five candidates, no protected paths; sort order is st_mtime descending
        - Real file I/O via prune_config fixture; CONFIG_DIR is isolated

    On failure, first check:
        - Whether prune_old_certificates sorts by mtime and respects the keep count
        - Whether the status line text "Pruned superseded" is still the expected prefix
    """
    for i in range(5):
        _make_cert(prune_config, f"X509-cert-{i}.pem", 1000 + i)

    status = cert_rotation.prune_old_certificates(2, set())

    remaining = sorted(p.name for p in prune_config.glob("X509-cert-*.pem"))
    assert remaining == ["X509-cert-3.pem", "X509-cert-4.pem"]
    assert sum("Pruned superseded" in s for s in status) == 3


def test_prune_never_deletes_protected_even_if_oldest(prune_config):
    """
    [Unit] cert rotation: verifies a protected cert is never deleted even when it is the oldest (ROT-6).

    Scenario:
        Given five certs where the oldest is in the protected set, with keep=2
        When prune_old_certificates is called
        Then the protected oldest cert survives alongside the two newest, leaving three files total.

    Boundaries:
        - Protected set contains the oldest candidate; keep=2
        - Guards against self-inflicted lockout by deleting the active identity

    On failure, first check:
        - Whether protected paths are excluded from deletion candidates before the keep cutoff
        - Whether canonical path comparison (Path.resolve) is applied to protected paths
    """
    certs = [_make_cert(prune_config, f"X509-cert-{i}.pem", 1000 + i) for i in range(5)]
    oldest = certs[0]

    cert_rotation.prune_old_certificates(2, {oldest})

    remaining = sorted(p.name for p in prune_config.glob("X509-cert-*.pem"))
    assert remaining == ["X509-cert-0.pem", "X509-cert-3.pem", "X509-cert-4.pem"]


def test_prune_ignores_files_outside_glob_and_dir(prune_config, tmp_path):
    """
    [Unit] cert rotation: verifies only X509-cert-*.pem files inside CONFIG_DIR are pruning candidates (ROT-9).

    Scenario:
        Given two valid cert candidates in CONFIG_DIR, a non-matching name, a wrong extension,
        and a matching-name cert outside CONFIG_DIR
        When prune_old_certificates is called with keep=1
        Then only the oldest in-dir candidate is deleted; all other files remain.

    Boundaries:
        - Non-matching name, wrong extension, and an outside-dir cert are all non-candidates
        - Guards against deleting a --output cert written elsewhere

    On failure, first check:
        - Whether the glob is anchored to CONFIG_DIR and uses the exact "X509-cert-*.pem" pattern
        - Whether an outside-dir path is accidentally included via a loose glob
    """
    old = _make_cert(prune_config, "X509-cert-0.pem", 1000)
    new = _make_cert(prune_config, "X509-cert-1.pem", 2000)
    non_matching = prune_config / "settings.json"
    non_matching.write_text("{}")
    wrong_ext = prune_config / "X509-cert-note.txt"
    wrong_ext.write_text("x")
    outside = tmp_path / "X509-cert-external.pem"
    outside.write_bytes(b"pem")

    cert_rotation.prune_old_certificates(1, set())

    assert not old.exists()  # superseded candidate deleted
    assert new.exists()  # newest kept
    assert non_matching.exists()
    assert wrong_ext.exists()
    assert outside.exists()  # matching name but outside CONFIG_DIR - never a candidate


def test_prune_unlink_failure_is_warning(prune_config, monkeypatch):
    """
    [Local] cert rotation: verifies a failed unlink produces a WARNING status line and does not raise (ROT-10).

    Scenario:
        Given two cert files and Path.unlink patched to raise OSError
        When prune_old_certificates is called with keep=1
        Then a WARNING status line containing "OSError" is returned and the file survives.

    Boundaries:
        - Path.unlink monkeypatched; the undeletable cert must not abort the command
        - Guards against a single permission error crashing the whole rotation

    On failure, first check:
        - Whether the unlink call is wrapped in a try/except that emits a WARNING status line
        - Whether the status line starts with "WARNING" and mentions "OSError"
    """
    _make_cert(prune_config, "X509-cert-0.pem", 1000)
    _make_cert(prune_config, "X509-cert-1.pem", 2000)

    def _boom(self, *args, **kwargs):
        """Simulate a permission-denied failure on unlink."""
        raise OSError("permission denied")

    monkeypatch.setattr(pathlib.Path, "unlink", _boom)

    status = cert_rotation.prune_old_certificates(1, set())

    assert any(s.startswith("WARNING") and "OSError" in s for s in status)
    assert (prune_config / "X509-cert-0.pem").exists()  # survived the failed unlink


def test_prune_tolerates_dangling_symlink_and_vanished_file(prune_config, monkeypatch):
    """
    [Local] cert rotation: verifies pruning tolerates a dangling symlink and a file that vanished between glob and mtime read.

    Scenario:
        Given a dangling symlink, a file that appears in the glob but no longer exists,
        and two real cert files, with keep=1
        When prune_old_certificates is called
        Then the newest real cert survives, the old cert is deleted, the dangling symlink is pruned,
        and no WARNING status lines are emitted.

    Boundaries:
        - glob() monkeypatched to inject a vanished path; dangling symlink created via os.symlink
        - Guards against FileNotFoundError aborting the command after a successful rotation

    On failure, first check:
        - Whether the sort key handles lstat() on a dangling symlink or non-existent path gracefully
        - Whether the dangling-symlink candidate is pruned via unlink rather than left behind
    """
    _make_cert(prune_config, "X509-cert-old.pem", 1000)
    newest = _make_cert(prune_config, "X509-cert-new.pem", 3000)
    dangling = prune_config / "X509-cert-dangling.pem"
    dangling.symlink_to(prune_config / "does-not-exist.pem")
    os.utime(dangling, (500, 500), follow_symlinks=False)
    vanished = prune_config / "X509-cert-vanished.pem"
    real_glob = pathlib.Path.glob
    monkeypatch.setattr(pathlib.Path, "glob", lambda self, pattern: [*real_glob(self, pattern), vanished])

    status = cert_rotation.prune_old_certificates(1, set())

    assert newest.exists()
    assert not (prune_config / "X509-cert-old.pem").exists()
    assert not dangling.is_symlink()  # the dangling link itself was pruned, not followed
    assert not any(s.startswith("WARNING") for s in status)


@pytest.mark.parametrize("spelling", ["relative", "dotdot", "symlink"])
def test_prune_protects_active_cert_via_different_spelling(prune_config, tmp_path, monkeypatch, spelling):
    """
    [Local] cert rotation: verifies the active cert is protected even when its path is spelled differently from the glob result (ROT-7).

    Scenario:
        Given the active cert is the oldest of two, protected via a relative path, ``..``-path, or symlink
        When prune_old_certificates is called with keep=1
        Then the active cert survives despite being the oldest and despite the path spelling difference.

    Boundaries:
        - Parametrised over three spellings: relative, dotdot, symlink
        - Canonical comparison via Path.resolve() is the mechanism under test

    On failure, first check:
        - Whether protected path comparison uses Path.resolve() before equality check
        - Whether a symlink's resolved target matches the glob path's resolved path
    """
    active = _make_cert(prune_config, "X509-cert-active.pem", 1000)  # oldest
    _make_cert(prune_config, "X509-cert-new.pem", 2000)  # newest

    if spelling == "relative":
        monkeypatch.chdir(prune_config)
        spelled = pathlib.Path("X509-cert-active.pem")
    elif spelling == "dotdot":
        (prune_config / "sub").mkdir()
        spelled = prune_config / "sub" / ".." / "X509-cert-active.pem"
    else:  # symlink
        spelled = tmp_path / "link-to-active.pem"
        spelled.symlink_to(active)

    cert_rotation.prune_old_certificates(1, {spelled})

    assert active.exists()  # protected despite the different spelling and being the oldest


def test_prune_symlink_candidate_outside_dir_removes_link_not_target(prune_config, tmp_path):
    """
    [Unit] cert rotation: verifies a symlink candidate outside CONFIG_DIR has the link itself removed, not the target (ROT-10).

    Scenario:
        Given a symlink inside CONFIG_DIR pointing to a cert file outside CONFIG_DIR, plus one newer cert
        When prune_old_certificates is called with keep=1
        Then the symlink is removed (pruned) but the target file outside CONFIG_DIR is left untouched.

    Boundaries:
        - Candidate is a symlink; the target is outside CONFIG_DIR
        - unlink on the symlink path must not follow the link to the target

    On failure, first check:
        - Whether the unlink call operates on the matched glob path (the symlink) not the resolved target
        - Whether Path.resolve() is being used to decide what to delete instead of the candidate path
    """
    target = tmp_path / "outside-real-cert.pem"
    target.write_bytes(b"pem")
    os.utime(target, (1000, 1000))
    link = prune_config / "X509-cert-link.pem"
    link.symlink_to(target)  # candidate; stat() follows to target's old mtime, so it sorts oldest
    _make_cert(prune_config, "X509-cert-new.pem", 2000)  # newest, kept

    status = cert_rotation.prune_old_certificates(1, set())

    assert not link.is_symlink()  # the link itself (the candidate) was removed
    assert target.exists()  # the target outside CONFIG_DIR is untouched
    assert any("Pruned superseded" in s for s in status)


def test_prune_reports_nothing_when_within_keep(prune_config):
    """
    [Unit] cert rotation: verifies prune_old_certificates emits a no-op status line when fewer candidates than keep.

    Scenario:
        Given one cert file and keep=2
        When prune_old_certificates is called with an empty protected set
        Then no files are deleted and the status list contains exactly the "No superseded certificates" message.

    Boundaries:
        - One candidate < keep=2; nothing to prune
        - Exact status message text is asserted

    On failure, first check:
        - Whether the no-op branch returns the expected status string
        - Whether the status message text changed in a refactor
    """
    _make_cert(prune_config, "X509-cert-0.pem", 1000)

    status = cert_rotation.prune_old_certificates(2, set())

    assert status == ["No superseded certificates to prune (kept the newest 2 plus any in use)."]
