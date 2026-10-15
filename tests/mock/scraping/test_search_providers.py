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
