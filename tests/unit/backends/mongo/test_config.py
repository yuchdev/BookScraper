"""Unit tests for bookscraper.backends.mongo.config, the deterministic auth resolver.

This module is fully pure (env vars + a JSON file + Path.glob), so no mocking
library is needed - just monkeypatch.setenv / monkeypatch.setattr(config, "CONFIG_DIR", ...)
and tmp_path. The resolver's contract is "a bad config fails loudly with ConfigError,
never a silent guess", so every malformed shape is asserted to raise, and all four
x509 source combinations are asserted to resolve to identical values.
"""

import json
import logging
import os
import time
from pathlib import Path

import pytest

from bookscraper.backends.mongo import config
from bookscraper.backends.mongo.config import ConfigError

CONFIG_LOGGER = "bookscraper.backends.mongo.config"

URI = "mongodb+srv://u:p@cluster/db?retryWrites=true&w=majority"


def _write_settings(path, data) -> None:
    """Write a settings dict as JSON to ``path``."""
    path.write_text(json.dumps(data), encoding="utf-8")


class TestResolveField:
    """Tests for config._resolve_field(): literal vs env-var source resolution."""

    def test_literal_returns_value_as_is(self) -> None:
        """
        [Unit] resolve field: verifies literal returns value as is.

        Scenario:
            Given test inputs and fixtures for literal returns value as is
            When test_literal_returns_value_as_is executes the target flow
            Then the expected outcome for literal returns value as is is confirmed.

        Boundaries:
            - Focus: literal returns value as is
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to literal returns value as is
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve field
        """
        assert config._resolve_field({"source": "literal", "value": URI}, "f") == URI

    def test_env_returns_looked_up_value(self, monkeypatch) -> None:
        """
        [Local] resolve field: verifies env returns looked up value.

        Scenario:
            Given test inputs and fixtures for env returns looked up value
            When test_env_returns_looked_up_value executes the target flow
            Then the expected outcome for env returns looked up value is confirmed.

        Boundaries:
            - Focus: env returns looked up value
            - Fixtures/params: monkeypatch
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to env returns looked up value
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve field
        """
        monkeypatch.setenv("SOME_VAR", URI)
        assert config._resolve_field({"source": "env", "value": "SOME_VAR"}, "f") == URI

    def test_env_unset_raises(self, monkeypatch) -> None:
        """
        [Local] resolve field: verifies env unset raises.

        Scenario:
            Given test inputs and fixtures for env unset raises
            When test_env_unset_raises executes the target flow
            Then the expected outcome for env unset raises is confirmed.

        Boundaries:
            - Focus: env unset raises
            - Fixtures/params: monkeypatch
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to env unset raises
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve field
        """
        monkeypatch.delenv("MISSING_VAR", raising=False)
        with pytest.raises(ConfigError):
            config._resolve_field({"source": "env", "value": "MISSING_VAR"}, "f")

    def test_missing_source_raises(self) -> None:
        """
        [Unit] resolve field: verifies missing source raises.

        Scenario:
            Given test inputs and fixtures for missing source raises
            When test_missing_source_raises executes the target flow
            Then the expected outcome for missing source raises is confirmed.

        Boundaries:
            - Focus: missing source raises
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to missing source raises
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve field
        """
        with pytest.raises(ConfigError):
            config._resolve_field({"value": URI}, "f")

    def test_missing_value_raises(self) -> None:
        """
        [Unit] resolve field: verifies missing value raises.

        Scenario:
            Given test inputs and fixtures for missing value raises
            When test_missing_value_raises executes the target flow
            Then the expected outcome for missing value raises is confirmed.

        Boundaries:
            - Focus: missing value raises
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to missing value raises
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve field
        """
        with pytest.raises(ConfigError):
            config._resolve_field({"source": "literal"}, "f")

    def test_not_a_dict_raises(self) -> None:
        """
        [Unit] resolve field: verifies not a dict raises.

        Scenario:
            Given test inputs and fixtures for not a dict raises
            When test_not_a_dict_raises executes the target flow
            Then the expected outcome for not a dict raises is confirmed.

        Boundaries:
            - Focus: not a dict raises
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to not a dict raises
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve field
        """
        with pytest.raises(ConfigError):
            config._resolve_field("just a string", "f")

    def test_unrecognized_source_raises(self) -> None:
        """
        [Unit] resolve field: verifies unrecognized source raises.

        Scenario:
            Given test inputs and fixtures for unrecognized source raises
            When test_unrecognized_source_raises executes the target flow
            Then the expected outcome for unrecognized source raises is confirmed.

        Boundaries:
            - Focus: unrecognized source raises
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to unrecognized source raises
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve field
        """
        with pytest.raises(ConfigError):
            config._resolve_field({"source": "vault", "value": "x"}, "f")


