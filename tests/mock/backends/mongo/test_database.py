"""Mock-tier tests for bookscraper.backends.mongo.database.

The module holds _mongo_client / _mongo_db / _mongo_collection as mutable
module-level globals; the autouse reset_globals fixture forces all three back to
None before every test so state never leaks between cases. MongoClient and the
config resolver are patched at their point of use inside the database module, so
no real Atlas connection is ever attempted. load_dotenv is neutered so a repo-root
.env can't leak real values into a test.
"""

import logging
from unittest.mock import MagicMock, Mock, patch

import pytest
from pymongo.errors import (
    BulkWriteError,
    PyMongoError,
)

from bookscraper.backends.mongo import client, config, database
from bookscraper.backends.mongo.client import MongoConnectionError
from bookscraper.backends.storage import SaveSummary


@pytest.fixture(autouse=True)
def reset_globals(monkeypatch):
    """Reset the module-level connection globals and stop load_dotenv from reading
    a real .env for every test in this module."""
    monkeypatch.setattr(database, "_mongo_client", None)
    monkeypatch.setattr(database, "_mongo_db", None)
    monkeypatch.setattr(database, "_mongo_collection", None)
    monkeypatch.setattr(database, "_mongo_db_name", config.DEFAULT_DB_NAME)
    monkeypatch.setattr(database, "_mongo_collection_name", config.DEFAULT_COLLECTION_NAME)
    monkeypatch.setattr(database, "load_dotenv", lambda *a, **k: None)
    # Default the namespace resolver so a real _initialize never reads the real
    # ~/.bookscrapper/settings.json; individual tests override it as needed.
    monkeypatch.setattr(
        database.config,
        "resolve_mongo_namespace",
        Mock(return_value=(config.DEFAULT_DB_NAME, config.DEFAULT_COLLECTION_NAME)),
    )
    yield


