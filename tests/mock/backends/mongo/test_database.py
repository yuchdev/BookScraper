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
    BulkWriteError,
    ConnectionFailure,
    ServerSelectionTimeoutError,
)

from bookscraper.backends.mongo import config, database
from bookscraper.backends.storage import SaveSummary


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
        monkeypatch.setattr(database, "ensure_indexes", spy_index)

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
        # indexes ensured on the resolved collection.
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
        monkeypatch.setattr(database, "ensure_indexes", Mock())

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
        monkeypatch.setattr(database, "ensure_indexes", Mock())

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
    def test_save_returns_all_errors_and_skips_io_when_collection_is_none(self, monkeypatch) -> None:
        """
        Scenario: the resolved collection is None, so nothing can be written.
        Boundaries: two books, no live collection; the previous code fell through and
        raised AttributeError per book, reporting N errors as N swallowed exceptions.
        On failure: a total outage is misreported, and pymongo is touched despite there
        being no collection.
        """
        monkeypatch.setattr(database, "get_mongo_collection", Mock(return_value=None))
        books = [{"title": "x", "hash": "h1"}, {"title": "y", "hash": "h2"}]

        with patch.object(database, "print_log") as mock_print:
            summary = database.save_books_to_mongodb(books, None)

        assert summary == SaveSummary(inserted=0, duplicates=0, errors=2)
        printed = [c.args[0] for c in mock_print.call_args_list]
        assert any("not available" in msg for msg in printed)

    def test_save_empty_list_returns_zero_summary_without_db_call(self) -> None:
        """
        Scenario: an empty batch resolves to a zero summary and never touches the DB.
        Boundaries: [] with a live collection present.
        On failure: an empty insert_many([]) raises InvalidOperation, or a needless
        round-trip is issued.
        """
        collection = Mock(name="collection")

        summary = database.save_books_to_mongodb([], collection)

        assert summary == SaveSummary(inserted=0, duplicates=0, errors=0)
        collection.insert_many.assert_not_called()

    def test_save_uses_single_unordered_insert_many(self) -> None:
        """
        Scenario: a 100-book batch is written with exactly one unordered insert_many.
        Boundaries: 100 valid documents, all inserted.
        On failure: the per-book insert_one regression returns (500 round-trips), or the
        batch is ordered and aborts on the first duplicate.
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

    def test_bulk_write_error_splits_duplicates_from_other_errors(self) -> None:
        """
        Scenario: insert_many partially fails; duplicates (code 11000) and other write
        errors are counted apart, with nInserted as the success count.
        Boundaries: details carrying two 11000 codes and one 121 (document-validation).
        On failure: duplicates inflate the error count (or vice versa) and the partial-
        failure signal misfires.
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

    def test_invalid_document_counted_as_error_without_aborting_batch(self) -> None:
        """
        Scenario: one un-encodable document is rejected up front while the rest of the
        batch is still inserted.
        Boundaries: a batch of one BSON-encodable and one un-encodable document.
        On failure: the bad document aborts the whole insert_many, so a single malformed
        record loses every other book in the batch.
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

    def test_all_invalid_documents_skip_insert_many(self) -> None:
        """
        Scenario: every document fails pre-validation, so insert_many is never called.
        Boundaries: a batch whose only member is un-encodable.
        On failure: insert_many([]) is issued and raises InvalidOperation.
        """
        collection = Mock(name="collection")
        bad = {"title": "Bad", "hash": "h1", "broken": object()}

        with patch.object(database, "print_log"):
            summary = database.save_books_to_mongodb([bad], collection)

        collection.insert_many.assert_not_called()
        assert summary == SaveSummary(inserted=0, duplicates=0, errors=1)

    def test_caller_dicts_are_not_mutated_with_object_id(self) -> None:
        """
        Scenario: pymongo assigns _id in place on the docs it inserts; the caller's dicts
        must be untouched so a later json.dumps diagnostic write does not choke on an
        ObjectId.
        Boundaries: insert_many stamps _id onto the doc it receives.
        On failure: the caller's dict gains an ObjectId _id and downstream JSON writes fail.
        """
        collection = Mock(name="collection")

        def _stamp_id(docs, ordered):  # noqa: ARG001 - mirrors pymongo's in-place _id assignment
            for doc in docs:
                doc["_id"] = "stamped-object-id"
            return Mock(inserted_ids=["stamped-object-id"])

        collection.insert_many.side_effect = _stamp_id
        book = {"title": "Book", "hash": "h1"}

        with patch.object(database, "print_log"):
            database.save_books_to_mongodb([book], collection)

        assert "_id" not in book

    def test_unexpected_insert_many_error_counts_whole_batch_as_errors(self) -> None:
        """
        Scenario: insert_many fails with something other than a BulkWriteError (e.g. a
        connection drop mid-insert); the whole encodable batch is reported as errors.
        Boundaries: a generic exception raised by insert_many.
        On failure: the exception propagates out of save_books instead of being summarized.
        """
        collection = Mock(name="collection")
        collection.insert_many.side_effect = RuntimeError("connection lost")
        books = [{"title": f"Book {i}", "hash": f"h{i}"} for i in range(3)]

        with patch.object(database, "print_log"):
            summary = database.save_books_to_mongodb(books, collection)

        assert summary == SaveSummary(inserted=0, duplicates=0, errors=3)


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
# ensure_indexes (and its deprecated ensure_unique_index_on_hash alias)
# --------------------------------------------------------------------------- #
_ALL_SECONDARY_NAMES = ("asin_lookup_index", "book_id_lookup_index", "slug_lookup_index")


class TestEnsureUniqueIndexOnHash:
    def test_creates_index_when_absent(self) -> None:
        collection = Mock()
        # Secondary indexes already present so only the hash index is created, keeping
        # this alias test focused on the hash-index behavior it has always covered.
        collection.index_information.return_value = {"_id_": {}, **{name: {} for name in _ALL_SECONDARY_NAMES}}
        database.ensure_unique_index_on_hash(collection)
        collection.create_index.assert_called_once_with("hash", unique=True, name="hash_unique_index")

    def test_skips_create_when_present(self) -> None:
        collection = Mock()
        collection.index_information.return_value = {
            "hash_unique_index": {},
            **{name: {} for name in _ALL_SECONDARY_NAMES},
        }
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


class TestEnsureIndexes:
    def test_ensure_indexes_creates_all_four_when_absent(self) -> None:
        """
        Scenario: an empty collection gets the unique hash index plus all three sparse
        secondary lookup indexes created in one call.
        Boundaries: index_information() reports only the implicit _id_ index.
        On failure: a lookup field is left unindexed, so pre-scrape dedup falls back to a
        full collection scan.
        """
        collection = Mock()
        collection.index_information.return_value = {"_id_": {}}
        database.ensure_indexes(collection)
        collection.create_index.assert_any_call("hash", unique=True, name="hash_unique_index")
        collection.create_index.assert_any_call("asin", sparse=True, name="asin_lookup_index")
        collection.create_index.assert_any_call("book_id", sparse=True, name="book_id_lookup_index")
        collection.create_index.assert_any_call("slug", sparse=True, name="slug_lookup_index")
        assert collection.create_index.call_count == 4

    def test_ensure_indexes_is_idempotent_when_all_present(self) -> None:
        """
        Scenario: every index already exists, so a repeat call creates nothing.
        Boundaries: index_information() reports all four named indexes.
        On failure: a redundant create_index round-trip is issued, or a duplicate-index
        error is raised on a healthy collection.
        """
        collection = Mock()
        collection.index_information.return_value = {
            "hash_unique_index": {},
            **{name: {} for name in _ALL_SECONDARY_NAMES},
        }
        database.ensure_indexes(collection)
        collection.create_index.assert_not_called()

    def test_secondary_index_failure_warns_and_continues(self) -> None:
        """
        Scenario: creating the first secondary index fails; the remaining secondary
        indexes are still created and the failure is only a warning.
        Boundaries: only the hash index pre-exists; the asin index create raises.
        On failure: one bad index aborts the whole call, leaving later indexes missing.
        """
        collection = Mock()
        collection.index_information.return_value = {"hash_unique_index": {}}

        def create_index(field, **kwargs):
            if field == "asin":
                raise RuntimeError("boom")

        collection.create_index.side_effect = create_index

        with patch.object(database, "print_log") as mock_print:
            database.ensure_indexes(collection)

        attempted = [c.args[0] for c in collection.create_index.call_args_list]
        assert attempted == ["asin", "book_id", "slug"]
        printed = [c.args[0] for c in mock_print.call_args_list]
        assert any("Warning: Failed to ensure sparse index on 'asin'" in msg for msg in printed)

    def test_secondary_indexes_are_sparse_and_non_unique(self) -> None:
        """
        Scenario: the secondary indexes are created sparse and never unique, since two
        editions can legitimately share a slug across sites.
        Boundaries: empty collection so all secondary indexes are created.
        On failure: a unique secondary index would reject legitimate cross-site editions.
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

    def test_none_collection_is_noop(self) -> None:
        # Must not raise.
        database.ensure_indexes(None)

    def test_index_information_failure_aborts(self) -> None:
        """
        Scenario: reading index_information() fails, so no index is attempted and the
        error keeps the prior hash-index failure behavior.
        Boundaries: index_information() raises before any create_index call.
        On failure: the exception propagates, or secondary indexes are attempted against
        an unreadable collection.
        """
        collection = Mock()
        collection.index_information.side_effect = RuntimeError("boom")
        database.ensure_indexes(collection)
        collection.create_index.assert_not_called()


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