class TestResolveCertField:
    """Tests for config._resolve_cert_field(): resolves a cert path literal or env var relative to CONFIG_DIR."""

    def test_bare_filename_resolved_under_config_dir(self, monkeypatch, tmp_path) -> None:
        """
        [Local] resolve cert field: verifies bare filename resolved under config dir.

        Scenario:
            Given test inputs and fixtures for bare filename resolved under config dir
            When test_bare_filename_resolved_under_config_dir executes the target flow
            Then the expected outcome for bare filename resolved under config dir is confirmed.

        Boundaries:
            - Focus: bare filename resolved under config dir
            - Fixtures/params: monkeypatch, tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to bare filename resolved under config dir
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve cert field
        """
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        resolved = config._resolve_cert_field({"source": "literal", "value": "cert.pem"}, "x509.cert")
        assert resolved == str(tmp_path / "cert.pem")

    def test_absolute_path_returned_untouched(self, monkeypatch, tmp_path) -> None:
        """
        [Local] resolve cert field: verifies absolute path returned untouched.

        Scenario:
            Given test inputs and fixtures for absolute path returned untouched
            When test_absolute_path_returned_untouched executes the target flow
            Then the expected outcome for absolute path returned untouched is confirmed.

        Boundaries:
            - Focus: absolute path returned untouched
            - Fixtures/params: monkeypatch, tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to absolute path returned untouched
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve cert field
        """
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        abs_path = str(tmp_path / "elsewhere" / "cert.pem")
        resolved = config._resolve_cert_field({"source": "literal", "value": abs_path}, "x509.cert")
        assert resolved == abs_path


class TestGetDefaultTlsCertFile:
    """Tests for config.get_default_tls_cert_file(): returns the newest X509-cert-*.pem by mtime."""

    def test_no_certs_returns_none(self, monkeypatch, tmp_path) -> None:
        """
        [Local] get default tls cert file: verifies no certs returns none.

        Scenario:
            Given test inputs and fixtures for no certs returns none
            When test_no_certs_returns_none executes the target flow
            Then the expected outcome for no certs returns none is confirmed.

        Boundaries:
            - Focus: no certs returns none
            - Fixtures/params: monkeypatch, tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to no certs returns none
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by get default tls cert file
        """
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        assert config.get_default_tls_cert_file() is None

    def test_returns_newest_by_mtime(self, monkeypatch, tmp_path) -> None:
        """
        [Local] get default tls cert file: verifies returns newest by mtime.

        Scenario:
            Given test inputs and fixtures for returns newest by mtime
            When test_returns_newest_by_mtime executes the target flow
            Then the expected outcome for returns newest by mtime is confirmed.

        Boundaries:
            - Focus: returns newest by mtime
            - Fixtures/params: monkeypatch, tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to returns newest by mtime
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by get default tls cert file
        """
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
        """
        [Local] get default tls cert file: verifies ignores non matching files.

        Scenario:
            Given test inputs and fixtures for ignores non matching files
            When test_ignores_non_matching_files executes the target flow
            Then the expected outcome for ignores non matching files is confirmed.

        Boundaries:
            - Focus: ignores non matching files
            - Fixtures/params: monkeypatch, tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to ignores non matching files
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by get default tls cert file
        """
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
        assert config.get_default_tls_cert_file() is None


