"""Unit tests for bookscraper.backends.mongo.cert_rotation's pure/filesystem logic.

Real file I/O via tmp_path (through the isolated_config fixture, so the real
~/.bookscrapper/settings.json is never read or written); no network.
"""

import json
import re
import stat

import pytest

from bookscraper.backends.mongo import cert_rotation


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