# --------------------------------------------------------------------------- #
# _initialize_mongodb_connection
#
# The MongoClient itself is built by the single factory in client.py; database.py
# calls build_mongo_client + ping, so these tests patch those (imported into the
# database namespace) rather than a MongoClient constructor - there is no MongoClient
# reference in database.py to patch anymore.
# --------------------------------------------------------------------------- #
class TestInitializeMongodbConnection:
    """Tests for database._initialize_mongodb_connection."""

    def test_config_error_returns_none_none(self, monkeypatch, no_real_env):
        """
        [Local] database connection: verifies config error returns none none.

        Scenario:
            the resolver raises ConfigError, so no client is ever built.

        Boundaries:
            - resolve_mongo_connection raises before any factory call.

        On failure, first check:
            - a malformed config still reaches the factory / network.
        """
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(side_effect=config.ConfigError("bad config")),
        )
        with patch.object(database, "build_mongo_client") as mock_build:
            result = database._initialize_mongodb_connection()

        assert result == (None, None)
        mock_build.assert_not_called()

    def test_missing_uri_returns_none_none(self, monkeypatch, no_real_env):
        """
        [Local] database connection: verifies missing uri returns none none.

        Scenario:
            the resolver yields no URI, so the factory is never called.

        Boundaries:
            - resolve returns (None, None).

        On failure, first check:
            - a connection is attempted with an empty URI.
        """
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=(None, None)),
        )
        with patch.object(database, "build_mongo_client") as mock_build:
            result = database._initialize_mongodb_connection()

        assert result == (None, None)
        mock_build.assert_not_called()

    def test_missing_cert_file_returns_none_none(self, monkeypatch, no_real_env, tmp_path):
        """
        [Local] database connection: verifies missing cert file returns none none.

        Scenario:
            a configured client cert that does not exist aborts the connection via the factory's MongoConnectionError, rather than silently connecting certless.

        Boundaries:
            - resolve returns a cert path that does not exist; the real factory runs.

        On failure, first check:
            - the missing cert is dropped and a certless client is built.
        """
        missing = str(tmp_path / "does_not_exist.pem")
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=("mongodb+srv://uri", missing)),
        )
        # Real build_mongo_client; patch the underlying MongoClient so a slip never reaches network.
        with patch("bookscraper.backends.mongo.client.MongoClient") as mock_client:
            result = database._initialize_mongodb_connection()

        assert result == (None, None)
        mock_client.assert_not_called()

    def test_with_cert_file_builds_via_factory_and_pings(self, monkeypatch, no_real_env, tmp_path):
        """
        [Local] database connection: verifies with cert file builds via factory and pings.

        Scenario:
            with a valid cert the factory builds the client, ping confirms it, and the indexes are ensured on the resolved collection.

        Boundaries:
            - resolve returns a real cert path; build + ping are stubbed.

        On failure, first check:
            - the cert path is not forwarded to the factory, or ping/index setup is skipped.
        """
        cert = tmp_path / "cert.pem"
        cert.write_text("dummy-cert")
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=("mongodb+srv://uri", str(cert))),
        )
        spy_index = Mock()
        monkeypatch.setattr(database, "ensure_indexes", spy_index)

        # MagicMock so `_mongo_client[db_name][coll_name]` subscripting works.
        client_instance = MagicMock(name="client")
        build_spy = Mock(return_value=client_instance)
        ping_spy = Mock()
        monkeypatch.setattr(database, "build_mongo_client", build_spy)
        monkeypatch.setattr(database, "ping", ping_spy)

        returned_client, returned_collection = database._initialize_mongodb_connection()

        # Factory received uri + cert path; ping got the built client.
        args, _ = build_spy.call_args
        assert args[0] == "mongodb+srv://uri"
        assert args[1] == str(cert)
        ping_spy.assert_called_once_with(client_instance)
        spy_index.assert_called_once()
        assert returned_client is client_instance
        assert returned_collection is database._mongo_collection
        assert returned_collection is not None

    def test_without_cert_file_forwards_none_cert(self, monkeypatch, no_real_env):
        """
        [Local] database connection: verifies without cert file forwards none cert.

        Scenario:
            with no cert the factory is called with cert_file=None.

        Boundaries:
            - resolve returns (uri, None); build + ping stubbed.

        On failure, first check:
            - a stray cert path is forwarded to the factory.
        """
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=("mongodb+srv://uri", None)),
        )
        monkeypatch.setattr(database, "ensure_indexes", Mock())

        client_instance = MagicMock(name="client")
        build_spy = Mock(return_value=client_instance)
        monkeypatch.setattr(database, "build_mongo_client", build_spy)
        monkeypatch.setattr(database, "ping", Mock())

        returned_client, returned_collection = database._initialize_mongodb_connection()

        args, _ = build_spy.call_args
        assert args[1] is None
        assert returned_client is client_instance
        assert returned_collection is not None

    def test_namespace_from_resolver_used_for_db_and_collection(self, monkeypatch, no_real_env):
        """
        [Local] database connection: verifies namespace from resolver used for db and collection.

        Scenario:
            the db/collection subscripts come from config.resolve_mongo_namespace, not from the MONGODB_DB_NAME / MONGODB_COLLECTION_NAME env vars (determinism when settings.json exists).

        Boundaries:
            - the resolver returns a custom namespace while the env vars name a different one.

        On failure, first check:
            - the connection targets the env-var namespace, contradicting settings.json.
        """
        monkeypatch.setenv("MONGODB_DB_NAME", "env_db")
        monkeypatch.setenv("MONGODB_COLLECTION_NAME", "env_coll")
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=("mongodb+srv://uri", None)),
        )
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_namespace",
            Mock(return_value=("settings_db", "settings_coll")),
        )
        monkeypatch.setattr(database, "ensure_indexes", Mock())
        client_instance = MagicMock(name="client")
        monkeypatch.setattr(database, "build_mongo_client", Mock(return_value=client_instance))
        monkeypatch.setattr(database, "ping", Mock())

        database._initialize_mongodb_connection()

        client_instance.__getitem__.assert_called_once_with("settings_db")
        client_instance.__getitem__.return_value.__getitem__.assert_called_once_with("settings_coll")
        assert database._mongo_db_name == "settings_db"
        assert database._mongo_collection_name == "settings_coll"

    def test_tls_ca_file_env_is_forwarded_to_factory(self, monkeypatch, no_real_env):
        """
        [Local] database connection: verifies tls ca file env is forwarded to factory.

        Scenario:
            TLS_CA_FILE from the environment is forwarded to the factory as ca_file, so the real path pins the server CA identically to the pre-flight check.

        Boundaries:
            - TLS_CA_FILE set; build stubbed.

        On failure, first check:
            - the CA bundle is ignored on the real connection path.
        """
        monkeypatch.setenv("TLS_CA_FILE", "/path/to/ca.pem")
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=("mongodb+srv://uri", None)),
        )
        monkeypatch.setattr(database, "ensure_indexes", Mock())

        build_spy = Mock(return_value=MagicMock(name="client"))
        monkeypatch.setattr(database, "build_mongo_client", build_spy)
        monkeypatch.setattr(database, "ping", Mock())

        database._initialize_mongodb_connection()

        args, _ = build_spy.call_args
        assert args[2] == "/path/to/ca.pem"

    def test_ping_connection_error_resets_globals(self, monkeypatch, no_real_env):
        """
        [Local] database connection: verifies ping connection error resets globals.

        Scenario:
            ping fails with a redacted MongoConnectionError; the globals are reset and (None, None) is returned.

        Boundaries:
            - build succeeds, ping raises MongoConnectionError.

        On failure, first check:
            - a failed connection leaves stale globals or leaks raw pymongo text.
        """
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=("mongodb+srv://uri", None)),
        )
        monkeypatch.setattr(database, "build_mongo_client", Mock(return_value=MagicMock(name="client")))
        monkeypatch.setattr(
            database, "ping", Mock(side_effect=MongoConnectionError("OperationFailure 8000 AtlasError"))
        )

        result = database._initialize_mongodb_connection()

        assert result == (None, None)
        assert database._mongo_client is None
        assert database._mongo_db is None
        assert database._mongo_collection is None

    def test_generic_exception_resets_globals(self, monkeypatch, no_real_env):
        """
        [Local] database connection: verifies generic exception resets globals.

        Scenario:
            an unexpected non-pymongo error during init is caught defensively, globals reset, and no raw exception text is logged.

        Boundaries:
            - build_mongo_client raises a plain RuntimeError.

        On failure, first check:
            - the exception propagates or leaves stale globals.
        """
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=("mongodb+srv://uri", None)),
        )
        monkeypatch.setattr(database, "build_mongo_client", Mock(side_effect=RuntimeError("boom")))

        result = database._initialize_mongodb_connection()

        assert result == (None, None)
        assert database._mongo_client is None
        assert database._mongo_db is None
        assert database._mongo_collection is None

    def test_init_failure_does_not_leak_uri_or_host(self, monkeypatch, no_real_env, caplog):
        """
        [Local] database connection: verifies init failure does not leak uri or host.

        Scenario:
            ping fails; the resolved URI/host must appear in neither the logs nor the print_log output on the real connection path (regression for CWE-532 leakage).

        Boundaries:
            - ping raises a redacted MongoConnectionError; a fake secret URI is resolved.

        On failure, first check:
            - the connection URI/host reaches the persistent log or the console.
        """
        secret_uri = "mongodb+srv://user:pw@cluster0.example.net/db"
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=(secret_uri, None)),
        )
        monkeypatch.setattr(database, "build_mongo_client", Mock(return_value=MagicMock(name="client")))
        monkeypatch.setattr(
            database, "ping", Mock(side_effect=MongoConnectionError("OperationFailure 8000 AtlasError"))
        )

        with patch.object(database, "print_log") as mock_print, caplog.at_level(logging.DEBUG):
            database._initialize_mongodb_connection()

        printed = " ".join(str(a) for c in mock_print.call_args_list for a in c.args)
        logged = caplog.text
        for needle in ("cluster0.example.net", "user:pw", secret_uri):
            assert needle not in printed
            assert needle not in logged


