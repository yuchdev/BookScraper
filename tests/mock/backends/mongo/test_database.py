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
        """
        [Mock] initialize mongodb connection: verifies config error returns none none.

        Scenario:
            Given test inputs and fixtures for config error returns none none
            When test_config_error_returns_none_none executes the target flow
            Then the expected outcome for config error returns none none is confirmed.

        Boundaries:
            - Focus: config error returns none none
            - Fixtures/params: monkeypatch, no_real_env
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to config error returns none none
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by initialize mongodb connection
        """
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
        """
        [Mock] initialize mongodb connection: verifies missing uri returns none none.

        Scenario:
            Given test inputs and fixtures for missing uri returns none none
            When test_missing_uri_returns_none_none executes the target flow
            Then the expected outcome for missing uri returns none none is confirmed.

        Boundaries:
            - Focus: missing uri returns none none
            - Fixtures/params: monkeypatch, no_real_env
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to missing uri returns none none
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by initialize mongodb connection
        """
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
        """
        [Mock] initialize mongodb connection: verifies missing cert file returns none none.

        Scenario:
            Given test inputs and fixtures for missing cert file returns none none
            When test_missing_cert_file_returns_none_none executes the target flow
            Then the expected outcome for missing cert file returns none none is confirmed.

        Boundaries:
            - Focus: missing cert file returns none none
            - Fixtures/params: monkeypatch, no_real_env, tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to missing cert file returns none none
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by initialize mongodb connection
        """
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
        """
        [Mock] initialize mongodb connection: verifies with cert file builds tls client and pings.

        Scenario:
            Given test inputs and fixtures for with cert file builds tls client and pings
            When test_with_cert_file_builds_tls_client_and_pings executes the target flow
            Then the expected outcome for with cert file builds tls client and pings is confirmed.

        Boundaries:
            - Focus: with cert file builds tls client and pings
            - Fixtures/params: monkeypatch, no_real_env, tmp_path
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to with cert file builds tls client and pings
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by initialize mongodb connection
        """
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

    def test_ensure_indexes_false_never_creates_index(self, monkeypatch, no_real_env) -> None:
        """Mock: read-only callers (pull, push --dry-run, --check-mongo) connect without index creation.

        Scenario: _initialize_mongodb_connection(ensure_indexes=False) with a mocked client.
        Boundaries: MongoClient and ensure_unique_index_on_hash are mocked; no network.
        On failure: a read-only command would create an index (a write) on the real collection.
        """
        monkeypatch.setattr(database.config, "resolve_mongo_connection", Mock(return_value=("mongodb+srv://uri", None)))
        spy_index = Mock()
        monkeypatch.setattr(database, "ensure_unique_index_on_hash", spy_index)
        with patch.object(database, "MongoClient", return_value=MagicMock(name="client")):
            _, collection = database._initialize_mongodb_connection(ensure_indexes=False)

        assert collection is not None
        spy_index.assert_not_called()

    def test_without_cert_file_builds_client_without_tls_cert_kwargs(self, monkeypatch, no_real_env) -> None:
        """
        [Mock] initialize mongodb connection: verifies without cert file builds client without tls cert kwargs.

        Scenario:
            Given test inputs and fixtures for without cert file builds client without tls cert kwargs
            When test_without_cert_file_builds_client_without_tls_cert_kwargs executes the target flow
            Then the expected outcome for without cert file builds client without tls cert kwargs is confirmed.

        Boundaries:
            - Focus: without cert file builds client without tls cert kwargs
            - Fixtures/params: monkeypatch, no_real_env
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to without cert file builds client without tls cert kwargs
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by initialize mongodb connection
        """
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
        """
        [Mock] initialize mongodb connection: verifies tls ca file env is passed through.

        Scenario:
            Given test inputs and fixtures for tls ca file env is passed through
            When test_tls_ca_file_env_is_passed_through executes the target flow
            Then the expected outcome for tls ca file env is passed through is confirmed.

        Boundaries:
            - Focus: tls ca file env is passed through
            - Fixtures/params: monkeypatch, no_real_env
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to tls ca file env is passed through
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by initialize mongodb connection
        """
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
        """
        [Mock] initialize mongodb connection: verifies ping connection error resets globals.

        Scenario:
            Given test inputs and fixtures for ping connection error resets globals
            When test_ping_connection_error_resets_globals executes the target flow
            Then the expected outcome for ping connection error resets globals is confirmed.

        Boundaries:
            - Focus: ping connection error resets globals
            - Fixtures/params: monkeypatch, no_real_env, ping_error (parametrized, 2 cases)
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to ping connection error resets globals
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by initialize mongodb connection
        """
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
        """
        [Mock] initialize mongodb connection: verifies generic exception resets globals.

        Scenario:
            Given test inputs and fixtures for generic exception resets globals
            When test_generic_exception_resets_globals executes the target flow
            Then the expected outcome for generic exception resets globals is confirmed.

        Boundaries:
            - Focus: generic exception resets globals
            - Fixtures/params: monkeypatch, no_real_env
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to generic exception resets globals
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by initialize mongodb connection
        """
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
        """
        [Mock] get mongo collection: verifies returns cached collection without reinitializing.

        Scenario:
            Given test inputs and fixtures for returns cached collection without reinitializing
            When test_returns_cached_collection_without_reinitializing executes the target flow
            Then the expected outcome for returns cached collection without reinitializing is confirmed.

        Boundaries:
            - Focus: returns cached collection without reinitializing
            - Fixtures/params: monkeypatch
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to returns cached collection without reinitializing
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by get mongo collection
        """
        cached = Mock(name="cached-collection")
        monkeypatch.setattr(database, "_mongo_collection", cached)
        init_spy = Mock()
        monkeypatch.setattr(database, "_initialize_mongodb_connection", init_spy)

        result = database.get_mongo_collection()

        assert result is cached
        init_spy.assert_not_called()

    def test_initializes_when_unset(self, monkeypatch) -> None:
        """
        [Mock] get mongo collection: verifies initializes when unset.

        Scenario:
            Given test inputs and fixtures for initializes when unset
            When test_initializes_when_unset executes the target flow
            Then the expected outcome for initializes when unset is confirmed.

        Boundaries:
            - Focus: initializes when unset
            - Fixtures/params: monkeypatch
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to initializes when unset
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by get mongo collection
        """
        client = Mock(name="client")
        collection = Mock(name="collection")
        init_spy = Mock(return_value=(client, collection))
        monkeypatch.setattr(database, "_initialize_mongodb_connection", init_spy)

        result = database.get_mongo_collection()

        init_spy.assert_called_once_with(ensure_indexes=True)
        assert result is collection

    def test_failed_initialize_does_not_claim_success(self, monkeypatch) -> None:
        """Mock: a None collection from initialization never prints the 'successful' message.

        Scenario: _initialize_mongodb_connection returns (None, None).
        Boundaries: initialization is mocked; print_log is captured.
        On failure: the console claims a MongoDB connection that does not exist (0001 02.0/04's bug).
        """
        monkeypatch.setattr(database, "_initialize_mongodb_connection", Mock(return_value=(None, None)))
        with patch.object(database, "print_log") as mock_print:
            result = database.get_mongo_collection(ensure_indexes=False)

        assert result is None
        assert not any("successful" in c.args[0] for c in mock_print.call_args_list)

    def test_initialize_raises_is_caught_and_returns_none(self, monkeypatch) -> None:
        """
        [Mock] get mongo collection: verifies initialize raises is caught and returns none.

        Scenario:
            Given test inputs and fixtures for initialize raises is caught and returns none
            When test_initialize_raises_is_caught_and_returns_none executes the target flow
            Then the expected outcome for initialize raises is caught and returns none is confirmed.

        Boundaries:
            - Focus: initialize raises is caught and returns none
            - Fixtures/params: monkeypatch
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to initialize raises is caught and returns none
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by get mongo collection
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


# --------------------------------------------------------------------------- #
# close_mongo_connection
# --------------------------------------------------------------------------- #
class TestCloseMongoConnection:
    def test_closes_client_and_resets_globals(self, monkeypatch) -> None:
        """
        [Mock] close mongo connection: verifies closes client and resets globals.

        Scenario:
            Given test inputs and fixtures for closes client and resets globals
            When test_closes_client_and_resets_globals executes the target flow
            Then the expected outcome for closes client and resets globals is confirmed.

        Boundaries:
            - Focus: closes client and resets globals
            - Fixtures/params: monkeypatch
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to closes client and resets globals
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by close mongo connection
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

    def test_no_client_is_noop(self) -> None:
        # Globals already None via reset_globals; must not raise.
        """
        [Unit] close mongo connection: verifies no client is noop.

        Scenario:
            Given test inputs and fixtures for no client is noop
            When test_no_client_is_noop executes the target flow
            Then the expected outcome for no client is noop is confirmed.

        Boundaries:
            - Focus: no client is noop
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to no client is noop
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by close mongo connection
        """
        database.close_mongo_connection()
        assert database._mongo_client is None


