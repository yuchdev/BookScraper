"""Mock-tier tests for bookscraper.backends.mongo.database.

The module holds _mongo_client / _mongo_db / _mongo_collection as mutable
module-level globals; the autouse reset_globals fixture forces all three back to
None before every test so state never leaks between cases. MongoClient and the
config resolver are patched at their point of use inside the database module, so
no real Atlas connection is ever attempted. load_dotenv is neutered so a repo-root
.env can't leak real values into a test.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from pymongo.errors import (
    ConnectionFailure,
    DuplicateKeyError,
    InvalidDocument,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from bookscraper.backends.mongo import config, database


@pytest.fixture(autouse=True)
def reset_globals(monkeypatch):
    """Reset the module-level connection globals and stop load_dotenv from reading
    a real .env for every test in this module."""
    monkeypatch.setattr(database, "_mongo_client", None)
    monkeypatch.setattr(database, "_mongo_db", None)
    monkeypatch.setattr(database, "_mongo_collection", None)
    monkeypatch.setattr(database, "load_dotenv", lambda *a, **k: None)
    yield


# --------------------------------------------------------------------------- #
# _initialize_mongodb_connection
# --------------------------------------------------------------------------- #
class TestInitializeMongodbConnection:
    def test_config_error_returns_none_none(self, monkeypatch, no_real_env) -> None:
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(side_effect=config.ConfigError("bad config")),
        )
        with patch.object(database, "MongoClient") as mock_client:
            result = database._initialize_mongodb_connection()

        assert result == (None, None)
        mock_client.assert_not_called()

    def test_missing_uri_returns_none_none(self, monkeypatch, no_real_env) -> None:
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=(None, None)),
        )
        with patch.object(database, "MongoClient") as mock_client:
            result = database._initialize_mongodb_connection()

        assert result == (None, None)
        mock_client.assert_not_called()

    def test_missing_cert_file_returns_none_none(self, monkeypatch, no_real_env, tmp_path) -> None:
        missing = str(tmp_path / "does_not_exist.pem")
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=("mongodb+srv://uri", missing)),
        )
        with patch.object(database, "MongoClient") as mock_client:
            result = database._initialize_mongodb_connection()

        assert result == (None, None)
        mock_client.assert_not_called()

    def test_with_cert_file_builds_tls_client_and_pings(self, monkeypatch, no_real_env, tmp_path) -> None:
        cert = tmp_path / "cert.pem"
        cert.write_text("dummy-cert")
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=("mongodb+srv://uri", str(cert))),
        )
        spy_index = Mock()
        monkeypatch.setattr(database, "ensure_unique_index_on_hash", spy_index)

        # MagicMock so `_mongo_client[db_name][coll_name]` subscripting works.
        client_instance = MagicMock(name="client")
        with patch.object(database, "MongoClient", return_value=client_instance) as mock_client:
            returned_client, returned_collection = database._initialize_mongodb_connection()

        # TLS client-cert kwargs must be present.
        _, kwargs = mock_client.call_args
        assert kwargs["tls"] is True
        assert kwargs["tlsCertificateKeyFile"] == str(cert)
        # ping was issued on the admin database.
        client_instance.admin.command.assert_called_once_with("ping")
        # unique index ensured on the resolved collection.
        spy_index.assert_called_once()
        assert returned_client is client_instance
        assert returned_collection is database._mongo_collection
        assert returned_collection is not None

    def test_without_cert_file_builds_client_without_tls_cert_kwargs(self, monkeypatch, no_real_env) -> None:
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=("mongodb+srv://uri", None)),
        )
        monkeypatch.setattr(database, "ensure_unique_index_on_hash", Mock())

        client_instance = MagicMock(name="client")
        with patch.object(database, "MongoClient", return_value=client_instance) as mock_client:
            returned_client, returned_collection = database._initialize_mongodb_connection()

        _, kwargs = mock_client.call_args
        assert "tlsCertificateKeyFile" not in kwargs
        assert "tls" not in kwargs
        assert returned_client is client_instance
        assert returned_collection is not None

    def test_tls_ca_file_env_is_passed_through(self, monkeypatch, no_real_env) -> None:
        monkeypatch.setenv("TLS_CA_FILE", "/path/to/ca.pem")
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=("mongodb+srv://uri", None)),
        )
        monkeypatch.setattr(database, "ensure_unique_index_on_hash", Mock())

        client_instance = MagicMock(name="client")
        with patch.object(database, "MongoClient", return_value=client_instance) as mock_client:
            database._initialize_mongodb_connection()

        _, kwargs = mock_client.call_args
        assert kwargs["tlsCAFile"] == "/path/to/ca.pem"

    @pytest.mark.parametrize(
        "ping_error",
        [ConnectionFailure("down"), ServerSelectionTimeoutError("timeout")],
    )
    def test_ping_connection_error_resets_globals(self, monkeypatch, no_real_env, ping_error) -> None:
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=("mongodb+srv://uri", None)),
        )
        client_instance = Mock(name="client")
        client_instance.admin.command.side_effect = ping_error
        with patch.object(database, "MongoClient", return_value=client_instance):
            result = database._initialize_mongodb_connection()

        assert result == (None, None)
        assert database._mongo_client is None
        assert database._mongo_db is None
        assert database._mongo_collection is None

    def test_generic_exception_resets_globals(self, monkeypatch, no_real_env) -> None:
        monkeypatch.setattr(
            database.config,
            "resolve_mongo_connection",
            Mock(return_value=("mongodb+srv://uri", None)),
        )
        with patch.object(database, "MongoClient", side_effect=RuntimeError("boom")):
            result = database._initialize_mongodb_connection()

        assert result == (None, None)
        assert database._mongo_client is None
        assert database._mongo_db is None
        assert database._mongo_collection is None


# --------------------------------------------------------------------------- #
# get_mongo_collection
# --------------------------------------------------------------------------- #
class TestGetMongoCollection:
    def test_returns_cached_collection_without_reinitializing(self, monkeypatch) -> None:
        cached = Mock(name="cached-collection")
        monkeypatch.setattr(database, "_mongo_collection", cached)
        init_spy = Mock()
        monkeypatch.setattr(database, "_initialize_mongodb_connection", init_spy)

        result = database.get_mongo_collection()

        assert result is cached
        init_spy.assert_not_called()

    def test_initializes_when_unset(self, monkeypatch) -> None:
        client = Mock(name="client")
        collection = Mock(name="collection")
        init_spy = Mock(return_value=(client, collection))
        monkeypatch.setattr(database, "_initialize_mongodb_connection", init_spy)

        result = database.get_mongo_collection()

        init_spy.assert_called_once_with()
        assert result is collection

    def test_initialize_raises_is_caught_and_returns_none(self, monkeypatch) -> None:
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


# --------------------------------------------------------------------------- #
# close_mongo_connection
# --------------------------------------------------------------------------- #
class TestCloseMongoConnection:
    def test_closes_client_and_resets_globals(self, monkeypatch) -> None:
        client = Mock(name="client")
        monkeypatch.setattr(database, "_mongo_client", client)
        monkeypatch.setattr(database, "_mongo_db", Mock())
        monkeypatch.setattr(database, "_mongo_collection", Mock())

        database.close_mongo_connection()

        client.close.assert_called_once_with()
        assert database._mongo_client is None
        assert database._mongo_db is None
        assert database._mongo_collection is None

    def test_no_client_is_noop(self) -> None:
        # Globals already None via reset_globals; must not raise.
        database.close_mongo_connection()
        assert database._mongo_client is None


# --------------------------------------------------------------------------- #
# save_books_to_mongodb
# --------------------------------------------------------------------------- #
class TestSaveBooksToMongodb:
    def test_all_insert_branches_counted(self) -> None:
        collection = Mock(name="collection")
        collection.insert_one.side_effect = [
            Mock(acknowledged=True, inserted_id="id1"),
            Mock(acknowledged=False),
            DuplicateKeyError("dup"),
            InvalidDocument("bad"),
            OperationFailure("op"),
            RuntimeError("boom"),
        ]
        books = [{"title": f"Book {i}", "hash": f"h{i}"} for i in range(6)]

        with patch.object(database, "print_log") as mock_print:
            database.save_books_to_mongodb(books, collection)

        assert collection.insert_one.call_count == 6
        summary_calls = [c.args[0] for c in mock_print.call_args_list if "insertion summary" in c.args[0]]
        assert summary_calls, "expected a summary print_log call"
        # 1 acknowledged insert, 1 duplicate, 4 errors (unacked + invalid + opfail + generic).
        assert "1 new, 1 duplicates, 4 errors." in summary_calls[-1]

    def test_none_collection_logs_error_and_does_not_raise(self, monkeypatch) -> None:
        monkeypatch.setattr(database, "get_mongo_collection", Mock(return_value=None))
        books = [{"title": "x", "hash": "h"}]

        with patch.object(database, "print_log") as mock_print:
            # Real behavior: books_collection stays None, the not-available error is
            # logged, then None.insert_one raises AttributeError which the generic
            # except catches - so this must NOT propagate.
            database.save_books_to_mongodb(books, None)

        printed = [c.args[0] for c in mock_print.call_args_list]
        assert any("not available" in msg for msg in printed)


# --------------------------------------------------------------------------- #
# check_amazon_asin_exists_in_db / check_book_exists_in_db
# --------------------------------------------------------------------------- #
class TestCheckAmazonAsinExists:
    def test_found(self) -> None:
        collection = Mock()
        collection.find_one.return_value = {"asin": "B00X"}
        assert database.check_amazon_asin_exists_in_db("B00X", collection) is True
        collection.find_one.assert_called_once_with({"asin": "B00X"})

    def test_not_found(self) -> None:
        collection = Mock()
        collection.find_one.return_value = None
        assert database.check_amazon_asin_exists_in_db("B00X", collection) is False

    def test_none_collection_returns_false(self, monkeypatch) -> None:
        monkeypatch.setattr(database, "get_mongo_collection", Mock(return_value=None))
        assert database.check_amazon_asin_exists_in_db("B00X", None) is False

    def test_find_one_exception_returns_false(self) -> None:
        collection = Mock()
        collection.find_one.side_effect = RuntimeError("boom")
        assert database.check_amazon_asin_exists_in_db("B00X", collection) is False


class TestCheckBookExists:
    def test_found(self) -> None:
        collection = Mock()
        collection.find_one.return_value = {"hash": "abc"}
        assert database.check_book_exists_in_db("abc", collection) is True
        collection.find_one.assert_called_once_with({"hash": "abc"})

    def test_not_found(self) -> None:
        collection = Mock()
        collection.find_one.return_value = None
        assert database.check_book_exists_in_db("abc", collection) is False

    def test_none_collection_returns_false(self, monkeypatch) -> None:
        monkeypatch.setattr(database, "get_mongo_collection", Mock(return_value=None))
        assert database.check_book_exists_in_db("abc", None) is False

    def test_find_one_exception_returns_false(self) -> None:
        collection = Mock()
        collection.find_one.side_effect = RuntimeError("boom")
        assert database.check_book_exists_in_db("abc", collection) is False


# --------------------------------------------------------------------------- #
# update_book_isbn
# --------------------------------------------------------------------------- #
class TestUpdateBookIsbn:
    def test_both_isbns_set_and_modified(self) -> None:
        collection = Mock()
        collection.update_one.return_value = Mock(modified_count=1)
        result = database.update_book_isbn(collection, "abc", new_isbn10="111", new_isbn13="222")
        assert result is True
        collection.update_one.assert_called_once_with({"hash": "abc"}, {"$set": {"isbn10": "111", "isbn13": "222"}})

    def test_only_isbn10_when_isbn13_is_na(self) -> None:
        collection = Mock()
        collection.update_one.return_value = Mock(modified_count=1)
        result = database.update_book_isbn(collection, "abc", new_isbn10="111", new_isbn13="N/A")
        assert result is True
        collection.update_one.assert_called_once_with({"hash": "abc"}, {"$set": {"isbn10": "111"}})

    def test_neither_valid_returns_false_without_update(self) -> None:
        collection = Mock()
        result = database.update_book_isbn(collection, "abc", new_isbn10="N/A", new_isbn13=None)
        assert result is False
        collection.update_one.assert_not_called()

    def test_modified_count_zero_returns_false(self) -> None:
        collection = Mock()
        collection.update_one.return_value = Mock(modified_count=0)
        result = database.update_book_isbn(collection, "abc", new_isbn10="111")
        assert result is False

    def test_none_collection_returns_false_without_calls(self) -> None:
        result = database.update_book_isbn(None, "abc", new_isbn10="111", new_isbn13="222")
        assert result is False

    def test_update_one_exception_returns_false(self) -> None:
        collection = Mock()
        collection.update_one.side_effect = RuntimeError("boom")
        result = database.update_book_isbn(collection, "abc", new_isbn10="111")
        assert result is False


# --------------------------------------------------------------------------- #
# ensure_unique_index_on_hash
# --------------------------------------------------------------------------- #
class TestEnsureUniqueIndexOnHash:
    def test_creates_index_when_absent(self) -> None:
        collection = Mock()
        collection.index_information.return_value = {"_id_": {}}
        database.ensure_unique_index_on_hash(collection)
        collection.create_index.assert_called_once_with("hash", unique=True, name="hash_unique_index")

    def test_skips_create_when_present(self) -> None:
        collection = Mock()
        collection.index_information.return_value = {"hash_unique_index": {}}
        database.ensure_unique_index_on_hash(collection)
        collection.create_index.assert_not_called()

    def test_none_collection_is_noop(self) -> None:
        # Must not raise.
        database.ensure_unique_index_on_hash(None)

    def test_exception_is_caught(self) -> None:
        collection = Mock()
        collection.index_information.side_effect = RuntimeError("boom")
        # Must not propagate.
        database.ensure_unique_index_on_hash(collection)


# --------------------------------------------------------------------------- #
# X.509 auth-mechanism diagnostics
#
# pymongo does not infer MONGODB-X509 from a client certificate, so a cert paired
# with a URI that never requests it yields an unauthenticated connection. These
# cover the detection helpers and the three-way warning branch.
# --------------------------------------------------------------------------- #
X509_URI = "mongodb+srv://cluster.example.net/db?authSource=%24external&authMechanism=MONGODB-X509"
SCRAM_URI = "mongodb+srv://user:pw@cluster.example.net/db?retryWrites=true"
BARE_URI = "mongodb+srv://cluster.example.net/db?retryWrites=true"


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
    assert database.uri_selects_x509_auth(uri) is expected


@pytest.mark.parametrize(
    "uri, expected",
    [(SCRAM_URI, True), (X509_URI, False), (BARE_URI, False)],
)
def test_uri_has_embedded_credentials(uri, expected) -> None:
    assert database.uri_has_embedded_credentials(uri) is expected


def test_x509_mechanism_present_warns_nothing() -> None:
    with (
        patch.object(database.module_logger, "warning") as warn,
        patch.object(database.module_logger, "critical") as crit,
        patch.object(database, "print_log") as printed,
    ):
        database._warn_if_x509_mechanism_missing(X509_URI, "/certs/x.pem")

    warn.assert_not_called()
    crit.assert_not_called()
    printed.assert_not_called()


def test_scram_uri_with_stray_cert_warns_but_does_not_escalate() -> None:
    """Legacy resolution attaches the newest cert to any URI, so a working SCRAM
    setup can pick one up incidentally - that is a warning, not a critical."""
    with (
        patch.object(database.module_logger, "warning") as warn,
        patch.object(database.module_logger, "critical") as crit,
        patch.object(database, "print_log") as printed,
    ):
        database._warn_if_x509_mechanism_missing(SCRAM_URI, "/certs/x.pem")

    crit.assert_not_called()
    printed.assert_not_called()
    assert "SCRAM" in warn.call_args.args[0]


def test_cert_without_mechanism_or_credentials_is_critical() -> None:
    """No mechanism and no userinfo means the connection authenticates as nobody."""
    with (
        patch.object(database.module_logger, "critical") as crit,
        patch.object(database, "print_log") as printed,
    ):
        database._warn_if_x509_mechanism_missing(BARE_URI, "/certs/x.pem")

    assert "will not be authenticated" in crit.call_args.args[0]
    assert "MONGODB-X509" in crit.call_args.args[0]
    assert printed.call_args.args[1] == "warning"