# --------------------------------------------------------------------------- #
# get_mongo_collection
# --------------------------------------------------------------------------- #
class TestGetMongoCollection:
    """Tests for database.get_mongo_collection."""

    def test_returns_cached_collection_without_reinitializing(self, monkeypatch):
        """
        [Local] get mongo collection: verifies that a cached collection is returned without calling _initialize_mongodb_connection.

        Scenario:
            Given _mongo_collection is already set to a Mock and _initialize_mongodb_connection is patched as a spy
            When database.get_mongo_collection() is called
            Then the cached collection is returned and _initialize_mongodb_connection is never called.

        Boundaries:
            - The cache-hit branch; no real MongoDB connection

        On failure, first check:
            - The _mongo_collection null check in get_mongo_collection
        """
        cached = Mock(name="cached-collection")
        monkeypatch.setattr(database, "_mongo_collection", cached)
        init_spy = Mock()
        monkeypatch.setattr(database, "_initialize_mongodb_connection", init_spy)

        result = database.get_mongo_collection()

        assert result is cached
        init_spy.assert_not_called()

    def test_initializes_when_unset(self, monkeypatch):
        """
        [Local] get mongo collection: verifies that _initialize_mongodb_connection is called and the resulting collection is returned when the cache is empty.

        Scenario:
            Given _mongo_collection is None and _initialize_mongodb_connection is patched to return (client, collection)
            When database.get_mongo_collection() is called
            Then _initialize_mongodb_connection is called once and the returned collection is the one from init.

        Boundaries:
            - The cache-miss / initialization path; no real MongoDB connection

        On failure, first check:
            - The initialization trigger in get_mongo_collection
        """
        client = Mock(name="client")
        collection = Mock(name="collection")
        init_spy = Mock(return_value=(client, collection))
        monkeypatch.setattr(database, "_initialize_mongodb_connection", init_spy)

        result = database.get_mongo_collection()

        init_spy.assert_called_once_with()
        assert result is collection

    def test_initialize_raises_is_caught_and_returns_none(self, monkeypatch):
        """
        [Local] get mongo collection: verifies that a RuntimeError from _initialize_mongodb_connection is caught, returns None, and prints a FAILED message.

        Scenario:
            Given _initialize_mongodb_connection is patched to raise RuntimeError("boom")
            When database.get_mongo_collection() is called with print_log patched
            Then None is returned and print_log is called with a message containing "FAILED".

        Boundaries:
            - The error-recovery branch; the exception must not propagate

        On failure, first check:
            - The try/except in get_mongo_collection and the FAILED message
        """
        monkeypatch.setattr(
            database,
            "_initialize_mongodb_connection",
            Mock(side_effect=RuntimeError("boom")),
        )
        with patch.object(database, "print_log") as mock_print:
            result = database.get_mongo_collection()

        # The except branch swallows the error and leaves the collection unset.
        assert result is None
        printed = [c.args[0] for c in mock_print.call_args_list]
        assert any("FAILED" in msg for msg in printed)

    def test_success_message_only_printed_when_collection_initialized(self, monkeypatch):
        """
        [Local] database connection: verifies success message only printed when collection initialized.

        Scenario:
            init returns a live collection, so exactly one 'success'-status line announces the connection.

        Boundaries:
            - init returns (client, non-None collection).

        On failure, first check:
            - the success line is dropped on a healthy connection, or emitted with the wrong status.
        """
        collection = Mock(name="collection")
        monkeypatch.setattr(
            database,
            "_initialize_mongodb_connection",
            Mock(return_value=(Mock(name="client"), collection)),
        )
        with patch.object(database, "print_log") as mock_print:
            result = database.get_mongo_collection()

        assert result is collection
        success_calls = [c for c in mock_print.call_args_list if c.args[1] == "success"]
        assert len(success_calls) == 1

    def test_no_success_message_when_initialization_returns_none(self, monkeypatch):
        """
        [Local] database connection: verifies no success message when initialization returns none.

        Scenario:
            init fails and returns (None, None); no contradictory 'success' line is emitted on top of the cause already logged by init and the FAILED line printed by resolve_store_backend.

        Boundaries:
            - init returns (None, None) without raising.

        On failure, first check:
            - a green 'success' line follows a red critical line, the exact pre-existing inconsistency this subtask removes.
        """
        monkeypatch.setattr(
            database,
            "_initialize_mongodb_connection",
            Mock(return_value=(None, None)),
        )
        with patch.object(database, "print_log") as mock_print:
            result = database.get_mongo_collection()

        assert result is None
        statuses = [c.args[1] for c in mock_print.call_args_list]
        assert "success" not in statuses

    def test_success_message_names_db_and_collection_not_uri(self, monkeypatch):
        """
        [Local] database connection: verifies success message names db and collection not uri.

        Scenario:
            the success line identifies the target by database.collection only, never leaking the host or connection URI into the console/log.

        Boundaries:
            - init returns a live collection; the resolved URI is never referenced by the message.

        On failure, first check:
            - the success line embeds the host/URI (credential leakage into the persistent log) or omits the database/collection identity.
        """
        monkeypatch.setattr(
            database,
            "_initialize_mongodb_connection",
            Mock(return_value=(Mock(name="client"), Mock(name="collection"))),
        )
        with patch.object(database, "print_log") as mock_print:
            database.get_mongo_collection()

        success_calls = [c for c in mock_print.call_args_list if c.args[1] == "success"]
        assert len(success_calls) == 1
        message = success_calls[0].args[0]
        assert "bookscraper_db.books" in message
        # No connection URI: neither the scheme separator nor userinfo/host markers.
        assert "://" not in message
        assert "@" not in message

    def test_success_message_uses_resolved_namespace_globals(self, monkeypatch):
        """
        [Local] database connection: verifies success message uses resolved namespace globals.

        Scenario:
            the success line reflects the namespace _initialize resolved (stored in the module globals), so it names exactly what was connected rather than a hard-coded or env-derived default.

        Boundaries:
            - the namespace globals are set to a custom namespace; init returns a live collection.

        On failure, first check:
            - the message names a different db/collection than the live connection.
        """
        monkeypatch.setattr(database, "_mongo_db_name", "resolved_db")
        monkeypatch.setattr(database, "_mongo_collection_name", "resolved_coll")
        monkeypatch.setattr(
            database,
            "_initialize_mongodb_connection",
            Mock(return_value=(Mock(name="client"), Mock(name="collection"))),
        )
        with patch.object(database, "print_log") as mock_print:
            database.get_mongo_collection()

        success_calls = [c for c in mock_print.call_args_list if c.args[1] == "success"]
        assert len(success_calls) == 1
        assert "resolved_db.resolved_coll" in success_calls[0].args[0]