# --------------------------------------------------------------------------- #
# check_amazon_asin_exists_in_db / check_book_exists_in_db
# --------------------------------------------------------------------------- #
class TestCheckAmazonAsinExists:
    def test_found(self) -> None:
        """
        [Mock] check amazon asin exists: verifies found.

        Scenario:
            Given test inputs and fixtures for found
            When test_found executes the target flow
            Then the expected outcome for found is confirmed.

        Boundaries:
            - Focus: found
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to found
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check amazon asin exists
        """
        collection = Mock()
        collection.find_one.return_value = {"asin": "B00X"}
        assert database.check_amazon_asin_exists_in_db("B00X", collection) is True
        collection.find_one.assert_called_once_with({"asin": "B00X"})

    def test_not_found(self) -> None:
        """
        [Mock] check amazon asin exists: verifies not found.

        Scenario:
            Given test inputs and fixtures for not found
            When test_not_found executes the target flow
            Then the expected outcome for not found is confirmed.

        Boundaries:
            - Focus: not found
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to not found
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check amazon asin exists
        """
        collection = Mock()
        collection.find_one.return_value = None
        assert database.check_amazon_asin_exists_in_db("B00X", collection) is False

    def test_none_collection_returns_false(self, monkeypatch) -> None:
        """
        [Mock] check amazon asin exists: verifies none collection returns false.

        Scenario:
            Given test inputs and fixtures for none collection returns false
            When test_none_collection_returns_false executes the target flow
            Then the expected outcome for none collection returns false is confirmed.

        Boundaries:
            - Focus: none collection returns false
            - Fixtures/params: monkeypatch
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to none collection returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check amazon asin exists
        """
        monkeypatch.setattr(database, "get_mongo_collection", Mock(return_value=None))
        assert database.check_amazon_asin_exists_in_db("B00X", None) is False

    def test_find_one_exception_returns_false(self) -> None:
        """
        [Mock] check amazon asin exists: verifies find one exception returns false.

        Scenario:
            Given test inputs and fixtures for find one exception returns false
            When test_find_one_exception_returns_false executes the target flow
            Then the expected outcome for find one exception returns false is confirmed.

        Boundaries:
            - Focus: find one exception returns false
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to find one exception returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check amazon asin exists
        """
        collection = Mock()
        collection.find_one.side_effect = RuntimeError("boom")
        assert database.check_amazon_asin_exists_in_db("B00X", collection) is False


