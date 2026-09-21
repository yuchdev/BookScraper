"""Unit tests for bookscraper.backends.mongo.config, the deterministic auth resolver.

This module is fully pure (env vars + a JSON file + Path.glob), so no mocking
library is needed - just monkeypatch.setenv / monkeypatch.setattr(config, "CONFIG_DIR", ...)
and tmp_path. The resolver's contract is "a bad config fails loudly with ConfigError,
never a silent guess", so every malformed shape is asserted to raise, and all four
x509 source combinations are asserted to resolve to identical values.
"""

import json
import os
import time

import pytest

from bookscraper.backends.mongo import config
from bookscraper.backends.mongo.config import ConfigError

URI = "mongodb+srv://u:p@cluster/db?retryWrites=true&w=majority"


def _write_settings(path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


class TestResolveField:
    def test_literal_returns_value_as_is(self) -> None:
        assert config._resolve_field({"source": "literal", "value": URI}, "f") == URI

    def test_env_returns_looked_up_value(self, monkeypatch) -> None:
        monkeypatch.setenv("SOME_VAR", URI)
        assert config._resolve_field({"source": "env", "value": "SOME_VAR"}, "f") == URI

    def test_env_unset_raises(self, monkeypatch) -> None:
        monkeypatch.delenv("MISSING_VAR", raising=False)
        with pytest.raises(ConfigError):
            config._resolve_field({"source": "env", "value": "MISSING_VAR"}, "f")

    def test_missing_source_raises(self) -> None:
        with pytest.raises(ConfigError):
            config._resolve_field({"value": URI}, "f")

    def test_missing_value_raises(self) -> None:
        with pytest.raises(ConfigError):
            config._resolve_field({"source": "literal"}, "f")

    def test_not_a_dict_raises(self) -> None:
        with pytest.raises(ConfigError):
            config._resolve_field("just a string", "f")

    def test_unrecognized_source_raises(self) -> None:
        with pytest.raises(ConfigError):
            config._resolve_field({"source": "vault", "value": "x"}, "f")


class TestResolveCertField:
    def test_bare_filename_resolved_under_config_dir(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        resolved = config._resolve_cert_field({"source": "literal", "value": "cert.pem"}, "x509.cert")
        assert resolved == str(tmp_path / "cert.pem")

    def test_absolute_path_returned_untouched(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        abs_path = str(tmp_path / "elsewhere" / "cert.pem")
        resolved = config._resolve_cert_field({"source": "literal", "value": abs_path}, "x509.cert")
        assert resolved == abs_path


class TestGetDefaultTlsCertFile:
    def test_no_certs_returns_none(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        assert config.get_default_tls_cert_file() is None

    def test_returns_newest_by_mtime(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        old = tmp_path / "X509-cert-20200101_000000.pem"
        new = tmp_path / "X509-cert-20260101_000000.pem"
        old.write_text("old", encoding="utf-8")
        new.write_text("new", encoding="utf-8")
        # Force deterministic ordering regardless of filesystem write timing.
        os.utime(old, (1_000_000_000, 1_000_000_000))
        os.utime(new, (2_000_000_000, 2_000_000_000))
        assert config.get_default_tls_cert_file() == str(new)

    def test_ignores_non_matching_files(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
        assert config.get_default_tls_cert_file() is None


class TestLoadSettings:
    def test_nonexistent_path_returns_none(self, tmp_path) -> None:
        assert config.load_settings(tmp_path / "does_not_exist.json") is None

    def test_malformed_json_raises(self, tmp_path) -> None:
        bad = tmp_path / "settings.json"
        bad.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ConfigError):
            config.load_settings(bad)

    def test_valid_json_returns_parsed_dict(self, tmp_path) -> None:
        good = tmp_path / "settings.json"
        _write_settings(good, {"auth_type": "pass"})
        assert config.load_settings(good) == {"auth_type": "pass"}


class TestResolveMongoConnectionSettings:
    def test_pass_literal_uri(self, tmp_path) -> None:
        path = tmp_path / "settings.json"
        _write_settings(
            path,
            {"auth_type": "pass", "pass": {"uri": {"source": "literal", "value": URI}}},
        )
        assert config.resolve_mongo_connection(path) == (URI, None)

    def test_pass_env_uri(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("MONGODB_URI", URI)
        path = tmp_path / "settings.json"
        _write_settings(
            path,
            {
                "auth_type": "pass",
                "pass": {"uri": {"source": "env", "value": "MONGODB_URI"}},
            },
        )
        assert config.resolve_mongo_connection(path) == (URI, None)

    def test_x509_literal_uri_literal_bare_cert(self, monkeypatch, tmp_path) -> None:
        config_dir = tmp_path / ".bookscrapper"
        config_dir.mkdir()
        monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
        path = tmp_path / "settings.json"
        _write_settings(
            path,
            {
                "auth_type": "x509",
                "x509": {
                    "uri": {"source": "literal", "value": URI},
                    "cert": {"source": "literal", "value": "cert.pem"},
                },
            },
        )
        uri, cert = config.resolve_mongo_connection(path)
        assert uri == URI
        assert cert == str(config_dir / "cert.pem")

    def test_all_four_x509_source_combos_resolve_identically(self, monkeypatch, tmp_path) -> None:
        config_dir = tmp_path / ".bookscrapper"
        config_dir.mkdir()
        monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
        monkeypatch.setenv("MONGODB_URI", URI)
        cert_filename = "cert.pem"
        monkeypatch.setenv("TLS_CERT_FILE", cert_filename)
        expected_cert = str(config_dir / cert_filename)

        uri_specs = {
            "literal": {"source": "literal", "value": URI},
            "env": {"source": "env", "value": "MONGODB_URI"},
        }
        cert_specs = {
            "literal": {"source": "literal", "value": cert_filename},
            "env": {"source": "env", "value": "TLS_CERT_FILE"},
        }

        results = []
        for uri_src in ("literal", "env"):
            for cert_src in ("literal", "env"):
                path = tmp_path / f"settings_{uri_src}_{cert_src}.json"
                _write_settings(
                    path,
                    {
                        "auth_type": "x509",
                        "x509": {
                            "uri": uri_specs[uri_src],
                            "cert": cert_specs[cert_src],
                        },
                    },
                )
                results.append(config.resolve_mongo_connection(path))

        assert results == [(URI, expected_cert)] * 4

    def test_missing_auth_type_raises(self, tmp_path) -> None:
        path = tmp_path / "settings.json"
        _write_settings(path, {"pass": {"uri": {"source": "literal", "value": URI}}})
        with pytest.raises(ConfigError):
            config.resolve_mongo_connection(path)

    def test_unrecognized_auth_type_raises(self, tmp_path) -> None:
        path = tmp_path / "settings.json"
        _write_settings(path, {"auth_type": "kerberos"})
        with pytest.raises(ConfigError):
            config.resolve_mongo_connection(path)

    def test_pass_without_pass_block_raises(self, tmp_path) -> None:
        path = tmp_path / "settings.json"
        _write_settings(path, {"auth_type": "pass"})
        with pytest.raises(ConfigError):
            config.resolve_mongo_connection(path)

    def test_x509_without_x509_block_raises(self, tmp_path) -> None:
        path = tmp_path / "settings.json"
        _write_settings(path, {"auth_type": "x509"})
        with pytest.raises(ConfigError):
            config.resolve_mongo_connection(path)


class TestResolveMongoConnectionLegacyFallback:
    def test_env_uri_no_cert(self, monkeypatch, tmp_path, no_real_env) -> None:
        config_dir = tmp_path / ".bookscrapper"
        config_dir.mkdir()
        monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
        monkeypatch.setenv("MONGODB_URI", URI)
        # nonexistent settings path -> legacy fallback
        result = config.resolve_mongo_connection(config_dir / "settings.json")
        assert result == (URI, None)

    def test_cert_glob_used_when_no_tls_cert_env(self, monkeypatch, tmp_path, no_real_env) -> None:
        config_dir = tmp_path / ".bookscrapper"
        config_dir.mkdir()
        monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
        monkeypatch.setenv("MONGODB_URI", URI)
        cert = config_dir / "X509-cert-20260101_000000.pem"
        cert.write_text("cert", encoding="utf-8")
        uri, resolved_cert = config.resolve_mongo_connection(config_dir / "settings.json")
        assert uri == URI
        assert resolved_cert == str(cert)

    def test_explicit_tls_cert_env_wins_over_glob(self, monkeypatch, tmp_path, no_real_env) -> None:
        config_dir = tmp_path / ".bookscrapper"
        config_dir.mkdir()
        monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
        monkeypatch.setenv("MONGODB_URI", URI)
        glob_cert = config_dir / "X509-cert-20260101_000000.pem"
        glob_cert.write_text("cert", encoding="utf-8")
        time.sleep(0.01)
        explicit = str(tmp_path / "explicit-cert.pem")
        monkeypatch.setenv("TLS_CERT_FILE", explicit)
        uri, resolved_cert = config.resolve_mongo_connection(config_dir / "settings.json")
        assert uri == URI
        assert resolved_cert == explicit
