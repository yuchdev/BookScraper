"""Mock-tier tests for bookscraper.backends.mongo.client - the single MongoClient factory.

Every test patches ``client.MongoClient`` (the sole construction site in ``src/``) or drives
the pure helpers directly; no real Atlas connection is ever attempted. The security focus is
redaction: a pymongo failure must never leak the host/URI/credentials through
``MongoConnectionError``, a log, or ``print_log``.
"""

import logging
from unittest.mock import MagicMock, Mock, patch

import pytest
from pymongo.errors import ConfigurationError, OperationFailure, ServerSelectionTimeoutError

from bookscraper.backends.mongo import client
from bookscraper.backends.mongo.client import MongoConnectionError

X509_URI = "mongodb+srv://cluster.example.net/db?authSource=%24external&authMechanism=MONGODB-X509"
SCRAM_URI = "mongodb+srv://user:pw@cluster.example.net/db?retryWrites=true"
BARE_URI = "mongodb+srv://cluster.example.net/db?retryWrites=true"


# --------------------------------------------------------------------------- #
# uri_selects_x509_auth / uri_has_embedded_credentials
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "uri, expected",
    [
        (X509_URI, True),
        ("mongodb+srv://c.example.net/db?authMechanism=mongodb-x509", True),  # case-insensitive
        (SCRAM_URI, False),
        (BARE_URI, False),
        ("mongodb+srv://c.example.net/db?authMechanism=SCRAM-SHA-256", False),
    ],
)
def test_uri_selects_x509_auth(uri, expected) -> None:
    """
    [Local] client: verifies that uri_selects_x509_auth detects the MONGODB-X509 authMechanism (case-insensitive).

    Scenario:
        Given a URI from the parametrized set (X509, lowercase x509, SCRAM, bare, SCRAM-SHA-256)
        When client.uri_selects_x509_auth(uri) is called
        Then it returns True only for URIs that include authMechanism=MONGODB-X509 (case-insensitive).

    Boundaries:
        - 5 parametrized cases covering X509 present/absent and case variation
        - Pure string parsing; no network or mock

    On failure, first check:
        - The authMechanism detection regex/string comparison in uri_selects_x509_auth
    """
    assert client.uri_selects_x509_auth(uri) is expected


@pytest.mark.parametrize(
    "uri, expected",
    [(SCRAM_URI, True), (X509_URI, False), (BARE_URI, False)],
)
def test_uri_has_embedded_credentials(uri, expected) -> None:
    """
    [Local] client: verifies that uri_has_embedded_credentials detects user:password in the URI.

    Scenario:
        Given a URI from the parametrized set (SCRAM with user:pw, X509 without, bare without)
        When client.uri_has_embedded_credentials(uri) is called
        Then it returns True only for the SCRAM URI that embeds user:pw in the netloc.

    Boundaries:
        - 3 parametrized cases; pure string parsing, no network or mock

    On failure, first check:
        - The credential detection logic in uri_has_embedded_credentials
    """
    assert client.uri_has_embedded_credentials(uri) is expected


# --------------------------------------------------------------------------- #
# _warn_if_x509_mechanism_missing - the three-way branch
# --------------------------------------------------------------------------- #
def test_x509_mechanism_present_warns_nothing() -> None:
    """
    [Local] client: verifies that a properly configured X.509 URI with a cert emits no warning or critical log.

    Scenario:
        Given an X509_URI that includes authMechanism=MONGODB-X509 and a cert path
        When _warn_if_x509_mechanism_missing(X509_URI, "/certs/x.pem") is called
        Then neither warning nor critical is logged, and print_log is not called.

    Boundaries:
        - The "correctly configured" no-op path; warning and critical loggers both patched

    On failure, first check:
        - The early-return condition in _warn_if_x509_mechanism_missing
    """
    with (
        patch.object(client.module_logger, "warning") as warn,
        patch.object(client.module_logger, "critical") as crit,
        patch.object(client, "print_log") as printed,
    ):
        client._warn_if_x509_mechanism_missing(X509_URI, "/certs/x.pem")

    warn.assert_not_called()
    crit.assert_not_called()
    printed.assert_not_called()