class TestCheckBookExists:
    def test_found(self) -> None:
        """
        [Mock] check book exists: verifies found.

        Scenario:
            Given test inputs and fixtures for found
            When test_found executes the target flow
            Then the expected outcome for found is confirmed.

        Boundaries:
            - Focus: found
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to found
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check book exists
        """
        collection = Mock()
        collection.find_one.return_value = {"hash": "abc"}
        assert database.check_book_exists_in_db("abc", collection) is True
        collection.find_one.assert_called_once_with({"hash": "abc"})

    def test_not_found(self) -> None:
        """
        [Mock] check book exists: verifies not found.

        Scenario:
            Given test inputs and fixtures for not found
            When test_not_found executes the target flow
            Then the expected outcome for not found is confirmed.

        Boundaries:
            - Focus: not found
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to not found
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check book exists
        """
        collection = Mock()
        collection.find_one.return_value = None
        assert database.check_book_exists_in_db("abc", collection) is False

    def test_none_collection_returns_false(self, monkeypatch) -> None:
        """
        [Mock] check book exists: verifies none collection returns false.

        Scenario:
            Given test inputs and fixtures for none collection returns false
            When test_none_collection_returns_false executes the target flow
            Then the expected outcome for none collection returns false is confirmed.

        Boundaries:
            - Focus: none collection returns false
            - Fixtures/params: monkeypatch
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to none collection returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check book exists
        """
        monkeypatch.setattr(database, "get_mongo_collection", Mock(return_value=None))
        assert database.check_book_exists_in_db("abc", None) is False

    def test_find_one_exception_returns_false(self) -> None:
        """
        [Mock] check book exists: verifies find one exception returns false.

        Scenario:
            Given test inputs and fixtures for find one exception returns false
            When test_find_one_exception_returns_false executes the target flow
            Then the expected outcome for find one exception returns false is confirmed.

        Boundaries:
            - Focus: find one exception returns false
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to find one exception returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by check book exists
        """
        collection = Mock()
        collection.find_one.side_effect = RuntimeError("boom")
        assert database.check_book_exists_in_db("abc", collection) is False


