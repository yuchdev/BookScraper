"""Unit/mock tests for bookscraper.scraping.search_providers.

The Leanpub search API (``get_leanpub_search_results_via_api``) and Playwright's
``async_playwright`` are mocked, so no network and no browser are touched. These tests pin the
registry invariant, the Leanpub provider's legacy-compatible adaptation, the lazy-browser
guarantee, and that the resolved ``--max-search-pages`` reaches the provider.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from bookscraper.backends.storage import StorageBackend
from bookscraper.scraping.parameters import site_constants
from bookscraper.scraping.search_providers import (
    PROVIDERS,
    AmazonPlaywrightProvider,
    LeanpubApiProvider,
    SearchCandidate,
    SearchContext,
)

MODULE = "bookscraper.scraping.search_providers"


def _raw_leanpub_book(slug, title="A Book", book_id=None) -> dict:
    """Build one raw Leanpub search-API result dict (the shape the API helper returns).

    :param slug: The book slug (``None`` to simulate a result with no slug).
    :param title: The book title.
    :param book_id: The Leanpub book id; defaults to ``id-<slug>``.
    :returns: A raw result dict with site/title/book_id/slug/authors keys.
    """
    return {
        "site": "leanpub",
        "title": title,
        "book_id": book_id if book_id is not None else f"id-{slug}",
        "slug": slug,
        "authors": ["Ada Lovelace"],
    }


def _context() -> SearchContext:
    """Build a SearchContext with mocked collaborators and no browser launched.

    :returns: A :class:`SearchContext` whose httpx client and limiters are mocks and whose robots
        cache is ``None`` (the API path a test exercises never touches them for real).
    """
    return SearchContext(http_client=MagicMock(), limiters=MagicMock(), robots=None, headless=True)


def test_registry_contains_every_site_in_site_constants():
    """
    [Local] search_providers: every registered provider keys a real site and is self-consistent.

    Scenario:
        Given the PROVIDERS registry
        When its keys are compared against site_constants and each provider's own ``site`` attribute
        Then every registry key is a site_constants key, each provider's ``site`` equals its key, and
        the Leanpub provider is registered (the only one wired in this subtask).

    Boundaries:
        - Only Leanpub is wired now; Amazon/Packtpub/O'Reilly are added by subtasks 02/03, so this
          asserts the registry is a subset of site_constants (no provider for a non-existent site),
          not equality.

    On failure, first check:
        - PROVIDERS keys and each provider class's ``site`` attribute in search_providers.py.
    """
    assert set(PROVIDERS) <= set(site_constants)
    assert "leanpub" in PROVIDERS
    for key, provider in PROVIDERS.items():
        assert provider.site == key


async def test_leanpub_provider_matches_legacy_behavior():
    """
    [Local] search_providers: LeanpubApiProvider adapts raw API results like the old inline loop.

    Scenario:
        Given the Leanpub search API returning two books with slugs and one without
        When LeanpubApiProvider.search runs
        Then only the two slugged results become SearchCandidates carrying the legacy-equivalent
        fields (title, slug, source_id=book_id, detail_ref=SINGLE_BOOK_API, human url), the no-slug
        result is dropped, and already_stored delegates to leanpub_book_exists(book_id, slug).

    Boundaries:
        - The API helper is mocked; this pins the dict->SearchCandidate adaptation and the
          already_stored delegation, not the API pagination itself.

    On failure, first check:
        - LeanpubApiProvider.search's slug guard / field mapping and already_stored in search_providers.py.
    """
    raw = [
        _raw_leanpub_book("first-book", "First Book"),
        _raw_leanpub_book(None, "No Slug Book"),
        _raw_leanpub_book("second-book", "Second Book"),
    ]
    provider = LeanpubApiProvider()
    api = AsyncMock(return_value=raw)

    with patch(f"{MODULE}.get_leanpub_search_results_via_api", api):
        candidates = await provider.search("Python", 3, _context())

    assert [c.slug for c in candidates] == ["first-book", "second-book"]
    first = candidates[0]
    assert isinstance(first, SearchCandidate)
    assert first.site == "leanpub"
    assert first.title == "First Book"
    assert first.source_id == "id-first-book"
    assert first.detail_ref == site_constants["leanpub"]["SINGLE_BOOK_API"].replace("[slug]", "first-book")
    assert first.detail_ref.endswith("/first-book.json")
    assert first.url == "https://leanpub.com/first-book"
    assert first.authors == ["Ada Lovelace"]

    backend = MagicMock(spec=StorageBackend)
    backend.leanpub_book_exists.return_value = True
    assert provider.already_stored(first, backend) is True
    backend.leanpub_book_exists.assert_called_once_with("id-first-book", "first-book")


async def test_browser_not_launched_when_only_api_providers_selected():
    """
    [Local] search_providers: an API-only provider never triggers a Playwright launch.

    Scenario:
        Given a SearchContext and the API-only LeanpubApiProvider (needs_browser is False)
        When the provider runs a search and the context is later closed
        Then async_playwright is never called, no browser is launched, and the context's browser
        handle stays None through aclose().

    Boundaries:
        - Playwright is patched so a launch would be observable; the Leanpub search path simply never
          calls ctx.browser(), so nothing starts.

    On failure, first check:
        - SearchContext.browser's lazy launch and LeanpubApiProvider.needs_browser in search_providers.py.
    """
    assert LeanpubApiProvider.needs_browser is False
    provider = LeanpubApiProvider()
    api = AsyncMock(return_value=[_raw_leanpub_book("only-book", "Only Book")])
    playwright_factory = MagicMock()

    ctx = _context()
    with (
        patch(f"{MODULE}.get_leanpub_search_results_via_api", api),
        patch(f"{MODULE}.async_playwright", playwright_factory),
    ):
        await provider.search("Python", 2, ctx)
        await ctx.aclose()

    playwright_factory.assert_not_called()
    assert ctx._browser is None


async def test_max_search_pages_flag_reaches_provider():
    """
    [Local] search_providers: LeanpubApiProvider.search forwards max_pages to the API helper.

    Scenario:
        Given LeanpubApiProvider.search called with max_pages=7
        When the mocked search API records its call
        Then it is awaited once with the query and max_pages=7 (the resolved --max-search-pages).

    Boundaries:
        - The API helper is mocked; this pins the flag's pass-through, not pagination behavior.

    On failure, first check:
        - The max_pages argument forwarded to get_leanpub_search_results_via_api in LeanpubApiProvider.search.
    """
    provider = LeanpubApiProvider()
    api = AsyncMock(return_value=[])

    with patch(f"{MODULE}.get_leanpub_search_results_via_api", api):
        await provider.search("Rust", 7, _context())

    api.assert_awaited_once()
    assert api.call_args.args[0] == "Rust"
    assert api.call_args.kwargs["max_pages"] == 7


# ---------------------------------------------------------------------------
# AmazonPlaywrightProvider (task 03.3/02)
# ---------------------------------------------------------------------------


def _amazon_context() -> SearchContext:
    """Build a SearchContext whose lazy browser is a stub, so no real Chromium is launched.

    :returns: A :class:`SearchContext` with mocked collaborators and ``browser()`` stubbed to an
        awaitable returning a fake browser handle.
    """
    ctx = SearchContext(http_client=MagicMock(), limiters=MagicMock(), robots=None, headless=True)
    ctx.browser = AsyncMock(return_value=MagicMock())
    return ctx


def _raw_amazon_book(**overrides) -> dict:
    """Build one raw amazon search-result dict (the shape get_search_results_via_playwright returns).

    :param overrides: Fields to override on the default record.
    :returns: A raw amazon result dict with site/query/url/asin/title/authors/rating keys.
    """
    book = {
        "site": "amazon",
        "query": "python",
        "search_page_number": 1,
        "search_result_index": 1,
        "url": "https://www.amazon.com/dp/B0ABCDEFGH",
        "asin": "B0ABCDEFGH",
        "title": "A Python Book",
        "authors_from_search": "Jane Doe, John Smith",
        "publication_date": "Jan 1, 2024",
        "rating": 4.6,
        "rating_count": 1234,
    }
    book.update(overrides)
    return book


def test_amazon_provider_registered_and_needs_browser():
    """
    [Local] search_providers: AmazonPlaywrightProvider is registered and declares needs_browser.

    Scenario:
        Given the PROVIDERS registry
        When the amazon entry is inspected
        Then it is an AmazonPlaywrightProvider whose ``site`` is "amazon" and ``needs_browser`` True.

    Boundaries:
        - Registry/attribute invariant only; the search behaviour is covered by other tests.

    On failure, first check:
        - PROVIDERS["amazon"] wiring and the class attributes in search_providers.py.
    """
    provider = PROVIDERS["amazon"]
    assert isinstance(provider, AmazonPlaywrightProvider)
    assert provider.site == "amazon"
    assert provider.needs_browser is True


async def test_amazon_candidate_carries_asin_and_rating():
    """
    [Local] search_providers: AmazonPlaywrightProvider maps a raw card dict onto a SearchCandidate.

    Scenario:
        Given get_search_results_via_playwright returning one amazon card dict (asin, rating,
        rating_count, comma-joined authors, url, page number)
        When AmazonPlaywrightProvider.search runs
        Then the candidate carries source_id=asin, rating/rating_count, the authors split into a
        list, url==detail_ref==the card url, and page from search_page_number.

    Boundaries:
        - The Playwright function is mocked; this pins the dict->SearchCandidate adaptation only.

    On failure, first check:
        - AmazonPlaywrightProvider.search's field mapping in search_providers.py.
    """
    provider = AmazonPlaywrightProvider()
    search = AsyncMock(return_value=[_raw_amazon_book(search_page_number=2)])

    with patch(f"{MODULE}.get_search_results_via_playwright", search):
        candidates = await provider.search("python", 2, _amazon_context())

    assert len(candidates) == 1
    candidate = candidates[0]
    assert isinstance(candidate, SearchCandidate)
    assert candidate.site == "amazon"
    assert candidate.source_id == "B0ABCDEFGH"
    assert candidate.rating == 4.6
    assert candidate.rating_count == 1234
    assert candidate.authors == ["Jane Doe", "John Smith"]
    assert candidate.url == "https://www.amazon.com/dp/B0ABCDEFGH"
    assert candidate.detail_ref == "https://www.amazon.com/dp/B0ABCDEFGH"
    assert candidate.page == 2


async def test_amazon_candidate_without_asin_is_kept():
    """
    [Local] search_providers: a card with no ASIN is still emitted as a candidate (URL-deduped later).

    Scenario:
        Given a raw amazon card dict whose asin is None (e.g. a link-selector-drifted card)
        When AmazonPlaywrightProvider.search runs
        Then exactly one candidate is still returned with source_id None (kept, not dropped).

    Boundaries:
        - Pins the "keep without ASIN" rule; the debug log line itself is not asserted.

    On failure, first check:
        - The ``if not asin`` branch in AmazonPlaywrightProvider.search (it must keep, not skip).
    """
    provider = AmazonPlaywrightProvider()
    search = AsyncMock(return_value=[_raw_amazon_book(asin=None, url=None)])

    with patch(f"{MODULE}.get_search_results_via_playwright", search):
        candidates = await provider.search("python", 1, _amazon_context())

    assert len(candidates) == 1
    assert candidates[0].source_id is None
    assert candidates[0].url == ""


async def test_amazon_already_stored_uses_asin_lookup():
    """
    [Local] search_providers: already_stored dedups an amazon candidate by its ASIN.

    Scenario:
        Given an amazon candidate with an ASIN and a backend whose book_exists_by_asin returns True
        When already_stored runs
        Then it returns True and called book_exists_by_asin with that ASIN; a candidate with no ASIN
        returns False WITHOUT calling the backend (it is URL-deduped later).

    Boundaries:
        - The backend is a spec'd mock; this pins the ASIN delegation and the no-ASIN short-circuit.

    On failure, first check:
        - AmazonPlaywrightProvider.already_stored in search_providers.py.
    """
    provider = AmazonPlaywrightProvider()
    with_asin = SearchCandidate(site="amazon", title="A", url="u", detail_ref="u", authors=[], source_id="B0ABCDEFGH")
    without_asin = SearchCandidate(site="amazon", title="A", url="u", detail_ref="u", authors=[], source_id=None)

    backend = MagicMock(spec=StorageBackend)
    backend.book_exists_by_asin.return_value = True
    assert provider.already_stored(with_asin, backend) is True
    backend.book_exists_by_asin.assert_called_once_with("B0ABCDEFGH")

    backend.book_exists_by_asin.reset_mock()
    assert provider.already_stored(without_asin, backend) is False
    backend.book_exists_by_asin.assert_not_called()


async def test_amazon_provider_launches_browser_and_forwards_max_pages():
    """
    [Local] search_providers: AmazonPlaywrightProvider gets the shared browser and forwards max_pages.

    Scenario:
        Given AmazonPlaywrightProvider.search called with max_pages=5
        When the mocked Playwright search records its call
        Then ctx.browser() was awaited (the browser-backed provider needs one) and the function was
        awaited once with the shared browser, site "amazon", the query, and max_pages=5.

    Boundaries:
        - Both the browser launch and the Playwright function are mocked; pins the wiring only.

    On failure, first check:
        - The ctx.browser()/get_search_results_via_playwright call in AmazonPlaywrightProvider.search.
    """
    provider = AmazonPlaywrightProvider()
    search = AsyncMock(return_value=[])
    ctx = _amazon_context()

    with patch(f"{MODULE}.get_search_results_via_playwright", search):
        await provider.search("python", 5, ctx)

    ctx.browser.assert_awaited_once()
    search.assert_awaited_once()
    assert search.call_args.args[1] == "amazon"
    assert search.call_args.args[2] == "python"
    assert search.call_args.kwargs["max_pages"] == 5
