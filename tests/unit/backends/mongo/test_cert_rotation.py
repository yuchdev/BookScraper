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


def _write_settings(config_dir, settings: dict) -> None:
    (config_dir / "settings.json").write_text(json.dumps(settings))


# --------------------------------------------------------------------------- #
# resolve_months
# --------------------------------------------------------------------------- #
def test_resolve_months_defaults_to_six(monkeypatch) -> None:
    monkeypatch.delenv("ATLAS_CERT_MONTHS", raising=False)
    assert cert_rotation.resolve_months() == 6


def test_resolve_months_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_CERT_MONTHS", "12")
    assert cert_rotation.resolve_months() == 12


def test_resolve_months_argument_beats_env(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_CERT_MONTHS", "12")
    assert cert_rotation.resolve_months(3) == 3


def test_resolve_months_rejects_non_integer_env(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_CERT_MONTHS", "soon")
    with pytest.raises(cert_rotation.RotationError, match="ATLAS_CERT_MONTHS"):
        cert_rotation.resolve_months()


@pytest.mark.parametrize("months", [0, -1, 25])
def test_resolve_months_rejects_out_of_range(months) -> None:
    with pytest.raises(cert_rotation.RotationError, match="1-24"):
        cert_rotation.resolve_months(months)


@pytest.mark.parametrize("months", [1, 24])
def test_resolve_months_accepts_range_boundaries(months) -> None:
    assert cert_rotation.resolve_months(months) == months


# --------------------------------------------------------------------------- #
# atomic_write / default_output_path
# --------------------------------------------------------------------------- #
def test_atomic_write_creates_parents_and_owner_only_file(tmp_path) -> None:
    target = tmp_path / "nested" / "cert.pem"
    cert_rotation.atomic_write(target, b"PEM DATA")

    assert target.read_bytes() == b"PEM DATA"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_atomic_write_replaces_existing_file_and_leaves_no_temp_files(tmp_path) -> None:
    target = tmp_path / "cert.pem"
    target.write_bytes(b"old")

    cert_rotation.atomic_write(target, b"new")

    assert target.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["cert.pem"]


def test_default_output_path_is_timestamped_under_config_dir(isolated_config) -> None:
    path = cert_rotation.default_output_path()

    assert path.parent == isolated_config
    assert re.fullmatch(r"X509-cert-\d{8}_\d{6}\.pem", path.name)


# --------------------------------------------------------------------------- #
# update_settings_after_rotation
# --------------------------------------------------------------------------- #
def test_update_settings_no_file_reports_legacy_fallback(isolated_config) -> None:
    notes = cert_rotation.update_settings_after_rotation(isolated_config / "X509-cert-new.pem")

    assert len(notes) == 1
    assert "nothing to update" in notes[0]
    assert not (isolated_config / "settings.json").exists()


def test_update_settings_malformed_file_warns_and_is_untouched(isolated_config) -> None:
    settings_file = isolated_config / "settings.json"
    settings_file.write_text("{not json")

    notes = cert_rotation.update_settings_after_rotation(isolated_config / "X509-cert-new.pem")

    assert notes[0].startswith("WARNING")
    assert settings_file.read_text() == "{not json"


def test_update_settings_pass_auth_is_untouched(isolated_config) -> None:
    settings = {"auth_type": "pass", "pass": {"uri": {"source": "literal", "value": "mongodb+srv://x"}}}
    _write_settings(isolated_config, settings)

    notes = cert_rotation.update_settings_after_rotation(isolated_config / "X509-cert-new.pem")

    assert "not configured for x509" in notes[0]
    assert json.loads((isolated_config / "settings.json").read_text()) == settings


def test_update_settings_literal_cert_is_repointed(isolated_config) -> None:
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


def test_update_settings_env_cert_only_reminds(isolated_config) -> None:
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


def test_update_settings_unrecognized_cert_shape_is_untouched(isolated_config) -> None:
    settings = {"auth_type": "x509", "x509": {"cert": {"source": "vault", "value": "x"}}}
    _write_settings(isolated_config, settings)

    notes = cert_rotation.update_settings_after_rotation(isolated_config / "X509-cert-new.pem")

    assert "unrecognized shape" in notes[0]
    assert json.loads((isolated_config / "settings.json").read_text()) == settings


# --------------------------------------------------------------------------- #
# read_certificate_expiry
# --------------------------------------------------------------------------- #
def test_read_expiry_from_combined_key_and_cert_pem(tmp_path) -> None:
    """
    Scenario: the expiry is read out of a combined private-key + certificate PEM.
    Boundaries: key block first (rotation's own ordering); expiry returned as tz-aware UTC.
    On failure: the health check cannot tell when the active identity expires.
    """
    expiry = datetime(2027, 6, 1, 12, 0, 0, tzinfo=UTC)
    cert_file = tmp_path / "X509-cert.pem"
    cert_file.write_bytes(_combined_pem(expiry, key_first=True))

    result = cert_rotation.read_certificate_expiry(cert_file)

    assert result == expiry
    assert result.tzinfo is not None


def test_read_expiry_parses_first_cert_when_cert_precedes_key(tmp_path) -> None:
    """
    Scenario: the certificate is still found when the CERTIFICATE block precedes the key block.
    Boundaries: reversed ordering; the parser must key on the block type, not file position.
    On failure: a valid cert-first file is misread as having no certificate.
    """
    expiry = datetime(2028, 1, 15, 0, 0, 0, tzinfo=UTC)
    cert_file = tmp_path / "X509-cert.pem"
    cert_file.write_bytes(_combined_pem(expiry, key_first=False))

    assert cert_rotation.read_certificate_expiry(cert_file) == expiry


def test_read_expiry_missing_file_raises_rotation_error(tmp_path) -> None:
    """
    Scenario: a missing cert file raises RotationError naming only the path.
    Boundaries: file does not exist.
    On failure: a missing cert crashes with a raw OSError instead of a controlled message.
    """
    missing = tmp_path / "nope.pem"
    with pytest.raises(cert_rotation.RotationError, match=re.escape(str(missing))):
        cert_rotation.read_certificate_expiry(missing)


def test_read_expiry_pem_without_certificate_block_raises(tmp_path) -> None:
    """
    Scenario: a PEM that holds only a private key (no CERTIFICATE block) is rejected.
    Boundaries: valid key bytes present, but no certificate to read.
    On failure: the key block is mistaken for a certificate, or the key bytes leak.
    """
    key_only = tmp_path / "key-only.pem"
    key_only.write_bytes(_TEST_KEY_PEM)

    with pytest.raises(cert_rotation.RotationError, match="No X.509 CERTIFICATE block"):
        cert_rotation.read_certificate_expiry(key_only)


def test_read_expiry_garbage_certificate_block_raises(tmp_path) -> None:
    """
    Scenario: a file with a CERTIFICATE header but garbage body raises a parse RotationError.
    Boundaries: the regex matches a block, but cryptography cannot parse it.
    On failure: an unparseable cert crashes with a raw cryptography ValueError.
    """
    garbage = tmp_path / "garbage.pem"
    garbage.write_bytes(b"-----BEGIN CERTIFICATE-----\nnot base64 at all\n-----END CERTIFICATE-----\n")

    with pytest.raises(cert_rotation.RotationError, match="Could not parse"):
        cert_rotation.read_certificate_expiry(garbage)


def test_read_expiry_never_leaks_key_bytes_on_error(tmp_path) -> None:
    """
    Scenario: an error on a file that also contains the private key must not echo key bytes.
    Boundaries: combined key + corrupted cert; the RotationError message is inspected.
    On failure: private-key PEM lands in an exception message and then a log (CWE-532/CWE-312).
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
def test_cert_expiry_warning_none_cert_returns_none() -> None:
    """
    Scenario: no configured cert means nothing to warn about.
    Boundaries: cert_path is None.
    On failure: the pre-flight path crashes when x509 auth is not in use.
    """
    assert cert_rotation.cert_expiry_warning(None) is None


def test_cert_expiry_warning_far_expiry_returns_none(tmp_path) -> None:
    """
    Scenario: a cert well outside the warn window produces no warning.
    Boundaries: expiry ~180 days out, default 30-day window.
    On failure: healthy certs generate noise on every run.
    """
    cert_file = tmp_path / "X509-cert.pem"
    cert_file.write_bytes(_combined_pem(datetime.now(UTC) + timedelta(days=180)))

    assert cert_rotation.cert_expiry_warning(cert_file) is None


def test_cert_expiry_warning_within_window_warns(tmp_path) -> None:
    """
    Scenario: a cert inside the warn window returns a renewal warning.
    Boundaries: expiry ~10 days out, default 30-day window.
    On failure: an about-to-expire identity goes unannounced until a run fails.
    """
    cert_file = tmp_path / "X509-cert.pem"
    cert_file.write_bytes(_combined_pem(datetime.now(UTC) + timedelta(days=10)))

    warning = cert_rotation.cert_expiry_warning(cert_file)

    assert warning is not None
    assert "expires on" in warning
    assert "rotate-cert" in warning


def test_cert_expiry_warning_expired_warns(tmp_path) -> None:
    """
    Scenario: an already-expired cert returns an expired warning.
    Boundaries: expiry in the past.
    On failure: an expired identity is reported as merely 'soon', hiding an outage cause.
    """
    cert_file = tmp_path / "X509-cert.pem"
    cert_file.write_bytes(_combined_pem(datetime.now(UTC) - timedelta(days=5)))

    warning = cert_rotation.cert_expiry_warning(cert_file)

    assert warning is not None
    assert "expired on" in warning


def test_cert_expiry_warning_parse_failure_is_warning_not_raise(tmp_path) -> None:
    """
    Scenario: a parse failure returns a warning string instead of raising (ROT-17).
    Boundaries: a key-only file (no CERTIFICATE block) on the non-raising pre-flight path.
    On failure: a bad cert crashes the whole connection pre-flight instead of warning.
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
def prune_config(isolated_config):
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


def test_prune_keeps_newest_n(prune_config) -> None:
    """
    Scenario: with keep=2, only the two newest certs by mtime survive.
    Boundaries: five candidates, no protected paths; ordering is by st_mtime, newest-first.
    On failure: too many stale private keys linger, or too many are deleted.
    """
    for i in range(5):
        _make_cert(prune_config, f"X509-cert-{i}.pem", 1000 + i)

    status = cert_rotation.prune_old_certificates(2, set())

    remaining = sorted(p.name for p in prune_config.glob("X509-cert-*.pem"))
    assert remaining == ["X509-cert-3.pem", "X509-cert-4.pem"]
    assert sum("Pruned superseded" in s for s in status) == 3


def test_prune_never_deletes_protected_even_if_oldest(prune_config) -> None:
    """
    Scenario: a protected cert survives pruning however old it is (ROT-6).
    Boundaries: the oldest of five certs is in the protected set with keep=2.
    On failure: the active identity is deleted, causing a self-inflicted lockout.
    """
    certs = [_make_cert(prune_config, f"X509-cert-{i}.pem", 1000 + i) for i in range(5)]
    oldest = certs[0]

    cert_rotation.prune_old_certificates(2, {oldest})

    remaining = sorted(p.name for p in prune_config.glob("X509-cert-*.pem"))
    assert remaining == ["X509-cert-0.pem", "X509-cert-3.pem", "X509-cert-4.pem"]


def test_prune_ignores_files_outside_glob_and_dir(prune_config, tmp_path) -> None:
    """
    Scenario: only ``X509-cert-*.pem`` files inside CONFIG_DIR are candidates (ROT-9).
    Boundaries: a non-matching name, a wrong extension, and a matching-name file *outside*
    CONFIG_DIR all coexist with prunable candidates.
    On failure: a user-supplied --output cert elsewhere, or an unrelated file, is deleted.
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


def test_prune_unlink_failure_is_warning(prune_config, monkeypatch) -> None:
    """
    Scenario: a failed unlink becomes a WARNING status line, not a raised exception (ROT-10).
    Boundaries: Path.unlink patched to raise OSError; the file consequently survives.
    On failure: one undeletable cert aborts the whole rotation with an unhandled error.
    """
    _make_cert(prune_config, "X509-cert-0.pem", 1000)
    _make_cert(prune_config, "X509-cert-1.pem", 2000)

    def _boom(self, *args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(pathlib.Path, "unlink", _boom)

    status = cert_rotation.prune_old_certificates(1, set())

    assert any(s.startswith("WARNING") and "OSError" in s for s in status)
    assert (prune_config / "X509-cert-0.pem").exists()  # survived the failed unlink


@pytest.mark.parametrize("spelling", ["relative", "dotdot", "symlink"])
def test_prune_protects_active_cert_via_different_spelling(prune_config, tmp_path, monkeypatch, spelling) -> None:
    """
    Scenario: the active cert is protected even when spelled differently from the glob path (ROT-7).
    Boundaries: the same on-disk cert referenced via a relative path, a ``..`` path, or a symlink;
    it is also the oldest, so only canonical comparison saves it.
    On failure: a spelling mismatch treats the in-use cert as unprotected and deletes it.
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


def test_prune_symlink_candidate_outside_dir_removes_link_not_target(prune_config, tmp_path) -> None:
    """
    Scenario: a symlink candidate pointing outside CONFIG_DIR has the link removed, target intact.
    Boundaries: the matched glob entry is a symlink to a file outside CONFIG_DIR (ROT-10).
    On failure: resolve()-then-delete removes the external target instead of the link.
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


def test_prune_reports_nothing_when_within_keep(prune_config) -> None:
    """
    Scenario: with fewer candidates than keep, nothing is deleted and a single status line says so.
    Boundaries: one candidate, keep=2.
    On failure: a no-op prune reports a deletion, or emits no status at all.
    """
    _make_cert(prune_config, "X509-cert-0.pem", 1000)

    status = cert_rotation.prune_old_certificates(2, set())

    assert status == ["No superseded certificates to prune (kept the newest 2 plus any in use)."]