class TestLoadSettings:
    """Tests for config.load_settings(): reads and parses settings.json, returns None when absent."""

    def test_nonexistent_path_returns_none(self, tmp_path) -> None:
        """
        [Unit] load settings: verifies nonexistent path returns none.

        Scenario:
            Given test inputs and fixtures for nonexistent path returns none
            When test_nonexistent_path_returns_none executes the target flow
            Then the expected outcome for nonexistent path returns none is confirmed.

        Boundaries:
            - Focus: nonexistent path returns none
            - Fixtures/params: tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to nonexistent path returns none
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by load settings
        """
        assert config.load_settings(tmp_path / "does_not_exist.json") is None

    def test_malformed_json_raises(self, tmp_path) -> None:
        """
        [Unit] load settings: verifies malformed json raises.

        Scenario:
            Given test inputs and fixtures for malformed json raises
            When test_malformed_json_raises executes the target flow
            Then the expected outcome for malformed json raises is confirmed.

        Boundaries:
            - Focus: malformed json raises
            - Fixtures/params: tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to malformed json raises
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by load settings
        """
        bad = tmp_path / "settings.json"
        bad.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ConfigError):
            config.load_settings(bad)

    def test_valid_json_returns_parsed_dict(self, tmp_path) -> None:
        """
        [Unit] load settings: verifies valid json returns parsed dict.

        Scenario:
            Given test inputs and fixtures for valid json returns parsed dict
            When test_valid_json_returns_parsed_dict executes the target flow
            Then the expected outcome for valid json returns parsed dict is confirmed.

        Boundaries:
            - Focus: valid json returns parsed dict
            - Fixtures/params: tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to valid json returns parsed dict
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by load settings
        """
        good = tmp_path / "settings.json"
        _write_settings(good, {"auth_type": "pass"})
        assert config.load_settings(good) == {"auth_type": "pass"}