# --------------------------------------------------------------------------- #
# update_book_isbn
# --------------------------------------------------------------------------- #
class TestUpdateBookIsbn:
    def test_both_isbns_set_and_modified(self) -> None:
        """
        [Mock] update book isbn: verifies both isbns set and modified.

        Scenario:
            Given test inputs and fixtures for both isbns set and modified
            When test_both_isbns_set_and_modified executes the target flow
            Then the expected outcome for both isbns set and modified is confirmed.

        Boundaries:
            - Focus: both isbns set and modified
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to both isbns set and modified
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by update book isbn
        """
        collection = Mock()
        collection.update_one.return_value = Mock(modified_count=1)
        result = database.update_book_isbn(collection, "abc", new_isbn10="111", new_isbn13="222")
        assert result is True
        collection.update_one.assert_called_once_with({"hash": "abc"}, {"$set": {"isbn10": "111", "isbn13": "222"}})

    def test_only_isbn10_when_isbn13_is_na(self) -> None:
        """
        [Mock] update book isbn: verifies only isbn10 when isbn13 is na.

        Scenario:
            Given test inputs and fixtures for only isbn10 when isbn13 is na
            When test_only_isbn10_when_isbn13_is_na executes the target flow
            Then the expected outcome for only isbn10 when isbn13 is na is confirmed.

        Boundaries:
            - Focus: only isbn10 when isbn13 is na
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to only isbn10 when isbn13 is na
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by update book isbn
        """
        collection = Mock()
        collection.update_one.return_value = Mock(modified_count=1)
        result = database.update_book_isbn(collection, "abc", new_isbn10="111", new_isbn13="N/A")
        assert result is True
        collection.update_one.assert_called_once_with({"hash": "abc"}, {"$set": {"isbn10": "111"}})

    def test_neither_valid_returns_false_without_update(self) -> None:
        """
        [Mock] update book isbn: verifies neither valid returns false without update.

        Scenario:
            Given test inputs and fixtures for neither valid returns false without update
            When test_neither_valid_returns_false_without_update executes the target flow
            Then the expected outcome for neither valid returns false without update is confirmed.

        Boundaries:
            - Focus: neither valid returns false without update
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to neither valid returns false without update
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by update book isbn
        """
        collection = Mock()
        result = database.update_book_isbn(collection, "abc", new_isbn10="N/A", new_isbn13=None)
        assert result is False
        collection.update_one.assert_not_called()

    def test_modified_count_zero_returns_false(self) -> None:
        """
        [Mock] update book isbn: verifies modified count zero returns false.

        Scenario:
            Given test inputs and fixtures for modified count zero returns false
            When test_modified_count_zero_returns_false executes the target flow
            Then the expected outcome for modified count zero returns false is confirmed.

        Boundaries:
            - Focus: modified count zero returns false
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to modified count zero returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by update book isbn
        """
        collection = Mock()
        collection.update_one.return_value = Mock(modified_count=0)
        result = database.update_book_isbn(collection, "abc", new_isbn10="111")
        assert result is False

    def test_none_collection_returns_false_without_calls(self) -> None:
        """
        [Unit] update book isbn: verifies none collection returns false without calls.

        Scenario:
            Given test inputs and fixtures for none collection returns false without calls
            When test_none_collection_returns_false_without_calls executes the target flow
            Then the expected outcome for none collection returns false without calls is confirmed.

        Boundaries:
            - Focus: none collection returns false without calls
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to none collection returns false without calls
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by update book isbn
        """
        result = database.update_book_isbn(None, "abc", new_isbn10="111", new_isbn13="222")
        assert result is False

    def test_update_one_exception_returns_false(self) -> None:
        """
        [Mock] update book isbn: verifies update one exception returns false.

        Scenario:
            Given test inputs and fixtures for update one exception returns false
            When test_update_one_exception_returns_false executes the target flow
            Then the expected outcome for update one exception returns false is confirmed.

        Boundaries:
            - Focus: update one exception returns false
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to update one exception returns false
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by update book isbn
        """
        collection = Mock()
        collection.update_one.side_effect = RuntimeError("boom")
        result = database.update_book_isbn(collection, "abc", new_isbn10="111")
        assert result is False