def test_scram_uri_with_stray_cert_warns_but_does_not_escalate() -> None:
    """
    [Local] client: verifies scram uri with stray cert warns but does not escalate.

    Scenario:
        Legacy resolution attaches the newest cert to any URI, so a working SCRAM setup can pick one up incidentally - that is a warning, not a critical.

    Boundaries:
        - see test body

    On failure, first check:
        - see test body
    """
    with (
        patch.object(client.module_logger, "warning") as warn,
        patch.object(client.module_logger, "critical") as crit,
        patch.object(client, "print_log") as printed,
    ):
        client._warn_if_x509_mechanism_missing(SCRAM_URI, "/certs/x.pem")

    crit.assert_not_called()
    printed.assert_not_called()
    assert "SCRAM" in warn.call_args.args[0]


def test_cert_without_mechanism_or_credentials_is_critical() -> None:
    """
    [Local] client: verifies cert without mechanism or credentials is critical.

    Scenario:
        No mechanism and no userinfo means the connection authenticates as nobody.

    Boundaries:
        - see test body

    On failure, first check:
        - see test body
    """
    with (
        patch.object(client.module_logger, "critical") as crit,
        patch.object(client, "print_log") as printed,
    ):
        client._warn_if_x509_mechanism_missing(BARE_URI, "/certs/x.pem")

    assert "will not be authenticated" in crit.call_args.args[0]
    assert "MONGODB-X509" in crit.call_args.args[0]
    assert printed.call_args.args[1] == "warning"


# --------------------------------------------------------------------------- #
# resolve_server_selection_timeout_ms
# --------------------------------------------------------------------------- #
class TestResolveTimeout:
    """Tests for client.resolve_server_selection_timeout_ms."""

    def test_timeout_default_and_env_override(self, monkeypatch) -> None:
        """
        [Local] client: verifies timeout default and env override.

        Scenario:
            the timeout defaults to DEFAULT_SERVER_SELECTION_TIMEOUT_MS and is overridden by MONGODB_SERVER_SELECTION_TIMEOUT_MS when set to a valid integer.

        Boundaries:
            - env unset -> default; env "2500" -> 2500.

        On failure, first check:
            - the knob is ignored, or the default drifts from 10_000.
        """
        monkeypatch.delenv(client.SERVER_SELECTION_TIMEOUT_ENV, raising=False)
        assert client.resolve_server_selection_timeout_ms() == client.DEFAULT_SERVER_SELECTION_TIMEOUT_MS
        assert client.DEFAULT_SERVER_SELECTION_TIMEOUT_MS == 10_000

        monkeypatch.setenv(client.SERVER_SELECTION_TIMEOUT_ENV, "2500")
        assert client.resolve_server_selection_timeout_ms() == 2500

    def test_blank_env_falls_back_to_default(self, monkeypatch) -> None:
        """
        [Local] client: verifies blank env falls back to default.

        Scenario:
            a blank/whitespace env value falls back to the default.

        Boundaries:
            - env set to "   ".

        On failure, first check:
            - an empty knob crashes int() instead of defaulting.
        """
        monkeypatch.setenv(client.SERVER_SELECTION_TIMEOUT_ENV, "   ")
        assert client.resolve_server_selection_timeout_ms() == client.DEFAULT_SERVER_SELECTION_TIMEOUT_MS

    def test_non_integer_env_warns_and_defaults(self, monkeypatch) -> None:
        """
        [Local] client: verifies non integer env warns and defaults.

        Scenario:
            a non-integer knob warns and falls back rather than failing the connection.

        Boundaries:
            - env "abc".

        On failure, first check:
            - a mistyped knob aborts the connection.
        """
        monkeypatch.setenv(client.SERVER_SELECTION_TIMEOUT_ENV, "abc")
        with patch.object(client.module_logger, "warning") as warn:
            assert client.resolve_server_selection_timeout_ms() == client.DEFAULT_SERVER_SELECTION_TIMEOUT_MS
        warn.assert_called_once()

    def test_non_positive_env_warns_and_defaults(self, monkeypatch) -> None:
        """
        [Local] client: verifies non positive env warns and defaults.

        Scenario:
            a non-positive knob warns and falls back.

        Boundaries:
            - env "0".

        On failure, first check:
            - a zero/negative timeout is passed straight to pymongo.
        """
        monkeypatch.setenv(client.SERVER_SELECTION_TIMEOUT_ENV, "0")
        with patch.object(client.module_logger, "warning") as warn:
            assert client.resolve_server_selection_timeout_ms() == client.DEFAULT_SERVER_SELECTION_TIMEOUT_MS
        warn.assert_called_once()