# --------------------------------------------------------------------------- #
# close_mongo_connection
# --------------------------------------------------------------------------- #
class TestCloseMongoConnection:
    """Tests for database.close_mongo_connection."""

    def test_closes_client_and_resets_globals(self, monkeypatch):
        """
        [Local] close mongo connection: verifies that close_mongo_connection calls client.close() and resets all three globals to None.

        Scenario:
            Given _mongo_client, _mongo_db, _mongo_collection are all set to Mock objects
            When database.close_mongo_connection() is called
            Then client.close() is called once and all three globals are reset to None.

        Boundaries:
            - The happy-path teardown; all three module-level globals must be cleared

        On failure, first check:
            - The close call and global-reset logic in close_mongo_connection
        """
        client = Mock(name="client")
        monkeypatch.setattr(database, "_mongo_client", client)
        monkeypatch.setattr(database, "_mongo_db", Mock())
        monkeypatch.setattr(database, "_mongo_collection", Mock())

        database.close_mongo_connection()

        client.close.assert_called_once_with()
        assert database._mongo_client is None
        assert database._mongo_db is None
        assert database._mongo_collection is None

    def test_no_client_is_noop(self):
        # Globals already None via reset_globals; must not raise.
        """
        [Local] close mongo connection: verifies that close_mongo_connection is a no-op when no client is set.

        Scenario:
            Given _mongo_client is None (reset by reset_globals autouse fixture)
            When database.close_mongo_connection() is called
            Then no exception is raised and _mongo_client remains None.

        Boundaries:
            - The already-closed / never-connected case

        On failure, first check:
            - The None-client guard in close_mongo_connection
        """
        database.close_mongo_connection()
        assert database._mongo_client is None


# --------------------------------------------------------------------------- #
# save_books_to_mongodb
# --------------------------------------------------------------------------- #
class TestSaveBooksToMongodb:
    """Tests for database.save_books_to_mongodb."""

    def test_save_returns_all_errors_and_skips_io_when_collection_is_none(self, monkeypatch):
        """
        [Local] database save books: verifies save returns all errors and skips io when collection is none.

        Scenario:
            the resolved collection is None, so nothing can be written.

        Boundaries:
            - two books, no live collection; the previous code fell through and raised AttributeError per book, reporting N errors as N swallowed exceptions.

        On failure, first check:
            - a total outage is misreported, and pymongo is touched despite there being no collection.
        """
        monkeypatch.setattr(database, "get_mongo_collection", Mock(return_value=None))
        books = [{"title": "x", "hash": "h1"}, {"title": "y", "hash": "h2"}]

        with patch.object(database, "print_log") as mock_print:
            summary = database.save_books_to_mongodb(books, None)

        assert summary == SaveSummary(inserted=0, duplicates=0, errors=2)
        printed = [c.args[0] for c in mock_print.call_args_list]
        assert any("not available" in msg for msg in printed)

    def test_save_empty_list_returns_zero_summary_without_db_call(self):
        """
        [Local] database save books: verifies save empty list returns zero summary without db call.

        Scenario:
            an empty batch resolves to a zero summary and never touches the DB.

        Boundaries:
            - [] with a live collection present.

        On failure, first check:
            - an empty insert_many([]) raises InvalidOperation, or a needless round-trip is issued.
        """
        collection = Mock(name="collection")

        summary = database.save_books_to_mongodb([], collection)

        assert summary == SaveSummary(inserted=0, duplicates=0, errors=0)
        collection.insert_many.assert_not_called()

    def test_save_uses_single_unordered_insert_many(self):
        """
        [Local] database save books: verifies save uses single unordered insert many.

        Scenario:
            a 100-book batch is written with exactly one unordered insert_many.

        Boundaries:
            - 100 valid documents, all inserted.

        On failure, first check:
            - the per-book insert_one regression returns (500 round-trips), or the batch is ordered and aborts on the first duplicate.
        """
        collection = Mock(name="collection")
        collection.insert_many.return_value = Mock(inserted_ids=[f"id{i}" for i in range(100)])
        books = [{"title": f"Book {i}", "hash": f"h{i}"} for i in range(100)]

        with patch.object(database, "print_log"):
            summary = database.save_books_to_mongodb(books, collection)

        collection.insert_many.assert_called_once()
        args, kwargs = collection.insert_many.call_args
        assert len(args[0]) == 100
        assert kwargs["ordered"] is False
        assert summary == SaveSummary(inserted=100, duplicates=0, errors=0)

    def test_bulk_write_error_splits_duplicates_from_other_errors(self):
        """
        [Local] database save books: verifies bulk write error splits duplicates from other errors.

        Scenario:
            insert_many partially fails; duplicates (code 11000) and other write errors are counted apart, with nInserted as the success count.

        Boundaries:
            - details carrying two 11000 codes and one 121 (document-validation).

        On failure, first check:
            - duplicates inflate the error count (or vice versa) and the partial- failure signal misfires.
        """
        collection = Mock(name="collection")
        details = {
            "nInserted": 2,
            "writeErrors": [
                {"code": 11000, "errmsg": "dup key"},
                {"code": 11000, "errmsg": "dup key"},
                {"code": 121, "errmsg": "doc validation"},
            ],
        }
        collection.insert_many.side_effect = BulkWriteError(details)
        books = [{"title": f"Book {i}", "hash": f"h{i}"} for i in range(5)]

        with patch.object(database, "print_log"):
            summary = database.save_books_to_mongodb(books, collection)

        assert summary == SaveSummary(inserted=2, duplicates=2, errors=1)

    def test_invalid_document_counted_as_error_without_aborting_batch(self):
        """
        [Local] database save books: verifies invalid document counted as error without aborting batch.

        Scenario:
            one un-encodable document is rejected up front while the rest of the batch is still inserted.

        Boundaries:
            - a batch of one BSON-encodable and one un-encodable document.

        On failure, first check:
            - the bad document aborts the whole insert_many, so a single malformed record loses every other book in the batch.
        """
        collection = Mock(name="collection")
        collection.insert_many.return_value = Mock(inserted_ids=["id1"])
        good = {"title": "Good", "hash": "h1"}
        bad = {"title": "Bad", "hash": "h2", "broken": object()}  # object() is not BSON-encodable

        with patch.object(database, "print_log"):
            summary = database.save_books_to_mongodb([good, bad], collection)

        # Only the encodable document reaches insert_many.
        args, _ = collection.insert_many.call_args
        assert [d["hash"] for d in args[0]] == ["h1"]
        assert summary == SaveSummary(inserted=1, duplicates=0, errors=1)

    def test_all_invalid_documents_skip_insert_many(self):
        """
        [Local] database save books: verifies all invalid documents skip insert many.

        Scenario:
            every document fails pre-validation, so insert_many is never called.

        Boundaries:
            - a batch whose only member is un-encodable.

        On failure, first check:
            - insert_many([]) is issued and raises InvalidOperation.
        """
        collection = Mock(name="collection")
        bad = {"title": "Bad", "hash": "h1", "broken": object()}

        with patch.object(database, "print_log"):
            summary = database.save_books_to_mongodb([bad], collection)

        collection.insert_many.assert_not_called()
        assert summary == SaveSummary(inserted=0, duplicates=0, errors=1)

    def test_caller_dicts_are_not_mutated_with_object_id(self):
        """
        [Local] database save books: verifies caller dicts are not mutated with object id.

        Scenario:
            pymongo assigns _id in place on the docs it inserts; the caller's dicts must be untouched so a later json.dumps diagnostic write does not choke on an ObjectId.

        Boundaries:
            - insert_many stamps _id onto the doc it receives.

        On failure, first check:
            - the caller's dict gains an ObjectId _id and downstream JSON writes fail.
        """
        collection = Mock(name="collection")

        def _stamp_id(docs, ordered) -> Mock:  # signature mirrors insert_many(docs, ordered=...)
            """Side-effect mimicking pymongo's in-place _id stamping on insert_many docs."""
            for doc in docs:
                doc["_id"] = "stamped-object-id"
            return Mock(inserted_ids=["stamped-object-id"])

        collection.insert_many.side_effect = _stamp_id
        book = {"title": "Book", "hash": "h1"}

        with patch.object(database, "print_log"):
            database.save_books_to_mongodb([book], collection)

        assert "_id" not in book

    def test_unexpected_insert_many_error_counts_whole_batch_as_errors(self):
        """
        [Local] database save books: verifies unexpected insert many error counts whole batch as errors.

        Scenario:
            insert_many fails with something other than a BulkWriteError (e.g. a connection drop mid-insert); the whole encodable batch is reported as errors.

        Boundaries:
            - a generic exception raised by insert_many.

        On failure, first check:
            - the exception propagates out of save_books instead of being summarized.
        """
        collection = Mock(name="collection")
        collection.insert_many.side_effect = PyMongoError("connection lost")
        books = [{"title": f"Book {i}", "hash": f"h{i}"} for i in range(3)]

        with patch.object(database, "print_log"):
            summary = database.save_books_to_mongodb(books, collection)

        assert summary == SaveSummary(inserted=0, duplicates=0, errors=3)