class TestResolveMongoConnectionSettings:
    def test_pass_literal_uri(self, tmp_path) -> None:
        """
        [Unit] resolve mongo connection settings: verifies pass literal uri.

        Scenario:
            Given test inputs and fixtures for pass literal uri
            When test_pass_literal_uri executes the target flow
            Then the expected outcome for pass literal uri is confirmed.

        Boundaries:
            - Focus: pass literal uri
            - Fixtures/params: tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to pass literal uri
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve mongo connection settings
        """
        path = tmp_path / "settings.json"
        _write_settings(
            path,
            {"auth_type": "pass", "pass": {"uri": {"source": "literal", "value": URI}}},
        )
        assert config.resolve_mongo_connection(path) == (URI, None)

    def test_pass_env_uri(self, monkeypatch, tmp_path) -> None:
        """
        [Local] resolve mongo connection settings: verifies pass env uri.

        Scenario:
            Given test inputs and fixtures for pass env uri
            When test_pass_env_uri executes the target flow
            Then the expected outcome for pass env uri is confirmed.

        Boundaries:
            - Focus: pass env uri
            - Fixtures/params: monkeypatch, tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to pass env uri
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve mongo connection settings
        """
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
        """
        [Local] resolve mongo connection settings: verifies x509 literal uri literal bare cert.

        Scenario:
            Given test inputs and fixtures for x509 literal uri literal bare cert
            When test_x509_literal_uri_literal_bare_cert executes the target flow
            Then the expected outcome for x509 literal uri literal bare cert is confirmed.

        Boundaries:
            - Focus: x509 literal uri literal bare cert
            - Fixtures/params: monkeypatch, tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to x509 literal uri literal bare cert
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve mongo connection settings
        """
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
        """
        [Local] resolve mongo connection settings: verifies all four x509 source combos resolve identically.

        Scenario:
            Given test inputs and fixtures for all four x509 source combos resolve identically
            When test_all_four_x509_source_combos_resolve_identically executes the target flow
            Then the expected outcome for all four x509 source combos resolve identically is confirmed.

        Boundaries:
            - Focus: all four x509 source combos resolve identically
            - Fixtures/params: monkeypatch, tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to all four x509 source combos resolve identically
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve mongo connection settings
        """
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
        """
        [Unit] resolve mongo connection settings: verifies missing auth type raises.

        Scenario:
            Given test inputs and fixtures for missing auth type raises
            When test_missing_auth_type_raises executes the target flow
            Then the expected outcome for missing auth type raises is confirmed.

        Boundaries:
            - Focus: missing auth type raises
            - Fixtures/params: tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to missing auth type raises
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve mongo connection settings
        """
        path = tmp_path / "settings.json"
        _write_settings(path, {"pass": {"uri": {"source": "literal", "value": URI}}})
        with pytest.raises(ConfigError):
            config.resolve_mongo_connection(path)

    def test_unrecognized_auth_type_raises(self, tmp_path) -> None:
        """
        [Unit] resolve mongo connection settings: verifies unrecognized auth type raises.

        Scenario:
            Given test inputs and fixtures for unrecognized auth type raises
            When test_unrecognized_auth_type_raises executes the target flow
            Then the expected outcome for unrecognized auth type raises is confirmed.

        Boundaries:
            - Focus: unrecognized auth type raises
            - Fixtures/params: tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to unrecognized auth type raises
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve mongo connection settings
        """
        path = tmp_path / "settings.json"
        _write_settings(path, {"auth_type": "kerberos"})
        with pytest.raises(ConfigError):
            config.resolve_mongo_connection(path)

    def test_pass_without_pass_block_raises(self, tmp_path) -> None:
        """
        [Unit] resolve mongo connection settings: verifies pass without pass block raises.

        Scenario:
            Given test inputs and fixtures for pass without pass block raises
            When test_pass_without_pass_block_raises executes the target flow
            Then the expected outcome for pass without pass block raises is confirmed.

        Boundaries:
            - Focus: pass without pass block raises
            - Fixtures/params: tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to pass without pass block raises
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve mongo connection settings
        """
        path = tmp_path / "settings.json"
        _write_settings(path, {"auth_type": "pass"})
        with pytest.raises(ConfigError):
            config.resolve_mongo_connection(path)

    def test_x509_without_x509_block_raises(self, tmp_path) -> None:
        """
        [Unit] resolve mongo connection settings: verifies x509 without x509 block raises.

        Scenario:
            Given test inputs and fixtures for x509 without x509 block raises
            When test_x509_without_x509_block_raises executes the target flow
            Then the expected outcome for x509 without x509 block raises is confirmed.

        Boundaries:
            - Focus: x509 without x509 block raises
            - Fixtures/params: tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to x509 without x509 block raises
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve mongo connection settings
        """
        path = tmp_path / "settings.json"
        _write_settings(path, {"auth_type": "x509"})
        with pytest.raises(ConfigError):
            config.resolve_mongo_connection(path)


class TestResolveMongoConnectionLegacyFallback:
    def test_env_uri_no_cert(self, monkeypatch, tmp_path, no_real_env) -> None:
        """
        [Local] resolve mongo connection legacy fallback: verifies env uri no cert.

        Scenario:
            Given test inputs and fixtures for env uri no cert
            When test_env_uri_no_cert executes the target flow
            Then the expected outcome for env uri no cert is confirmed.

        Boundaries:
            - Focus: env uri no cert
            - Fixtures/params: monkeypatch, tmp_path, no_real_env
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to env uri no cert
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve mongo connection legacy fallback
        """
        config_dir = tmp_path / ".bookscrapper"
        config_dir.mkdir()
        monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
        monkeypatch.setenv("MONGODB_URI", URI)
        # nonexistent settings path -> legacy fallback
        result = config.resolve_mongo_connection(config_dir / "settings.json")
        assert result == (URI, None)

    def test_cert_glob_used_when_no_tls_cert_env(self, monkeypatch, tmp_path, no_real_env) -> None:
        """
        [Local] resolve mongo connection legacy fallback: verifies cert glob used when no tls cert env.

        Scenario:
            Given test inputs and fixtures for cert glob used when no tls cert env
            When test_cert_glob_used_when_no_tls_cert_env executes the target flow
            Then the expected outcome for cert glob used when no tls cert env is confirmed.

        Boundaries:
            - Focus: cert glob used when no tls cert env
            - Fixtures/params: monkeypatch, tmp_path, no_real_env
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to cert glob used when no tls cert env
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve mongo connection legacy fallback
        """
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
        """
        [Local] resolve mongo connection legacy fallback: verifies explicit tls cert env wins over glob.

        Scenario:
            Given test inputs and fixtures for explicit tls cert env wins over glob
            When test_explicit_tls_cert_env_wins_over_glob executes the target flow
            Then the expected outcome for explicit tls cert env wins over glob is confirmed.

        Boundaries:
            - Focus: explicit tls cert env wins over glob
            - Fixtures/params: monkeypatch, tmp_path, no_real_env
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to explicit tls cert env wins over glob
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by resolve mongo connection legacy fallback
        """
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