# --------------------------------------------------------------------------- #
# build_mongo_client
# --------------------------------------------------------------------------- #
class TestBuildMongoClient:
    """Tests for client.build_mongo_client."""

    def test_build_client_with_cert_sets_tls_and_key_file(self, tmp_path) -> None:
        """
        [Local] client: verifies build client with cert sets tls and key file.

        Scenario:
            a valid cert yields tls=True + tlsCertificateKeyFile and hard-locked validation, plus ServerApi("1") and the timeout.

        Boundaries:
            - an existing cert path with an X.509 URI (no warning branch).

        On failure, first check:
            - TLS is left off, the key file is dropped, or validation is disableable.
        """
        cert = tmp_path / "cert.pem"
        cert.write_text("dummy")
        instance = MagicMock(name="client")
        with patch.object(client, "MongoClient", return_value=instance) as mock_ctor:
            result = client.build_mongo_client(X509_URI, str(cert), None, timeout_ms=1234)

        assert result is instance
        args, kwargs = mock_ctor.call_args
        assert args[0] == X509_URI
        assert kwargs["tls"] is True
        assert kwargs["tlsCertificateKeyFile"] == str(cert)
        assert kwargs["tlsAllowInvalidCertificates"] is False
        assert kwargs["serverSelectionTimeoutMS"] == 1234
        assert kwargs["server_api"].version == "1"
        assert "tlsCAFile" not in kwargs

    def test_build_client_without_cert_omits_tls_kwargs(self) -> None:
        """
        [Local] client: verifies build client without cert omits tls kwargs.

        Scenario:
            with no cert, tls/tlsCertificateKeyFile are omitted but validation and ServerApi are still set.

        Boundaries:
            - cert_file=None, ca_file=None.

        On failure, first check:
            - a certless build accidentally sets tls kwargs or disables validation.
        """
        instance = MagicMock(name="client")
        with patch.object(client, "MongoClient", return_value=instance) as mock_ctor:
            client.build_mongo_client(SCRAM_URI, None, None)

        _, kwargs = mock_ctor.call_args
        assert "tls" not in kwargs
        assert "tlsCertificateKeyFile" not in kwargs
        assert kwargs["tlsAllowInvalidCertificates"] is False
        assert kwargs["serverSelectionTimeoutMS"] == client.DEFAULT_SERVER_SELECTION_TIMEOUT_MS

    def test_build_client_passes_ca_file_only_when_set(self) -> None:
        """
        [Local] client: verifies build client passes ca file only when set.

        Scenario:
            tlsCAFile is present only when ca_file is given.

        Boundaries:
            - ca_file="/ca.pem" vs ca_file=None.

        On failure, first check:
            - the CA bundle is ignored, or a None CA path is forwarded.
        """
        instance = MagicMock(name="client")
        with patch.object(client, "MongoClient", return_value=instance) as mock_ctor:
            client.build_mongo_client(SCRAM_URI, None, "/ca.pem")
        _, kwargs = mock_ctor.call_args
        assert kwargs["tlsCAFile"] == "/ca.pem"

        with patch.object(client, "MongoClient", return_value=instance) as mock_ctor:
            client.build_mongo_client(SCRAM_URI, None, None)
        _, kwargs = mock_ctor.call_args
        assert "tlsCAFile" not in kwargs

    def test_build_client_missing_cert_file_raises(self, tmp_path) -> None:
        """
        [Local] client: verifies build client missing cert file raises.

        Scenario:
            a configured cert path that does not exist raises rather than dropping it.

        Boundaries:
            - cert_file points at a nonexistent file; MongoClient must never be reached.

        On failure, first check:
            - the missing cert is silently dropped and a certless client is built.
        """
        missing = str(tmp_path / "nope.pem")
        with patch.object(client, "MongoClient") as mock_ctor:
            with pytest.raises(MongoConnectionError):
                client.build_mongo_client(SCRAM_URI, missing, None)
        mock_ctor.assert_not_called()

    def test_build_client_warns_when_cert_lacks_x509_mechanism(self, tmp_path) -> None:
        """
        [Local] client: verifies build client warns when cert lacks x509 mechanism.

        Scenario:
            a cert paired with a bare URI triggers the unauthenticated-connection warning.

        Boundaries:
            - existing cert + BARE_URI (no mechanism, no creds).

        On failure, first check:
            - the silent-cert misconfiguration is not surfaced.
        """
        cert = tmp_path / "cert.pem"
        cert.write_text("dummy")
        with (
            patch.object(client, "MongoClient", return_value=MagicMock()),
            patch.object(client, "_warn_if_x509_mechanism_missing") as warn,
        ):
            client.build_mongo_client(BARE_URI, str(cert), None)
        warn.assert_called_once_with(BARE_URI, str(cert))

    def test_build_client_wraps_construction_error_redacted(self) -> None:
        """
        [Local] client: verifies build client wraps construction error redacted.

        Scenario:
            MongoClient rejects a bad URI (ConfigurationError embedding the URI); the factory re-raises a redacted MongoConnectionError that never carries the URI text.

        Boundaries:
            - constructor raises ConfigurationError with the secret URI in the message.

        On failure, first check:
            - the raw URI (with password) escapes through the exception message.
        """
        secret_uri = "mongodb+srv://user:pw@cluster0.example.net/db"
        with patch.object(client, "MongoClient", side_effect=ConfigurationError(secret_uri)):
            with pytest.raises(MongoConnectionError) as excinfo:
                client.build_mongo_client(secret_uri, None, None)
        message = str(excinfo.value)
        assert "cluster0.example.net" not in message
        assert "user:pw" not in message
        assert "ConfigurationError" in message


