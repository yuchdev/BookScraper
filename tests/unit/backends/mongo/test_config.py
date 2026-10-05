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
        [Unit] resolve field: verifies a literal source field returns its value without modification.

        Scenario:
            Given a field dict with source "literal" and a URI string as value
            When _resolve_field is called
            Then the URI string is returned unchanged

        Boundaries:
            - No env vars consulted; only the "value" key is read
            - No filesystem or network access

        On failure, first check:
            - Whether _resolve_field applies any transformation to literal values
            - Recent changes to the "literal" branch in _resolve_field
        """
        assert config._resolve_field({"source": "literal", "value": URI}, "f") == URI

    def test_env_returns_looked_up_value(self, monkeypatch) -> None:
        """
        [Local] resolve field: verifies an env source field returns the looked-up env variable value.

        Scenario:
            Given SOME_VAR set to a URI string and a field dict with source "env" and value "SOME_VAR"
            When _resolve_field is called
            Then the URI string is returned

        Boundaries:
            - Env var must be set; name is taken from the "value" key
            - No filesystem access; pure os.environ lookup

        On failure, first check:
            - Whether the "env" branch reads the var name from "value" and looks it up in os.environ
            - Recent changes to the "env" branch in _resolve_field
        """
        monkeypatch.setenv("SOME_VAR", URI)
        assert config._resolve_field({"source": "env", "value": "SOME_VAR"}, "f") == URI

    def test_env_unset_raises(self, monkeypatch) -> None:
        """
        [Local] resolve field: verifies a missing env variable raises ConfigError.

        Scenario:
            Given MISSING_VAR not set in the environment
            When _resolve_field is called with source "env" and value "MISSING_VAR"
            Then ConfigError is raised

        Boundaries:
            - Absent env var raises rather than returning None or empty string
            - Fail-loud: missing config is always an error, not a silent default

        On failure, first check:
            - Whether a missing env var returns None instead of raising ConfigError
            - Recent changes to the missing-var error path in _resolve_field
        """
        monkeypatch.delenv("MISSING_VAR", raising=False)
        with pytest.raises(ConfigError):
            config._resolve_field({"source": "env", "value": "MISSING_VAR"}, "f")

    def test_missing_source_raises(self) -> None:
        """
        [Unit] settings.json field resolver: verifies a field without 'source' raises ConfigError.

        Scenario:
            Given a field dict carrying only 'value'
            When config._resolve_field() resolves it
            Then ConfigError is raised instead of guessing a source.

        Boundaries:
            - Pure resolution logic, no file or env access

        On failure, first check:
            - The required-key validation at the top of _resolve_field()
        """
        with pytest.raises(ConfigError):
            config._resolve_field({"value": URI}, "f")

    def test_missing_value_raises(self) -> None:
        """
        [Unit] settings.json field resolver: verifies a field without 'value' raises ConfigError.

        Scenario:
            Given a field dict carrying only source 'literal'
            When config._resolve_field() resolves it
            Then ConfigError is raised.

        Boundaries:
            - Pure resolution logic, no file or env access

        On failure, first check:
            - The required-key validation at the top of _resolve_field()
        """
        with pytest.raises(ConfigError):
            config._resolve_field({"source": "literal"}, "f")

    def test_not_a_dict_raises(self) -> None:
        """
        [Unit] settings.json field resolver: verifies a bare string instead of a {source, value} object raises ConfigError.

        Scenario:
            Given a plain string in place of the field object
            When config._resolve_field() resolves it
            Then ConfigError is raised rather than the string being used as a value.

        Boundaries:
            - Only the shape check; valid shapes are covered by the happy-path tests

        On failure, first check:
            - The isinstance(dict) guard in _resolve_field()
        """
        with pytest.raises(ConfigError):
            config._resolve_field("just a string", "f")

    def test_unrecognized_source_raises(self) -> None:
        """
        [Unit] settings.json field resolver: verifies a source other than 'env' or 'literal' raises ConfigError.

        Scenario:
            Given a field with source 'vault'
            When config._resolve_field() resolves it
            Then ConfigError is raised (determinism: unknown sources fail loudly).

        Boundaries:
            - Only the source whitelist

        On failure, first check:
            - The accepted source values in _resolve_field()
        """
        with pytest.raises(ConfigError):
            config._resolve_field({"source": "vault", "value": "x"}, "f")


class TestResolveCertField:
    """Tests for config._resolve_cert_field(): resolves a cert path literal or env var relative to CONFIG_DIR."""

    def test_bare_filename_resolved_under_config_dir(self, monkeypatch, tmp_path) -> None:
        """
        [Unit] settings.json cert resolver: verifies a bare literal cert filename resolves under CONFIG_DIR.

        Scenario:
            Given CONFIG_DIR pointed at tmp_path and a literal cert value 'cert.pem'
            When config._resolve_cert_field() resolves it
            Then the result is CONFIG_DIR/cert.pem.

        Boundaries:
            - CONFIG_DIR is monkeypatched; the real ~/.bookscrapper/ is never touched
            - The file need not exist: resolution never opens it

        On failure, first check:
            - The relative-path join in _resolve_cert_field()
        """
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        resolved = config._resolve_cert_field({"source": "literal", "value": "cert.pem"}, "x509.cert")
        assert resolved == str(tmp_path / "cert.pem")

    def test_absolute_path_returned_untouched(self, monkeypatch, tmp_path) -> None:
        """
        [Unit] settings.json cert resolver: verifies an absolute literal cert path is returned unchanged.

        Scenario:
            Given CONFIG_DIR pointed at tmp_path and an absolute cert path outside it
            When config._resolve_cert_field() resolves it
            Then the absolute path comes back as given.

        Boundaries:
            - CONFIG_DIR is monkeypatched; nothing is read

        On failure, first check:
            - The is_absolute() branch in _resolve_cert_field()
        """
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        abs_path = str(tmp_path / "elsewhere" / "cert.pem")
        resolved = config._resolve_cert_field({"source": "literal", "value": abs_path}, "x509.cert")
        assert resolved == abs_path


class TestGetDefaultTlsCertFile:
    """Tests for config.get_default_tls_cert_file(): returns the newest X509-cert-*.pem by mtime."""

    def test_no_certs_returns_none(self, monkeypatch, tmp_path) -> None:
        """
        [Unit] legacy cert fallback: verifies an empty config dir yields no default cert.

        Scenario:
            Given CONFIG_DIR pointed at an empty tmp_path
            When config.get_default_tls_cert_file() runs
            Then it returns None.

        Boundaries:
            - CONFIG_DIR is monkeypatched to tmp_path

        On failure, first check:
            - The glob pattern and empty-result handling in get_default_tls_cert_file()
        """
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        assert config.get_default_tls_cert_file() is None

    def test_returns_newest_by_mtime(self, monkeypatch, tmp_path) -> None:
        """
        [Unit] legacy cert fallback: verifies the newest X509-cert-*.pem by modification time wins.

        Scenario:
            Given two cert files in tmp_path with mtimes forced apart via os.utime
            When config.get_default_tls_cert_file() runs
            Then the path of the newer file is returned.

        Boundaries:
            - mtimes are set explicitly so the result does not depend on write timing

        On failure, first check:
            - The max-by-st_mtime selection (not name order) in get_default_tls_cert_file()
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
        [Unit] legacy cert fallback: verifies files not matching X509-cert-*.pem are never picked.

        Scenario:
            Given a config dir holding only settings.json and notes.txt
            When config.get_default_tls_cert_file() runs
            Then it returns None.

        Boundaries:
            - CONFIG_DIR is monkeypatched to tmp_path

        On failure, first check:
            - The glob pattern used by get_default_tls_cert_file()
        """
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
        assert config.get_default_tls_cert_file() is None


class TestLoadSettings:
    """Tests for config.load_settings(): reads and parses settings.json, returns None when absent."""

    def test_nonexistent_path_returns_none(self, tmp_path) -> None:
        """
        [Unit] settings.json loader: verifies a missing settings file means legacy mode (None).

        Scenario:
            Given a settings path that does not exist
            When config.load_settings() reads it
            Then None is returned, selecting the legacy env fallback.

        Boundaries:
            - Real file-system check under tmp_path only

        On failure, first check:
            - The existence check at the start of load_settings()
        """
        assert config.load_settings(tmp_path / "does_not_exist.json") is None

    def test_malformed_json_raises(self, tmp_path) -> None:
        """
        [Unit] settings.json loader: verifies invalid JSON raises ConfigError instead of falling back.

        Scenario:
            Given a settings file containing '{not valid json'
            When config.load_settings() reads it
            Then ConfigError is raised.

        Boundaries:
            - Real file I/O under tmp_path only

        On failure, first check:
            - The JSONDecodeError handling in load_settings()
        """
        bad = tmp_path / "settings.json"
        bad.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ConfigError):
            config.load_settings(bad)

    def test_valid_json_returns_parsed_dict(self, tmp_path) -> None:
        """
        [Unit] settings.json loader: verifies a valid file is returned as the parsed dict.

        Scenario:
            Given a settings file holding {'auth_type': 'pass'}
            When config.load_settings() reads it
            Then the parsed dict is returned unchanged.

        Boundaries:
            - Only parsing; the shape validation of the dict is covered elsewhere

        On failure, first check:
            - load_settings() return value and its permission-warning side path
        """
        good = tmp_path / "settings.json"
        _write_settings(good, {"auth_type": "pass"})
        assert config.load_settings(good) == {"auth_type": "pass"}


class TestResolveMongoConnectionSettings:
    """Tests for config.resolve_mongo_connection() with settings.json: all six valid auth shapes plus error paths."""

    def test_pass_literal_uri(self, tmp_path) -> None:
        """
        [Unit] resolve mongo connection settings: verifies pass auth with a literal URI resolves to (uri, None).

        Scenario:
            Given settings.json with auth_type "pass" and a literal-sourced URI
            When resolve_mongo_connection(path) is called
            Then (URI, None) is returned

        Boundaries:
            - No env vars consulted; value read directly from "value" key
            - cert is always None for password auth

        On failure, first check:
            - Whether the "pass" block resolver reads the "value" key correctly
            - Recent changes to the password-auth resolution path in config.py
        """
        path = tmp_path / "settings.json"
        _write_settings(
            path,
            {"auth_type": "pass", "pass": {"uri": {"source": "literal", "value": URI}}},
        )
        assert config.resolve_mongo_connection(path) == (URI, None)

    def test_pass_env_uri(self, monkeypatch, tmp_path) -> None:
        """
        [Local] resolve mongo connection settings: verifies pass auth with an env-sourced URI resolves correctly.

        Scenario:
            Given MONGODB_URI set in the environment and settings.json with source "env" pointing to it
            When resolve_mongo_connection(path) is called
            Then (URI, None) is returned

        Boundaries:
            - env var lookup via source "env"; cert is always None for password auth
            - MONGODB_URI must be set before the resolver is called

        On failure, first check:
            - Whether the "env" source branch reads the var name from "value" correctly
            - Recent changes to the env-var resolution path in config.py
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
        [Local] resolve mongo connection settings: verifies x509 with a literal URI and bare cert filename resolves the cert under CONFIG_DIR.

        Scenario:
            Given x509 settings with literal URI and cert filename "cert.pem" and CONFIG_DIR patched to a subdirectory
            When resolve_mongo_connection(path) is called
            Then uri equals URI and cert equals str(CONFIG_DIR / "cert.pem")

        Boundaries:
            - A bare (non-absolute) cert filename is joined to CONFIG_DIR
            - Absolute cert paths are passed through untouched (tested separately)

        On failure, first check:
            - Whether _resolve_cert_field joins bare names to CONFIG_DIR
            - Recent changes to the cert-field resolution path in config.py
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
        [Local] resolve mongo connection settings: verifies all four x509 source combinations resolve to the same (uri, cert).

        Scenario:
            Given literal and env variants set to identical underlying values
            When all four {literal,env}x{literal,env} settings files are resolved
            Then all four results equal (URI, expected_cert_path)

        Boundaries:
            - Source mechanism is the only variable; underlying values are identical for all four combos
            - Both env vars (MONGODB_URI and TLS_CERT_FILE) are set to the same values as the literals

        On failure, first check:
            - Whether one source combo (e.g. env cert) resolves differently (e.g. not joined to CONFIG_DIR)
            - Recent changes to the source-dispatch logic in _resolve_field / _resolve_cert_field
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
        [Unit] resolve mongo connection settings: verifies a settings.json without auth_type raises ConfigError.

        Scenario:
            Given settings.json with a "pass" block but no "auth_type" key
            When resolve_mongo_connection(path) is called
            Then ConfigError is raised

        Boundaries:
            - auth_type is required; a "pass" block alone is not enough to infer the type
            - Fail-loud contract: no silent default

        On failure, first check:
            - Whether the resolver falls back to "pass" when auth_type is absent
            - Recent changes to the auth_type presence check in resolve_mongo_connection
        """
        path = tmp_path / "settings.json"
        _write_settings(path, {"pass": {"uri": {"source": "literal", "value": URI}}})
        with pytest.raises(ConfigError):
            config.resolve_mongo_connection(path)

    def test_unrecognized_auth_type_raises(self, tmp_path) -> None:
        """
        [Unit] resolve mongo connection settings: verifies an unsupported auth_type raises ConfigError.

        Scenario:
            Given settings.json with auth_type "kerberos"
            When resolve_mongo_connection(path) is called
            Then ConfigError is raised

        Boundaries:
            - Only "pass" and "x509" are recognized; anything else fails loudly
            - No silent fallback or default

        On failure, first check:
            - Whether the auth_type dispatcher has a catch-all default branch
            - Recent changes to the auth_type dispatch in resolve_mongo_connection
        """
        path = tmp_path / "settings.json"
        _write_settings(path, {"auth_type": "kerberos"})
        with pytest.raises(ConfigError):
            config.resolve_mongo_connection(path)

    def test_pass_without_pass_block_raises(self, tmp_path) -> None:
        """
        [Unit] resolve mongo connection settings: verifies auth_type "pass" without a "pass" block raises ConfigError.

        Scenario:
            Given settings.json with auth_type "pass" but no "pass" key
            When resolve_mongo_connection(path) is called
            Then ConfigError is raised

        Boundaries:
            - Type is valid but the required sub-block is absent; no silent default
            - Mirrors the symmetric test for x509

        On failure, first check:
            - Whether the "pass" resolver silently uses a default URI when the block is absent
            - Recent changes to the sub-block presence check in resolve_mongo_connection
        """
        path = tmp_path / "settings.json"
        _write_settings(path, {"auth_type": "pass"})
        with pytest.raises(ConfigError):
            config.resolve_mongo_connection(path)

    def test_x509_without_x509_block_raises(self, tmp_path) -> None:
        """
        [Unit] resolve mongo connection settings: verifies auth_type "x509" without an "x509" block raises ConfigError.

        Scenario:
            Given settings.json with auth_type "x509" but no "x509" key
            When resolve_mongo_connection(path) is called
            Then ConfigError is raised

        Boundaries:
            - Type is valid but the required sub-block is absent; no silent default
            - Mirrors the symmetric test for pass auth

        On failure, first check:
            - Whether the "x509" resolver silently uses env vars when the block is absent
            - Recent changes to the sub-block presence check in resolve_mongo_connection
        """
        path = tmp_path / "settings.json"
        _write_settings(path, {"auth_type": "x509"})
        with pytest.raises(ConfigError):
            config.resolve_mongo_connection(path)


class TestResolveMongoConnectionLegacyFallback:
    """Tests for config.resolve_mongo_connection() legacy fallback (no settings.json): MONGODB_URI / TLS_CERT_FILE / X509 glob."""

    def test_env_uri_no_cert(self, monkeypatch, tmp_path, no_real_env) -> None:
        """
        [Local] resolve mongo connection legacy fallback: verifies MONGODB_URI without a cert resolves to (uri, None).

        Scenario:
            Given MONGODB_URI set, no settings.json, no TLS_CERT_FILE, and an empty CONFIG_DIR
            When resolve_mongo_connection is called with a nonexistent settings path
            Then (URI, None) is returned via legacy fallback

        Boundaries:
            - Nonexistent settings path triggers legacy mode; no TLS cert present
            - no_real_env fixture strips real MongoDB vars so the test controls the full env

        On failure, first check:
            - Whether the legacy fallback is triggered correctly on a missing settings.json
            - Recent changes to the MONGODB_URI / legacy-mode path in config.py
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
        [Local] resolve mongo connection legacy fallback: verifies the newest X509-cert-*.pem is used when TLS_CERT_FILE is unset.

        Scenario:
            Given a cert file in CONFIG_DIR, MONGODB_URI set, and TLS_CERT_FILE absent
            When resolve_mongo_connection is called without settings.json
            Then the glob-found cert path is returned

        Boundaries:
            - Only one cert file present; glob-by-mtime picks it
            - TLS_CERT_FILE absent is the trigger for the glob fallback

        On failure, first check:
            - Whether get_default_tls_cert_file is called in legacy mode when TLS_CERT_FILE is absent
            - Recent changes to the cert fallback order in config.py
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
        [Local] resolve mongo connection legacy fallback: verifies TLS_CERT_FILE takes precedence over the X509 glob.

        Scenario:
            Given TLS_CERT_FILE set to an explicit path AND a cert glob match in CONFIG_DIR
            When resolve_mongo_connection is called without settings.json
            Then TLS_CERT_FILE is returned, not the glob match

        Boundaries:
            - Glob file exists and has a later mtime; precedence under test
            - time.sleep used to ensure distinct mtimes for the two files

        On failure, first check:
            - Whether TLS_CERT_FILE is checked before the glob in legacy mode
            - Recent changes to the env-vs-glob precedence in config.py
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
        [Local] settings file security: verifies a world-readable settings.json emits the warning exactly once per process.

        Scenario:
            Given a settings.json with mode 0o644 and the warning guard cleared by the autouse fixture
            When load_settings() is called twice on the same path
            Then exactly one caplog WARNING names the file and includes the "chmod 600" fix

        Boundaries:
            - POSIX only (test is skipif on Windows); mode 0o644
            - Per-process deduplication via _SECURITY_WARNINGS_EMITTED prevents double-firing

        On failure, first check:
            - Warning is missing, fires twice, or omits the "chmod 600" text
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
        [Local] settings file security: verifies the world-readable warning is printed to stdout, not only logged.

        Scenario:
            Given a settings.json with mode 0o644
            When load_settings() is called once
            Then stdout contains the "readable by group/other" text

        Boundaries:
            - POSIX only; root logger has only a file handler, so logger-only writes are invisible on console
            - capsys captures stdout; the warning must reach print_log, not only logging

        On failure, first check:
            - Warning is emitted via logger only and not via print_log to stdout
        """
        path = tmp_path / "settings.json"
        _write_settings(path, _pass_settings())
        os.chmod(path, 0o644)
        config.load_settings(path)
        assert "readable by group/other" in capsys.readouterr().out

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
    def test_owner_only_settings_does_not_warn(self, tmp_path, caplog) -> None:
        """
        [Unit] settings file security: verifies a correctly locked-down settings.json loads without a warning.

        Scenario:
            Given a settings.json with mode 0o600 owned by the current user
            When load_settings() is called
            Then no WARNING-level log records are emitted

        Boundaries:
            - POSIX only; mode 0o600, regular file (not a symlink), current user's UID
            - Covers the "all-clear" path to guard against false positives

        On failure, first check:
            - False-positive warning fires on a correctly locked-down file
        """
        path = tmp_path / "settings.json"
        _write_settings(path, _pass_settings())
        os.chmod(path, 0o600)
        with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
            config.load_settings(path)
        assert caplog.records == []

    def test_permission_check_skipped_on_windows(self, tmp_path, caplog, monkeypatch) -> None:
        """
        [Local] settings file security: verifies the POSIX permission check is skipped when os.name is "nt".

        Scenario:
            Given os.name patched to "nt" and a settings.json with mode 0o644
            When load_settings() is called
            Then no WARNING-level log records are emitted

        Boundaries:
            - os.name monkeypatched; the actual OS is not changed
            - Ensures the guard is conditional on POSIX, not just the skipif decorator on other tests

        On failure, first check:
            - POSIX permission check runs even when os.name is "nt"
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
        [Local] settings file security: verifies a symlinked settings.json emits an "is a symlink" warning.

        Scenario:
            Given settings.json is a symlink pointing to an owner-only regular target
            When load_settings() is called via the symlink path
            Then a caplog WARNING includes "is a symlink"

        Boundaries:
            - POSIX only; the target itself is 0o600, so only the symlink check fires
            - An attacker-controlled symlink target could redirect auth credentials

        On failure, first check:
            - Symlink path is resolved before the check, causing the warning to be skipped
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
        [Local] settings file security: verifies a settings.json owned by a different UID emits a warning.

        Scenario:
            Given os.getuid patched to return file_owner_uid + 1 and a settings.json with mode 0o600
            When load_settings() is called
            Then a caplog WARNING includes "not owned by the current user"

        Boundaries:
            - POSIX only; monkeypatched UID avoids needing to chown the file
            - Mode is 0o600 so only the ownership check fires, not the permission check

        On failure, first check:
            - Foreign-owned config is trusted without warning
        """
        path = tmp_path / "settings.json"
        _write_settings(path, _pass_settings())
        os.chmod(path, 0o600)
        monkeypatch.setattr(config.os, "getuid", lambda: os.stat(path).st_uid + 1)
        with caplog.at_level(logging.WARNING, logger=CONFIG_LOGGER):
            config.load_settings(path)
        assert any("not owned by the current user" in r.message for r in caplog.records)


class TestUnknownTopLevelKeys:
    """Tests for settings.json unknown-key detection: ConfigError raised loudly, values never leaked (SEC-11)."""

    def test_unknown_top_level_key_raises(self, tmp_path) -> None:
        """
        [Unit] unknown top-level keys: verifies an unrecognized top-level key raises ConfigError naming the key.

        Scenario:
            Given settings.json with a typo'd key "x059" (instead of "x509")
            When load_settings() is called
            Then ConfigError is raised and "x059" appears in the exception message

        Boundaries:
            - Otherwise-valid file structure; only the key name is wrong
            - Check happens in load_settings before resolution

        On failure, first check:
            - Unknown key is silently dropped and the config resolves to a wrong shape
        """
        path = tmp_path / "settings.json"
        _write_settings(path, {"auth_type": "x509", "x059": {"uri": {}}})
        with pytest.raises(ConfigError) as exc:
            config.load_settings(path)
        assert "x059" in str(exc.value)

    def test_unknown_key_error_lists_only_key_names_not_values(self, tmp_path) -> None:
        """
        [Unit] unknown top-level keys: verifies the ConfigError names the offending key but never leaks its value (SEC-11).

        Scenario:
            Given settings.json with unknown key "bogus" whose value embeds a URI containing "hunter2placeholder"
            When load_settings() is called
            Then ConfigError is raised, "bogus" is in the message, and "hunter2placeholder" is not

        Boundaries:
            - SEC-11: error messages must never echo field values, only key names
            - Distinctive placeholder "hunter2placeholder" used as the credential marker

        On failure, first check:
            - The raw value (including the password) leaks into the ConfigError message
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
    """Tests for config.resolve_mongo_namespace(): database block resolution, defaults, and legacy env-var fallback."""

    def test_database_block_resolves_name_and_collection(self, monkeypatch, tmp_path) -> None:
        """
        [Local] resolve mongo namespace: verifies a settings.json database block resolves name and collection, ignoring env vars.

        Scenario:
            Given settings.json with a literal database block and MONGODB_DB_NAME/COLLECTION set to different values
            When resolve_mongo_namespace(path) is called
            Then ("custom_db", "custom_books") is returned, not the env-var values

        Boundaries:
            - settings.json is the sole source of truth; env vars are only consulted in legacy mode
            - Both env vars set to non-defaults to confirm they are not consulted

        On failure, first check:
            - Env-var namespace leaks in despite settings.json being present
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
        [Local] resolve mongo namespace: verifies a database block field with source "env" is resolved from the environment.

        Scenario:
            Given settings.json with database.name sourced from DB_NAME_VAR set to "resolved_db" and collection literal
            When resolve_mongo_namespace(path) is called
            Then ("resolved_db", "books") is returned

        Boundaries:
            - Mixed sources: name from env, collection literal
            - Same _resolve_field resolution path as auth fields

        On failure, first check:
            - Env-sourced database field returns the var name string instead of its looked-up value
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
        [Local] resolve mongo namespace: verifies settings.json without a database block returns defaults, not env vars.

        Scenario:
            Given settings.json with no database block and MONGODB_DB_NAME/COLLECTION set to non-defaults
            When resolve_mongo_namespace(path) is called
            Then (DEFAULT_DB_NAME, DEFAULT_COLLECTION_NAME) is returned

        Boundaries:
            - Absent database block means use defaults; env vars are NOT consulted
            - Contrast with legacy mode (no settings.json) where env vars DO apply

        On failure, first check:
            - Env vars leak in despite settings.json being present (breaks sole-source-of-truth)
        """
        monkeypatch.setenv("MONGODB_DB_NAME", "env_db")
        monkeypatch.setenv("MONGODB_COLLECTION_NAME", "env_coll")
        path = tmp_path / "settings.json"
        _write_settings(path, _pass_settings())
        assert config.resolve_mongo_namespace(path) == (config.DEFAULT_DB_NAME, config.DEFAULT_COLLECTION_NAME)

    def test_database_block_missing_field_raises(self, tmp_path) -> None:
        """
        [Unit] resolve mongo namespace: verifies a database block missing "collection" raises ConfigError.

        Scenario:
            Given settings.json with a database block containing only "name"
            When resolve_mongo_namespace(path) is called
            Then ConfigError is raised

        Boundaries:
            - Both "name" and "collection" must be present; partial block is never half-defaulted
            - Fail-loud: present means "I'm configuring this", so a partial block is an error

        On failure, first check:
            - A partial database block silently defaults the missing field
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
        [Unit] resolve mongo namespace: verifies an empty or non-object database key raises ConfigError.

        Scenario:
            Given settings.json with a database key that is an empty dict, a bare string, or an empty list
            When resolve_mongo_namespace(path) is called
            Then ConfigError is raised for each invalid shape

        Boundaries:
            - Parametrized over {}, "bookscraper_db", []; present-but-wrong shapes are always rejected
            - "Present means I'm configuring this": silently defaulting would be a guess

        On failure, first check:
            - Empty or malformed block silently resolves to bookscraper_db.books
        """
        path = tmp_path / "settings.json"
        _write_settings(path, {"auth_type": "pass", "database": block})
        with pytest.raises(ConfigError):
            config.resolve_mongo_namespace(path)

    def test_legacy_mode_honors_db_name_env_vars(self, monkeypatch, tmp_path, no_real_env) -> None:
        """
        [Local] resolve mongo namespace: verifies legacy mode uses MONGODB_DB_NAME and MONGODB_COLLECTION_NAME.

        Scenario:
            Given MONGODB_DB_NAME and MONGODB_COLLECTION_NAME set to non-defaults and no settings.json
            When resolve_mongo_namespace(tmp_path / "nope.json") is called
            Then ("legacy_db", "legacy_coll") is returned

        Boundaries:
            - Nonexistent path triggers legacy mode; env vars are the only namespace source
            - Backward compatibility contract for users without settings.json

        On failure, first check:
            - Legacy env-var namespace configuration stops working
        """
        monkeypatch.setenv("MONGODB_DB_NAME", "legacy_db")
        monkeypatch.setenv("MONGODB_COLLECTION_NAME", "legacy_coll")
        assert config.resolve_mongo_namespace(tmp_path / "nope.json") == ("legacy_db", "legacy_coll")

    def test_legacy_mode_defaults_when_env_unset(self, monkeypatch, tmp_path, no_real_env) -> None:
        """
        [Local] resolve mongo namespace: verifies legacy mode returns defaults when namespace env vars are unset.

        Scenario:
            Given no settings.json and MONGODB_DB_NAME / MONGODB_COLLECTION_NAME both deleted
            When resolve_mongo_namespace(tmp_path / "nope.json") is called
            Then (DEFAULT_DB_NAME, DEFAULT_COLLECTION_NAME) is returned

        Boundaries:
            - Legacy mode: absent env vars mean fall back to compile-time defaults
            - No ConfigError; silent defaults are correct here (no settings.json to contradict them)

        On failure, first check:
            - Missing namespace env vars crash instead of defaulting
        """
        monkeypatch.delenv("MONGODB_DB_NAME", raising=False)
        monkeypatch.delenv("MONGODB_COLLECTION_NAME", raising=False)
        assert config.resolve_mongo_namespace(tmp_path / "nope.json") == (
            config.DEFAULT_DB_NAME,
            config.DEFAULT_COLLECTION_NAME,
        )


class TestExampleSettingsFile:
    """Tests that docs/examples/settings.example.json parses and resolves through the real resolver."""

    def test_example_settings_file_is_valid_for_resolver(self, isolated_config, monkeypatch) -> None:
        """
        [Local] example settings file: verifies docs/examples/settings.example.json resolves cleanly when MONGODB_URI is set.

        Scenario:
            Given MONGODB_URI set and isolated_config patching CONFIG_DIR to tmp_path
            When resolve_mongo_connection and resolve_mongo_namespace are called on the example file
            Then URI matches, cert ends with "X509-cert-example.pem", and namespace is ("bookscraper_db", "books")

        Boundaries:
            - Real example file on disk; isolated_config prevents ~/.bookscrapper leaking in
            - Exercises the full x509 + database block shape

        On failure, first check:
            - The example file has drifted from the resolver's accepted key/value shape
        """
        example = Path(__file__).resolve().parents[4] / "docs" / "examples" / "settings.example.json"
        monkeypatch.setenv("MONGODB_URI", URI)
        uri, cert = config.resolve_mongo_connection(example)
        assert uri == URI
        assert cert.endswith("X509-cert-example.pem")
        assert config.resolve_mongo_namespace(example) == ("bookscraper_db", "books")