def _pass_settings() -> dict:
    """Return a minimal password-auth settings dict for tests that need a valid settings.json."""
    return {"auth_type": "pass", "pass": {"uri": {"source": "literal", "value": URI}}}


class TestSettingsFileSecurity:
    """POSIX advisory warnings on settings.json (SEC-9): warnings, never errors."""

    @pytest.fixture(autouse=True)
    def _clear_guard(self):
        """Reset the per-process security-warning deduplication set before and after each test."""
        config._SECURITY_WARNINGS_EMITTED.clear()
        yield
        config._SECURITY_WARNINGS_EMITTED.clear()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
    def test_world_readable_settings_warns_once(self, tmp_path, caplog) -> None:
        """
        Scenario: a group/other-readable settings.json warns exactly once no matter how many
        times it is loaded in one process.
        Boundaries: mode 0o644, loaded twice; a single warning naming the file and the fix.
        On failure: the warning is missing, repeats per load, or omits the chmod fix.
        """
        path = tmp_path / "settings.json"
        _write_settings(path, _pass_settings())
        os.chmod(path, 0o644)
        with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
            config.load_settings(path)
            config.load_settings(path)
        warnings = [r for r in caplog.records if "readable by group/other" in r.message]
        assert len(warnings) == 1
        assert str(path) in warnings[0].message
        assert "chmod 600" in warnings[0].message

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
    def test_security_warning_reaches_console(self, tmp_path, capsys) -> None:
        """
        Scenario: the settings.json warning must be visible on the console, not only in the
        run log - the root logger has just a file handler, so a logger-only warning is unseen.
        Boundaries: mode 0o644, one load; stdout carries the warning text.
        On failure: the user is never told their credentials file is group/other-readable.
        """
        path = tmp_path / "settings.json"
        _write_settings(path, _pass_settings())
        os.chmod(path, 0o644)
        config.load_settings(path)
        assert "readable by group/other" in capsys.readouterr().out

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
    def test_owner_only_settings_does_not_warn(self, tmp_path, caplog) -> None:
        """
        Scenario: an owner-only, non-symlinked settings.json owned by the current user loads
        with no security warning.
        Boundaries: mode 0o600, real owner, regular file.
        On failure: a false-positive warning fires on a correctly locked-down file.
        """
        path = tmp_path / "settings.json"
        _write_settings(path, _pass_settings())
        os.chmod(path, 0o600)
        with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
            config.load_settings(path)
        assert caplog.records == []

    def test_permission_check_skipped_on_windows(self, tmp_path, caplog, monkeypatch) -> None:
        """
        Scenario: on Windows the POSIX permission/ownership check is skipped entirely.
        Boundaries: os.name forced to 'nt' with a world-readable file present.
        On failure: a POSIX-only check runs on Windows and warns spuriously.
        """
        monkeypatch.setattr(config.os, "name", "nt")
        path = tmp_path / "settings.json"
        _write_settings(path, _pass_settings())
        os.chmod(path, 0o644)
        with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
            config.load_settings(path)
        assert caplog.records == []

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
    def test_symlinked_settings_warns(self, tmp_path, caplog) -> None:
        """
        Scenario: a settings.json that is a symlink warns that its target is trusted, since
        an attacker-controlled target would redirect the connection.
        Boundaries: symlink -> owner-only regular target.
        On failure: the symlink slips through and its target is trusted silently.
        """
        target = tmp_path / "real.json"
        _write_settings(target, _pass_settings())
        os.chmod(target, 0o600)
        link = tmp_path / "settings.json"
        link.symlink_to(target)
        with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
            config.load_settings(link)
        assert any("is a symlink" in r.message for r in caplog.records)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX ownership semantics")
    def test_foreign_owned_settings_warns(self, tmp_path, caplog, monkeypatch) -> None:
        """
        Scenario: a settings.json owned by another UID warns it may have been planted.
        Boundaries: os.getuid patched so it never equals the file's owner.
        On failure: a foreign-owned config is trusted without warning.
        """
        path = tmp_path / "settings.json"
        _write_settings(path, _pass_settings())
        os.chmod(path, 0o600)
        monkeypatch.setattr(config.os, "getuid", lambda: os.stat(path).st_uid + 1)
        with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
            config.load_settings(path)
        assert any("not owned by the current user" in r.message for r in caplog.records)