# --------------------------------------------------------------------------- #
# check_amazon_asin_exists_in_db / check_book_exists_in_db
# --------------------------------------------------------------------------- #
class TestCheckAmazonAsinExists:
    """Tests for database.check_amazon_asin_exists_in_db."""

    def test_found(self):
        """
        [Local] check amazon asin exists: verifies that a matching find_one result returns True and uses the correct query.

        Scenario:
            Given a Mock collection whose find_one returns a document
            When database.check_amazon_asin_exists_in_db("B00X", collection) is called
            Then True is returned and find_one is called with {"asin": "B00X"}.

        Boundaries:
            - The "found" path; no real MongoDB

        On failure, first check:
            - The find_one query shape and return-value in check_amazon_asin_exists_in_db
        """
        collection = Mock()
        collection.find_one.return_value = {"asin": "B00X"}
        assert database.check_amazon_asin_exists_in_db("B00X", collection) is True
        collection.find_one.assert_called_once_with({"asin": "B00X"})

    def test_not_found(self):
        """
        [Local] check amazon asin exists: verifies that a None find_one result returns False.

        Scenario:
            Given a Mock collection whose find_one returns None
            When database.check_amazon_asin_exists_in_db("B00X", collection) is called
            Then False is returned.

        Boundaries:
            - The "not found" path; no real MongoDB

        On failure, first check:
            - The None-result path in check_amazon_asin_exists_in_db
        """
        collection = Mock()
        collection.find_one.return_value = None
        assert database.check_amazon_asin_exists_in_db("B00X", collection) is False

    def test_none_collection_returns_false(self, monkeypatch):
        """
        [Local] check amazon asin exists: verifies that a None collection returns False without querying.

        Scenario:
            Given get_mongo_collection is patched to return None and collection argument is also None
            When database.check_amazon_asin_exists_in_db("B00X", None) is called
            Then False is returned.

        Boundaries:
            - The None-collection short-circuit; no real MongoDB

        On failure, first check:
            - The None-collection guard in check_amazon_asin_exists_in_db
        """
        monkeypatch.setattr(database, "get_mongo_collection", Mock(return_value=None))
        assert database.check_amazon_asin_exists_in_db("B00X", None) is False

    def test_find_one_exception_returns_false(self):
        """
        [Local] check amazon asin exists: verifies that a PyMongoError from find_one is caught and returns False.

        Scenario:
            Given a Mock collection whose find_one raises PyMongoError("boom")
            When database.check_amazon_asin_exists_in_db("B00X", collection) is called
            Then the exception is swallowed and False is returned.

        Boundaries:
            - The exception-handling path; only PyMongoError exercised

        On failure, first check:
            - The try/except around find_one in check_amazon_asin_exists_in_db
        """
        collection = Mock()
        collection.find_one.side_effect = PyMongoError("boom")
        assert database.check_amazon_asin_exists_in_db("B00X", collection) is False


