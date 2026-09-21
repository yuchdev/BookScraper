"""Mock-tier tests for bookscraper.backends.mongo.deduplicate.leanpub_prescrape_deduplicate.

Verifies the $or query is built from exactly the identifiers provided, that a truthy
find_one result means "duplicate" and None means "new", and that the no-identifier,
no-collection, and find_one-error branches all short-circuit to False without
propagating. The collection is a plain Mock - no real MongoDB involved.
"""

from unittest.mock import Mock, patch

from bookscraper.backends.mongo import deduplicate


class TestLeanpubPrescrapeDeduplicate:
    def test_book_id_only_builds_id_only_query(self) -> None:
        collection = Mock()
        collection.find_one.return_value = None
        result = deduplicate.leanpub_prescrape_deduplicate("id-1", None, collection)
        assert result is False
        collection.find_one.assert_called_once_with({"$or": [{"book_id": "id-1"}]})

    def test_book_slug_only_builds_slug_only_query(self) -> None:
        collection = Mock()
        collection.find_one.return_value = None
        result = deduplicate.leanpub_prescrape_deduplicate(None, "slug-1", collection)
        assert result is False
        collection.find_one.assert_called_once_with({"$or": [{"slug": "slug-1"}]})

    def test_both_identifiers_build_combined_query(self) -> None:
        collection = Mock()
        collection.find_one.return_value = None
        deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", collection)
        collection.find_one.assert_called_once_with({"$or": [{"book_id": "id-1"}, {"slug": "slug-1"}]})

    def test_no_identifiers_returns_false_without_query(self) -> None:
        collection = Mock()
        result = deduplicate.leanpub_prescrape_deduplicate(None, None, collection)
        assert result is False
        collection.find_one.assert_not_called()

    def test_found_returns_true(self) -> None:
        collection = Mock()
        collection.find_one.return_value = {"book_id": "id-1", "slug": "slug-1"}
        result = deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", collection)
        assert result is True

    def test_not_found_returns_false(self) -> None:
        collection = Mock()
        collection.find_one.return_value = None
        result = deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", collection)
        assert result is False

    def test_none_collection_returns_false(self) -> None:
        with patch.object(deduplicate, "get_mongo_collection", Mock(return_value=None)):
            result = deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", None)
        assert result is False

    def test_find_one_exception_returns_false(self) -> None:
        collection = Mock()
        collection.find_one.side_effect = RuntimeError("boom")
        result = deduplicate.leanpub_prescrape_deduplicate("id-1", "slug-1", collection)
        assert result is False