class TestUnknownTopLevelKeys:
    def test_unknown_top_level_key_raises(self, tmp_path) -> None:
        """
        Scenario: a typo'd top-level key (e.g. 'x059' for 'x509') fails loudly instead of
        being silently ignored.
        Boundaries: an otherwise-valid file carrying one unrecognized key.
        On failure: the typo is dropped and the config resolves with the wrong shape.
        """
        path = tmp_path / "settings.json"
        _write_settings(path, {"auth_type": "x509", "x059": {"uri": {}}})
        with pytest.raises(ConfigError) as exc:
            config.load_settings(path)
        assert "x059" in str(exc.value)

    def test_unknown_key_error_lists_only_key_names_not_values(self, tmp_path) -> None:
        """
        Scenario (SEC-11): the unknown-key error names the offending key but never echoes its
        (possibly secret) value.
        Boundaries: an unknown block whose value embeds a connection string with a password.
        On failure: the value leaks into the ConfigError -> log -> console path.
        """
        leaky_uri = "mongodb+srv://user:hunter2placeholder@cluster/db"
        path = tmp_path / "settings.json"
        _write_settings(path, {"auth_type": "pass", "bogus": {"uri": leaky_uri}})
        with pytest.raises(ConfigError) as exc:
            config.load_settings(path)
        message = str(exc.value)
        assert "bogus" in message
        assert "hunter2placeholder" not in message
        assert leaky_uri not in message