class TestCheckBookExistsByUrl:
    """Tests for database.check_book_exists_by_url."""

    def test_found(self):
        """
        [Local] check book exists by url: verifies a matching find_one result returns True and uses the correct query.

        Scenario:
            Given a Mock collection whose find_one returns a document
            When database.check_book_exists_by_url("https://x/p", collection) is called
            Then True is returned and find_one is called with {"url": "https://x/p"}.

        Boundaries:
            - The "found" path; no real MongoDB

        On failure, first check:
            - The find_one query shape and return-value in check_book_exists_by_url
        """
        collection = Mock()
        collection.find_one.return_value = {"url": "https://x/p"}
        assert database.check_book_exists_by_url("https://x/p", collection) is True
        collection.find_one.assert_called_once_with({"url": "https://x/p"})

    def test_not_found(self):
        """
        [Local] check book exists by url: verifies a None find_one result returns False.

        Scenario:
            Given a Mock collection whose find_one returns None
            When database.check_book_exists_by_url("https://x/p", collection) is called
            Then False is returned.

        Boundaries:
            - The "not found" path; no real MongoDB

        On failure, first check:
            - The None-result path in check_book_exists_by_url
        """
        collection = Mock()
        collection.find_one.return_value = None
        assert database.check_book_exists_by_url("https://x/p", collection) is False

    def test_empty_url_returns_false_without_querying(self):
        """
        [Local] check book exists by url: verifies an empty url returns False without querying.

        Scenario:
            Given a Mock collection
            When database.check_book_exists_by_url("", collection) is called
            Then False is returned and find_one is never called.

        Boundaries:
            - The falsy-url short-circuit; mirrors the json backend's empty-url guard

        On failure, first check:
            - The ``if not url`` guard at the top of check_book_exists_by_url
        """
        collection = Mock()
        assert database.check_book_exists_by_url("", collection) is False
        collection.find_one.assert_not_called()

    def test_none_collection_returns_false(self, monkeypatch):
        """
        [Local] check book exists by url: verifies a None collection returns False without querying.

        Scenario:
            Given get_mongo_collection is patched to return None and collection argument is also None
            When database.check_book_exists_by_url("https://x/p", None) is called
            Then False is returned.

        Boundaries:
            - The None-collection short-circuit; no real MongoDB

        On failure, first check:
            - The None-collection guard in check_book_exists_by_url
        """
        monkeypatch.setattr(database, "get_mongo_collection", Mock(return_value=None))
        assert database.check_book_exists_by_url("https://x/p", None) is False

    def test_find_one_exception_returns_false(self):
        """
        [Local] check book exists by url: verifies a PyMongoError from find_one is caught and returns False.

        Scenario:
            Given a Mock collection whose find_one raises PyMongoError("boom")
            When database.check_book_exists_by_url("https://x/p", collection) is called
            Then the exception is swallowed and False is returned.

        Boundaries:
            - The exception-handling path; only PyMongoError exercised

        On failure, first check:
            - The try/except around find_one in check_book_exists_by_url
        """
        collection = Mock()
        collection.find_one.side_effect = PyMongoError("boom")
        assert database.check_book_exists_by_url("https://x/p", collection) is False


class TestCheckBookExists:
    """Tests for database.check_book_exists_in_db."""

    def test_found(self):
        """
        [Local] check book exists: verifies that a matching find_one result returns True and uses the correct query.

        Scenario:
            Given a Mock collection whose find_one returns a document
            When database.check_book_exists_in_db("abc", collection) is called
            Then True is returned and find_one is called with {"hash": "abc"}.

        Boundaries:
            - The "found" path; no real MongoDB

        On failure, first check:
            - The find_one query shape and return-value in check_book_exists_in_db
        """
        collection = Mock()
        collection.find_one.return_value = {"hash": "abc"}
        assert database.check_book_exists_in_db("abc", collection) is True
        collection.find_one.assert_called_once_with({"hash": "abc"})

    def test_not_found(self):
        """
        [Local] check book exists: verifies that a None find_one result returns False.

        Scenario:
            Given a Mock collection whose find_one returns None
            When database.check_book_exists_in_db("abc", collection) is called
            Then False is returned.

        Boundaries:
            - The "not found" path; no real MongoDB

        On failure, first check:
            - The None-result path in check_book_exists_in_db
        """
        collection = Mock()
        collection.find_one.return_value = None
        assert database.check_book_exists_in_db("abc", collection) is False

    def test_none_collection_returns_false(self, monkeypatch):
        """
        [Local] check book exists: verifies that a None collection returns False without querying.

        Scenario:
            Given get_mongo_collection is patched to return None and collection argument is also None
            When database.check_book_exists_in_db("abc", None) is called
            Then False is returned.

        Boundaries:
            - The None-collection short-circuit; no real MongoDB

        On failure, first check:
            - The None-collection guard in check_book_exists_in_db
        """
        monkeypatch.setattr(database, "get_mongo_collection", Mock(return_value=None))
        assert database.check_book_exists_in_db("abc", None) is False

    def test_find_one_exception_returns_false(self):
        """
        [Local] check book exists: verifies that a PyMongoError from find_one is caught and returns False.

        Scenario:
            Given a Mock collection whose find_one raises PyMongoError("boom")
            When database.check_book_exists_in_db("abc", collection) is called
            Then the exception is swallowed and False is returned.

        Boundaries:
            - The exception-handling path; only PyMongoError exercised

        On failure, first check:
            - The try/except around find_one in check_book_exists_in_db
        """
        collection = Mock()
        collection.find_one.side_effect = PyMongoError("boom")
        assert database.check_book_exists_in_db("abc", collection) is False