# --------------------------------------------------------------------------- #
# ensure_unique_index_on_hash
# --------------------------------------------------------------------------- #
class TestEnsureUniqueIndexOnHash:
    def test_creates_index_when_absent(self) -> None:
        """
        [Mock] ensure unique index on hash: verifies creates index when absent.

        Scenario:
            Given test inputs and fixtures for creates index when absent
            When test_creates_index_when_absent executes the target flow
            Then the expected outcome for creates index when absent is confirmed.

        Boundaries:
            - Focus: creates index when absent
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to creates index when absent
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by ensure unique index on hash
        """
        collection = Mock()
        collection.index_information.return_value = {"_id_": {}}
        database.ensure_unique_index_on_hash(collection)
        collection.create_index.assert_called_once_with("hash", unique=True, name="hash_unique_index")

    def test_skips_create_when_present(self) -> None:
        """
        [Mock] ensure unique index on hash: verifies skips create when present.

        Scenario:
            Given test inputs and fixtures for skips create when present
            When test_skips_create_when_present executes the target flow
            Then the expected outcome for skips create when present is confirmed.

        Boundaries:
            - Focus: skips create when present
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to skips create when present
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by ensure unique index on hash
        """
        collection = Mock()
        collection.index_information.return_value = {"hash_unique_index": {}}
        database.ensure_unique_index_on_hash(collection)
        collection.create_index.assert_not_called()

    def test_none_collection_is_noop(self) -> None:
        # Must not raise.
        """
        [Unit] ensure unique index on hash: verifies none collection is noop.

        Scenario:
            Given test inputs and fixtures for none collection is noop
            When test_none_collection_is_noop executes the target flow
            Then the expected outcome for none collection is noop is confirmed.

        Boundaries:
            - Focus: none collection is noop
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to none collection is noop
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by ensure unique index on hash
        """
        database.ensure_unique_index_on_hash(None)

    def test_exception_is_caught(self) -> None:
        """
        [Mock] ensure unique index on hash: verifies exception is caught.

        Scenario:
            Given test inputs and fixtures for exception is caught
            When test_exception_is_caught executes the target flow
            Then the expected outcome for exception is caught is confirmed.

        Boundaries:
            - Focus: exception is caught
            - Fixtures/params: none
            - Scope: assertions and setup in this test case only

        On failure, first check:
            - Assertion details tied to exception is caught
            - Fixture or parameter values used by this test
            - Recent changes in code paths exercised by ensure unique index on hash
        """
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
    """
    [Unit] database: verifies uri selects x509 auth.

    Scenario:
        Given test inputs and fixtures for uri selects x509 auth
        When test_uri_selects_x509_auth executes the target flow
        Then the expected outcome for uri selects x509 auth is confirmed.

    Boundaries:
        - Focus: uri selects x509 auth
        - Fixtures/params: uri, expected (parametrized, 5 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to uri selects x509 auth
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by database
    """
    assert database.uri_selects_x509_auth(uri) is expected


@pytest.mark.parametrize(
    "uri, expected",
    [(SCRAM_URI, True), (X509_URI, False), (BARE_URI, False)],
)
def test_uri_has_embedded_credentials(uri, expected) -> None:
    """
    [Unit] database: verifies uri has embedded credentials.

    Scenario:
        Given test inputs and fixtures for uri has embedded credentials
        When test_uri_has_embedded_credentials executes the target flow
        Then the expected outcome for uri has embedded credentials is confirmed.

    Boundaries:
        - Focus: uri has embedded credentials
        - Fixtures/params: uri, expected (parametrized, 3 cases)
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to uri has embedded credentials
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by database
    """
    assert database.uri_has_embedded_credentials(uri) is expected


def test_x509_mechanism_present_warns_nothing() -> None:
    """
    [Unit] database: verifies x509 mechanism present warns nothing.

    Scenario:
        Given test inputs and fixtures for x509 mechanism present warns nothing
        When test_x509_mechanism_present_warns_nothing executes the target flow
        Then the expected outcome for x509 mechanism present warns nothing is confirmed.

    Boundaries:
        - Focus: x509 mechanism present warns nothing
        - Fixtures/params: none
        - Scope: assertions and setup in this test case only

    On failure, first check:
        - Assertion details tied to x509 mechanism present warns nothing
        - Fixture or parameter values used by this test
        - Recent changes in code paths exercised by database
    """
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