# --------------------------------------------------------------------------- #
# _redact_pymongo_error / ping
# --------------------------------------------------------------------------- #
class TestPing:
    """Tests for client.ping."""

    def test_ping_success_is_silent(self) -> None:
        """
        [Local] client: verifies ping success is silent.

        Scenario:
            a healthy client pings without raising.

        Boundaries:
            - admin.command("ping") returns normally.

        On failure, first check:
            - a successful ping raises or fails to issue the command.
        """
        instance = MagicMock(name="client")
        client.ping(instance)
        instance.admin.command.assert_called_once_with("ping")

    def test_ping_wraps_error_keeping_code_name_only(self) -> None:
        """
        [Local] client: verifies ping wraps error keeping code name only.

        Scenario:
            an OperationFailure carrying code/codeName is reduced to "OperationFailure 8000 AtlasError" - class name plus code plus codeName only.

        Boundaries:
            - OperationFailure with code=8000 and details.codeName="AtlasError".

        On failure, first check:
            - the raw pymongo message (or extra detail) survives into the wrapper.
        """
        err = OperationFailure("bad thing at cluster0.example.net", code=8000, details={"codeName": "AtlasError"})
        instance = MagicMock(name="client")
        instance.admin.command.side_effect = err
        with pytest.raises(MongoConnectionError) as excinfo:
            client.ping(instance)
        assert str(excinfo.value) == "OperationFailure 8000 AtlasError"

    def test_ping_wraps_error_without_code(self) -> None:
        """
        [Local] client: verifies ping wraps error without code.

        Scenario:
            a ServerSelectionTimeoutError (no code/codeName) is reduced to its class name alone.

        Boundaries:
            - exception with no code attribute and no details dict.

        On failure, first check:
            - the raw host-bearing timeout text leaks.
        """
        instance = MagicMock(name="client")
        instance.admin.command.side_effect = ServerSelectionTimeoutError("no servers at cluster0.example.net:27017")
        with pytest.raises(MongoConnectionError) as excinfo:
            client.ping(instance)
        assert str(excinfo.value) == "ServerSelectionTimeoutError"

    def test_ping_error_does_not_leak_host_in_message_or_logs(self, caplog) -> None:
        """
        [Local] client: verifies ping error does not leak host in message or logs.

        Scenario:
            regression - a pymongo error whose message embeds a fake URI/host must not appear in the MongoConnectionError message nor in any captured log (CWE-532/CWE-209).

        Boundaries:
            - OperationFailure message contains "mongodb+srv://user:pw@cluster0.example.net".

        On failure, first check:
            - the credential-bearing string reaches the exception or the log record.
        """
        secret = "mongodb+srv://user:pw@cluster0.example.net"
        instance = MagicMock(name="client")
        instance.admin.command.side_effect = OperationFailure(
            f"auth failed connecting to {secret}", code=18, details={"codeName": "AuthenticationFailed"}
        )
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(MongoConnectionError) as excinfo:
                client.ping(instance)

        message = str(excinfo.value)
        assert secret not in message
        assert "cluster0.example.net" not in message
        assert "user:pw" not in message
        assert secret not in caplog.text
        assert "cluster0.example.net" not in caplog.text

    def test_redact_ignores_non_dict_details(self) -> None:
        """
        [Local] client: verifies redact ignores non dict details.

        Scenario:
            an exception whose ``details`` is not a dict contributes only its class name and code.

        Boundaries:
            - a crafted exception with details set to a string.

        On failure, first check:
            - the redactor crashes on a non-dict details attribute.
        """

        class Weird(Exception):
            """Exception subclass with non-dict details to test the redact-ignores path."""

            code = 42
            details = "not-a-dict"

        assert client._redact_pymongo_error(Weird()) == "Weird 42"