# --------------------------------------------------------------------------- #
# update_book_isbn
# --------------------------------------------------------------------------- #
class TestUpdateBookIsbn:
    """Tests for database.update_book_isbn."""

    def test_both_isbns_set_and_modified(self):
        """
        [Local] update book isbn: verifies that both isbn10 and isbn13 are included in the $set when both are valid.

        Scenario:
            Given a Mock collection with update_one returning modified_count=1 and both isbn10="111" and isbn13="222"
            When database.update_book_isbn(collection, "abc", new_isbn10="111", new_isbn13="222") is called
            Then True is returned and update_one is called with {"$set": {"isbn10": "111", "isbn13": "222"}}.

        Boundaries:
            - Both ISBNs valid; no real MongoDB

        On failure, first check:
            - The $set construction and return-value in update_book_isbn
        """
        collection = Mock()
        collection.update_one.return_value = Mock(modified_count=1)
        result = database.update_book_isbn(collection, "abc", new_isbn10="111", new_isbn13="222")
        assert result is True
        collection.update_one.assert_called_once_with({"hash": "abc"}, {"$set": {"isbn10": "111", "isbn13": "222"}})

    def test_only_isbn10_when_isbn13_is_na(self):
        """
        [Local] update book isbn: verifies that isbn13 is omitted from $set when it is "N/A".

        Scenario:
            Given isbn10="111" and isbn13="N/A" and update_one returning modified_count=1
            When database.update_book_isbn(collection, "abc", new_isbn10="111", new_isbn13="N/A") is called
            Then True is returned and update_one is called with only {"isbn10": "111"} in $set.

        Boundaries:
            - The "N/A" filtering for isbn13; isbn10 is still valid

        On failure, first check:
            - The isbn13 "N/A" filter in update_book_isbn
        """
        collection = Mock()
        collection.update_one.return_value = Mock(modified_count=1)
        result = database.update_book_isbn(collection, "abc", new_isbn10="111", new_isbn13="N/A")
        assert result is True
        collection.update_one.assert_called_once_with({"hash": "abc"}, {"$set": {"isbn10": "111"}})

    def test_neither_valid_returns_false_without_update(self):
        """
        [Local] update book isbn: verifies that False is returned and update_one is not called when both ISBNs are invalid.

        Scenario:
            Given isbn10="N/A" and isbn13=None (both invalid)
            When database.update_book_isbn(collection, "abc", new_isbn10="N/A", new_isbn13=None) is called
            Then False is returned and update_one is never called.

        Boundaries:
            - The no-valid-isbn short-circuit path

        On failure, first check:
            - The invalid-ISBN filtering in update_book_isbn
        """
        collection = Mock()
        result = database.update_book_isbn(collection, "abc", new_isbn10="N/A", new_isbn13=None)
        assert result is False
        collection.update_one.assert_not_called()

    def test_modified_count_zero_returns_false(self):
        """
        [Local] update book isbn: verifies that False is returned when update_one reports modified_count=0.

        Scenario:
            Given update_one returning modified_count=0 and isbn10="111"
            When database.update_book_isbn(collection, "abc", new_isbn10="111") is called
            Then False is returned.

        Boundaries:
            - The no-document-matched case (update found nothing to update)

        On failure, first check:
            - The modified_count check in update_book_isbn
        """
        collection = Mock()
        collection.update_one.return_value = Mock(modified_count=0)
        result = database.update_book_isbn(collection, "abc", new_isbn10="111")
        assert result is False

    def test_none_collection_returns_false_without_calls(self):
        """
        [Local] update book isbn: verifies that a None collection returns False without calling update_one.

        Scenario:
            Given collection=None and both isbn10="111" and isbn13="222"
            When database.update_book_isbn(None, "abc", new_isbn10="111", new_isbn13="222") is called
            Then False is returned without any DB call.

        Boundaries:
            - The None-collection short-circuit; no real MongoDB

        On failure, first check:
            - The None-collection guard in update_book_isbn
        """
        result = database.update_book_isbn(None, "abc", new_isbn10="111", new_isbn13="222")
        assert result is False

    def test_update_one_exception_returns_false(self):
        """
        [Local] update book isbn: verifies that a PyMongoError from update_one is caught and returns False.

        Scenario:
            Given a Mock collection whose update_one raises PyMongoError("boom")
            When database.update_book_isbn(collection, "abc", new_isbn10="111") is called
            Then the exception is swallowed and False is returned.

        Boundaries:
            - The exception-handling path; only PyMongoError exercised

        On failure, first check:
            - The try/except around update_one in update_book_isbn
        """
        collection = Mock()
        collection.update_one.side_effect = PyMongoError("boom")
        result = database.update_book_isbn(collection, "abc", new_isbn10="111")
        assert result is False


# --------------------------------------------------------------------------- #
# ensure_indexes (and its deprecated ensure_unique_index_on_hash alias)
# --------------------------------------------------------------------------- #
_ALL_SECONDARY_NAMES = (
    "asin_lookup_index",
    "book_id_lookup_index",
    "source_id_lookup_index",
    "slug_lookup_index",
    "url_lookup_index",
)


class TestEnsureUniqueIndexOnHash:
    """Tests for the unique hash index via database.ensure_indexes."""

    def test_creates_index_when_absent(self):
        """
        [Local] ensure unique index on hash: verifies that hash_unique_index is created when it is not present.

        Scenario:
            Given a collection with only _id_ and secondary indexes (no hash_unique_index)
            When database.ensure_unique_index_on_hash(collection) is called
            Then create_index is called exactly once with ("hash", unique=True, name="hash_unique_index").

        Boundaries:
            - The index-absent creation path; secondary indexes are pre-populated so they do not trigger creation

        On failure, first check:
            - The existence check for "hash_unique_index" in ensure_unique_index_on_hash
        """
        collection = Mock()
        # Secondary indexes already present so only the hash index is created, keeping
        # this alias test focused on the hash-index behavior it has always covered.
        collection.index_information.return_value = {"_id_": {}, **{name: {} for name in _ALL_SECONDARY_NAMES}}
        database.ensure_unique_index_on_hash(collection)
        collection.create_index.assert_called_once_with("hash", unique=True, name="hash_unique_index")

    def test_skips_create_when_present(self):
        """
        [Local] ensure unique index on hash: verifies that create_index is not called when hash_unique_index already exists.

        Scenario:
            Given a collection whose index_information includes "hash_unique_index"
            When database.ensure_unique_index_on_hash(collection) is called
            Then create_index is never called.

        Boundaries:
            - The idempotent / already-present path

        On failure, first check:
            - The existence check for "hash_unique_index" in ensure_unique_index_on_hash
        """
        collection = Mock()
        collection.index_information.return_value = {
            "hash_unique_index": {},
            **{name: {} for name in _ALL_SECONDARY_NAMES},
        }
        database.ensure_unique_index_on_hash(collection)
        collection.create_index.assert_not_called()

    def test_none_collection_is_noop(self):
        # Must not raise.
        """
        [Local] ensure unique index on hash: verifies that a None collection is a no-op.

        Scenario:
            Given collection=None
            When database.ensure_unique_index_on_hash(None) is called
            Then no exception is raised.

        Boundaries:
            - The None-collection short-circuit

        On failure, first check:
            - The None-collection guard in ensure_unique_index_on_hash
        """
        database.ensure_unique_index_on_hash(None)

    def test_exception_is_caught(self):
        """
        [Local] ensure unique index on hash: verifies that a PyMongoError from index_information is caught and does not propagate.

        Scenario:
            Given a Mock collection whose index_information raises PyMongoError("boom")
            When database.ensure_unique_index_on_hash(collection) is called
            Then no exception propagates.

        Boundaries:
            - The exception-handling path; only PyMongoError exercised

        On failure, first check:
            - The try/except in ensure_unique_index_on_hash
        """
        collection = Mock()
        collection.index_information.side_effect = PyMongoError("boom")
        # Must not propagate.
        database.ensure_unique_index_on_hash(collection)