class TestResolveMongoNamespace:
    def test_database_block_resolves_name_and_collection(self, monkeypatch, tmp_path) -> None:
        """
        Scenario: a settings.json 'database' block resolves both names and the env vars are
        ignored (determinism).
        Boundaries: literal-sourced block present while MONGODB_DB_NAME/COLLECTION differ.
        On failure: the env-var namespace leaks in despite settings.json being present.
        """
        monkeypatch.setenv("MONGODB_DB_NAME", "env_db")
        monkeypatch.setenv("MONGODB_COLLECTION_NAME", "env_coll")
        path = tmp_path / "settings.json"
        _write_settings(
            path,
            {
                "auth_type": "pass",
                "pass": {"uri": {"source": "literal", "value": URI}},
                "database": {
                    "name": {"source": "literal", "value": "custom_db"},
                    "collection": {"source": "literal", "value": "custom_books"},
                },
            },
        )
        assert config.resolve_mongo_namespace(path) == ("custom_db", "custom_books")

    def test_database_block_env_sourced(self, monkeypatch, tmp_path) -> None:
        """
        Scenario: a 'database' block field with source 'env' is looked up like any other field.
        Boundaries: name via env, collection literal.
        On failure: env-sourced namespace fields are not resolved.
        """
        monkeypatch.setenv("DB_NAME_VAR", "resolved_db")
        path = tmp_path / "settings.json"
        _write_settings(
            path,
            {
                "auth_type": "pass",
                "database": {
                    "name": {"source": "env", "value": "DB_NAME_VAR"},
                    "collection": {"source": "literal", "value": "books"},
                },
            },
        )
        assert config.resolve_mongo_namespace(path) == ("resolved_db", "books")

    def test_settings_without_database_block_uses_defaults_and_ignores_env(self, monkeypatch, tmp_path) -> None:
        """
        Scenario: settings.json without a 'database' block uses the defaults and does NOT
        consult MONGODB_DB_NAME / MONGODB_COLLECTION_NAME.
        Boundaries: no block present while both env vars are set to non-defaults.
        On failure: env vars leak in, breaking settings.json's sole-source-of-truth promise.
        """
        monkeypatch.setenv("MONGODB_DB_NAME", "env_db")
        monkeypatch.setenv("MONGODB_COLLECTION_NAME", "env_coll")
        path = tmp_path / "settings.json"
        _write_settings(path, _pass_settings())
        assert config.resolve_mongo_namespace(path) == (config.DEFAULT_DB_NAME, config.DEFAULT_COLLECTION_NAME)

    def test_database_block_missing_field_raises(self, tmp_path) -> None:
        """
        Scenario: a 'database' block missing 'collection' fails loudly rather than half-
        defaulting.
        Boundaries: block with only 'name'.
        On failure: a partial block silently resolves with a defaulted field.
        """
        path = tmp_path / "settings.json"
        _write_settings(
            path,
            {"auth_type": "pass", "database": {"name": {"source": "literal", "value": "db"}}},
        )
        with pytest.raises(ConfigError):
            config.resolve_mongo_namespace(path)

    @pytest.mark.parametrize("block", [{}, "bookscraper_db", []])
    def test_database_block_empty_or_malformed_raises(self, tmp_path, block) -> None:
        """
        Scenario: a 'database' key that is present but empty or not an object fails loudly -
        present means "I'm configuring this", so silently using defaults would be a guess.
        Boundaries: empty dict, a bare string, an empty list.
        On failure: the typo'd/empty block resolves to bookscraper_db.books without a word.
        """
        path = tmp_path / "settings.json"
        _write_settings(path, {"auth_type": "pass", "database": block})
        with pytest.raises(ConfigError):
            config.resolve_mongo_namespace(path)

    def test_legacy_mode_honors_db_name_env_vars(self, monkeypatch, tmp_path, no_real_env) -> None:
        """
        Scenario: with no settings.json, MONGODB_DB_NAME / MONGODB_COLLECTION_NAME still drive
        the namespace (backward compatibility).
        Boundaries: nonexistent settings path, both env vars set.
        On failure: legacy env-var configuration stops working.
        """
        monkeypatch.setenv("MONGODB_DB_NAME", "legacy_db")
        monkeypatch.setenv("MONGODB_COLLECTION_NAME", "legacy_coll")
        assert config.resolve_mongo_namespace(tmp_path / "nope.json") == ("legacy_db", "legacy_coll")

    def test_legacy_mode_defaults_when_env_unset(self, monkeypatch, tmp_path, no_real_env) -> None:
        """
        Scenario: with no settings.json and no env vars, the defaults apply.
        Boundaries: nonexistent settings path, both env vars unset.
        On failure: a missing namespace crashes instead of defaulting.
        """
        monkeypatch.delenv("MONGODB_DB_NAME", raising=False)
        monkeypatch.delenv("MONGODB_COLLECTION_NAME", raising=False)
        assert config.resolve_mongo_namespace(tmp_path / "nope.json") == (
            config.DEFAULT_DB_NAME,
            config.DEFAULT_COLLECTION_NAME,
        )


class TestExampleSettingsFile:
    def test_example_settings_file_is_valid_for_resolver(self, isolated_config, monkeypatch) -> None:
        """
        Scenario: the shipped docs/examples/settings.example.json resolves cleanly once its
        referenced env var is set, so users can copy it verbatim.
        Boundaries: MONGODB_URI set; the example's cert is a literal filename under CONFIG_DIR.
        On failure: the example drifts from the resolver's accepted shape.
        """
        example = Path(__file__).resolve().parents[4] / "docs" / "examples" / "settings.example.json"
        monkeypatch.setenv("MONGODB_URI", URI)
        uri, cert = config.resolve_mongo_connection(example)
        assert uri == URI
        assert cert.endswith("X509-cert-example.pem")
        assert config.resolve_mongo_namespace(example) == ("bookscraper_db", "books")