# --------------------------------------------------------------------------- #
# Cross-path parity: database init and the pre-flight check build identical kwargs
# --------------------------------------------------------------------------- #
def test_database_and_preflight_build_identical_client_kwargs(monkeypatch, tmp_path) -> None:
    """
    [Local] client: verifies database and preflight build identical client kwargs.

    Scenario:
        for the same resolved inputs, database._initialize_mongodb_connection and book_utils.check_mongodb_connection construct MongoClient with identical kwargs (single factory, one construction site), so a diagnosis on one path carries to the other.

    Boundaries:
        - same URI, same existing cert, same TLS_CA_FILE on both paths.

    On failure, first check:
        - the two paths drift again (CA ignored, cert dropped, timeout mismatch).
    """
    from bookscraper import book_utils
    from bookscraper.backends.mongo import database

    cert = tmp_path / "cert.pem"
    cert.write_text("dummy")
    resolved = (X509_URI, str(cert))

    monkeypatch.setenv("TLS_CA_FILE", "/ca.pem")
    monkeypatch.delenv(client.SERVER_SELECTION_TIMEOUT_ENV, raising=False)
    monkeypatch.setattr(database, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(book_utils, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(database.config, "resolve_mongo_connection", Mock(return_value=resolved))
    monkeypatch.setattr("bookscraper.backends.mongo.config.resolve_mongo_connection", Mock(return_value=resolved))
    monkeypatch.setattr(database, "_mongo_client", None)
    monkeypatch.setattr(database, "_mongo_collection", None)
    monkeypatch.setattr(database, "ensure_indexes", Mock())

    captured = []

    def fake_ctor(uri, **kwargs):
        """Capture (uri, kwargs) and return a fresh MagicMock as the client."""
        captured.append((uri, kwargs))
        return MagicMock(name="client")

    with patch.object(client, "MongoClient", side_effect=fake_ctor):
        database._initialize_mongodb_connection()
        book_utils.check_mongodb_connection()

    assert len(captured) == 2
    db_uri, db_kwargs = captured[0]
    pf_uri, pf_kwargs = captured[1]
    assert db_uri == pf_uri
    # ServerApi instances aren't equality-comparable; compare by version and drop them.
    assert db_kwargs.pop("server_api").version == pf_kwargs.pop("server_api").version
    assert db_kwargs == pf_kwargs
    assert db_kwargs["tlsCAFile"] == "/ca.pem"
    assert db_kwargs["tlsCertificateKeyFile"] == str(cert)