class TestEnsureIndexes:
    """Tests for database.ensure_indexes."""

    def test_ensure_indexes_creates_all_six_when_absent(self):
        """
        [Local] database ensure indexes: verifies ensure indexes creates all six when absent.

        Scenario:
            an empty collection gets the unique hash index plus all five sparse secondary lookup indexes created in one call.

        Boundaries:
            - index_information() reports only the implicit _id_ index. The source_id index is the schema-2 (03.1) home of the Leanpub book id, indexed alongside the legacy book_id; the url index backs check_book_exists_by_url for a URL-keyed provider (03.3 subtask 03).

        On failure, first check:
            - a lookup field is left unindexed, so pre-scrape dedup falls back to a full collection scan.
        """
        collection = Mock()
        collection.index_information.return_value = {"_id_": {}}
        database.ensure_indexes(collection)
        collection.create_index.assert_any_call("hash", unique=True, name="hash_unique_index")
        collection.create_index.assert_any_call("asin", sparse=True, name="asin_lookup_index")
        collection.create_index.assert_any_call("book_id", sparse=True, name="book_id_lookup_index")
        collection.create_index.assert_any_call("source_id", sparse=True, name="source_id_lookup_index")
        collection.create_index.assert_any_call("slug", sparse=True, name="slug_lookup_index")
        collection.create_index.assert_any_call("url", sparse=True, name="url_lookup_index")
        assert collection.create_index.call_count == 6

    def test_ensure_indexes_is_idempotent_when_all_present(self):
        """
        [Local] database ensure indexes: verifies ensure indexes is idempotent when all present.

        Scenario:
            every index already exists, so a repeat call creates nothing.

        Boundaries:
            - index_information() reports all four named indexes.

        On failure, first check:
            - a redundant create_index round-trip is issued, or a duplicate-index error is raised on a healthy collection.
        """
        collection = Mock()
        collection.index_information.return_value = {
            "hash_unique_index": {},
            **{name: {} for name in _ALL_SECONDARY_NAMES},
        }
        database.ensure_indexes(collection)
        collection.create_index.assert_not_called()

    def test_secondary_index_failure_warns_and_continues(self):
        """
        [Local] database ensure indexes: verifies secondary index failure warns and continues.

        Scenario:
            creating the first secondary index fails; the remaining secondary indexes are still created and the failure is only a warning.

        Boundaries:
            - only the hash index pre-exists; the asin index create raises.

        On failure, first check:
            - one bad index aborts the whole call, leaving later indexes missing.
        """
        collection = Mock()
        collection.index_information.return_value = {"hash_unique_index": {}}

        def create_index(field, **kwargs):
            """Raise PyMongoError only for the 'asin' field to test partial-failure handling."""
            if field == "asin":
                raise PyMongoError("boom")

        collection.create_index.side_effect = create_index

        with patch.object(database, "print_log") as mock_print:
            database.ensure_indexes(collection)

        attempted = [c.args[0] for c in collection.create_index.call_args_list]
        assert attempted == ["asin", "book_id", "source_id", "slug", "url"]
        printed = [c.args[0] for c in mock_print.call_args_list]
        assert any("Warning: Failed to ensure sparse index on 'asin'" in msg for msg in printed)

    def test_secondary_indexes_are_sparse_and_non_unique(self):
        """
        [Local] database ensure indexes: verifies secondary indexes are sparse and non unique.

        Scenario:
            the secondary indexes are created sparse and never unique, since two editions can legitimately share a slug across sites.

        Boundaries:
            - empty collection so all secondary indexes are created.

        On failure, first check:
            - a unique secondary index would reject legitimate cross-site editions.
        """
        collection = Mock()
        collection.index_information.return_value = {"_id_": {}}
        database.ensure_indexes(collection)

        for call in collection.create_index.call_args_list:
            field, kwargs = call.args[0], call.kwargs
            if field == "hash":
                continue
            assert kwargs.get("sparse") is True
            assert "unique" not in kwargs

    def test_none_collection_is_noop(self):
        # Must not raise.
        """
        [Local] ensure indexes: verifies that a None collection is a no-op.

        Scenario:
            Given collection=None
            When database.ensure_indexes(None) is called
            Then no exception is raised.

        Boundaries:
            - The None-collection short-circuit in ensure_indexes

        On failure, first check:
            - The None-collection guard in ensure_indexes
        """
        database.ensure_indexes(None)

    def test_index_information_failure_aborts(self):
        """
        [Local] database ensure indexes: verifies index information failure aborts.

        Scenario:
            reading index_information() fails, so no index is attempted and the error keeps the prior hash-index failure behavior.

        Boundaries:
            - index_information() raises before any create_index call.

        On failure, first check:
            - the exception propagates, or secondary indexes are attempted against an unreadable collection.
        """
        collection = Mock()
        collection.index_information.side_effect = PyMongoError("boom")
        database.ensure_indexes(collection)
        collection.create_index.assert_not_called()


# --------------------------------------------------------------------------- #
# Re-export surface
#
# The X.509 helpers and the factory now live in client.py but are re-exported from
# database.py so pre-existing `from ...database import ...` imports keep working. The
# behavioral tests for those helpers live in tests/mock/backends/mongo/test_client.py.
# --------------------------------------------------------------------------- #
def test_x509_and_factory_symbols_are_reexported_from_client():
    """
    [Local] database: verifies x509 and factory symbols are reexported from client.

    Scenario:
        database.py re-exports the moved names so existing imports/patch targets keep resolving to the single client.py definitions.

    Boundaries:
        - identity comparison against the client module's objects.

    On failure, first check:
        - a stale duplicate definition drifts from the factory.
    """

    assert database.uri_selects_x509_auth is client.uri_selects_x509_auth
    assert database.uri_has_embedded_credentials is client.uri_has_embedded_credentials
    assert database._warn_if_x509_mechanism_missing is client._warn_if_x509_mechanism_missing
    assert database.build_mongo_client is client.build_mongo_client
    assert database.ping is client.ping
    assert database.MongoConnectionError is client.MongoConnectionError
