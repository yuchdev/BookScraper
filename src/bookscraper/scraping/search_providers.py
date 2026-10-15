"""Search-provider registry for the multi-site ``search`` pipeline (task 03.3 subtask 01).

``bookscraper search`` used to carry a single ``if site_name.lower() == "leanpub":`` branch with no
``else``. This module replaces that per-site branching with a small extension point:

* :class:`SearchCandidate` - the uniform, frozen result shape every provider yields, so the
  ``search -> pre-dedup -> filter -> detail scrape`` pipeline downstream never has to know which site
  a candidate came from.
* :class:`SearchProvider` - the :class:`typing.Protocol` each site implements (a ``search`` coroutine
  plus a synchronous ``already_stored`` dedup check).
* :class:`SearchContext` - the per-run bundle (shared httpx client, rate limiters, robots cache, and a
  **lazily** launched Playwright browser) handed to every ``search`` call. The browser is launched
  only when a provider that needs it actually asks for it, so an API-only run (today's Leanpub-only
  default) never starts Chromium.
* :data:`PROVIDERS` - the registry mapping a site key to its provider instance. It is the single
  source of truth for "is this site searchable"; :func:`bookscraper.commands.search.resolve_search_targets`
  checks membership here and subtasks 02/03 extend it with ``PROVIDERS["amazon"] = ...`` etc.

Only the Leanpub port (:class:`LeanpubApiProvider`, wrapping
:func:`~bookscraper.scraping.search_utils.get_leanpub_search_results_via_api`) is implemented now; the
other sites are wired in by later subtasks in this task.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Protocol

import httpx
from playwright.async_api import Browser, async_playwright

from ..backends import StorageBackend
from .parameters import site_constants
from .politeness import DomainRateLimiters, RobotsCache
from .search_utils import get_leanpub_search_results_via_api, get_search_results_via_playwright

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchCandidate:
    """One search-result book, in a site-agnostic shape the rest of the pipeline consumes.

    A provider's :meth:`SearchProvider.search` returns these; pre-dedup, filtering, and the detail
    scrape all read this uniform record rather than each site's raw payload.

    :ivar site: The site key this candidate came from (e.g. ``"leanpub"``).
    :ivar title: The book title as shown in search results.
    :ivar url: The human-facing detail-page URL (what a person would open in a browser).
    :ivar detail_ref: What the detail scraper actually fetches - the detail-page URL for the
        Playwright sites, or the JSON:API book URL for Leanpub.
    :ivar authors: The author names parsed from the search result (possibly empty).
    :ivar source_id: The site's stable identifier - an Amazon ASIN or a Leanpub ``book_id`` - or
        ``None`` when the search result carried none.
    :ivar slug: The site's URL slug, or ``None`` when not applicable.
    :ivar rating: The average rating when the search result exposes one, else ``None``.
    :ivar rating_count: The number of ratings when exposed, else ``None``.
    :ivar query: The search query that surfaced this candidate.
    :ivar page: The 1-based search-result page the candidate appeared on.
    """

    site: str
    title: str
    url: str
    detail_ref: str
    authors: list[str]
    source_id: Optional[str] = None
    slug: Optional[str] = None
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    query: str = ""
    page: int = 1


class SearchContext:
    """Per-run collaborators shared by every provider's :meth:`SearchProvider.search` call.

    Bundles the run's shared :class:`httpx.AsyncClient`, its per-host :class:`DomainRateLimiters`,
    the optional :class:`RobotsCache`, and a **lazily** launched Playwright browser. The browser is
    started on the first :meth:`browser` call and never before, so a run whose selected providers are
    all API-based (``needs_browser=False``) never launches Chromium. Headless mode comes from the
    resolved :class:`~bookscraper.runtime.RuntimeOptions`, matching the launch pattern the detail and
    search scrapers use, rather than a hard-coded flag.

    Used as an async context manager so the browser and Playwright driver (if they were started) are
    closed on exit.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        limiters: DomainRateLimiters,
        robots: Optional[RobotsCache],
        headless: bool,
    ):
        """Store the shared collaborators; the browser stays unlaunched until first requested.

        :param http_client: The run's shared httpx client (reused by API providers and the detail
            scrape so one pooled connection set serves the whole run).
        :param limiters: The run's per-host rate-limiter registry.
        :param robots: The run's robots.txt cache, or ``None`` under ``--ignore-robots``.
        :param headless: Whether a lazily launched browser runs headless (from ``RuntimeOptions``).
        """
        self._http_client = http_client
        self._limiters = limiters
        self._robots = robots
        self._headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None

    @property
    def http_client(self) -> httpx.AsyncClient:
        """The run's shared httpx client."""
        return self._http_client

    @property
    def limiters(self) -> DomainRateLimiters:
        """The run's per-host rate-limiter registry."""
        return self._limiters

    @property
    def robots(self) -> Optional[RobotsCache]:
        """The run's robots.txt cache, or ``None`` when robots checking is disabled."""
        return self._robots

    async def browser(self) -> Browser:
        """Return the shared Playwright browser, launching it (once) on first use.

        The first caller starts the Playwright driver and launches a Chromium browser with the
        resolved headless setting; later callers reuse the same instance. A provider with
        ``needs_browser=False`` never calls this, so an API-only run keeps Chromium unlaunched.

        :returns: The run's shared :class:`~playwright.async_api.Browser`.
        """
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self._headless)
        return self._browser

    async def aclose(self):
        """Close the browser and stop the Playwright driver if they were ever launched."""
        if self._browser is not None:
            try:
                await self._browser.close()
            finally:
                self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            finally:
                self._playwright = None

    async def __aenter__(self) -> SearchContext:
        """Enter the context manager, returning this already-constructed context.

        :returns: This :class:`SearchContext`.
        """
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        """Close the lazily launched browser/driver on exit; never suppress an exception.

        :param exc_type: The exception type propagating out of the ``async with`` block, or ``None``.
        :param exc: The exception instance, or ``None``.
        :param traceback: The traceback, or ``None``.
        :returns: ``False`` so any in-flight exception keeps propagating.
        """
        await self.aclose()
        return False


class SearchProvider(Protocol):
    """The per-site contract the ``search`` pipeline drives, one implementation per searchable site.

    An implementation declares its ``site`` key and whether it ``needs_browser`` (so
    :class:`SearchContext` knows whether a run must launch Chromium), returns uniform
    :class:`SearchCandidate` objects from :meth:`search`, and answers the pre-scrape dedup check in
    :meth:`already_stored`.
    """

    #: The site key this provider serves (matches its key in :data:`PROVIDERS` and ``site_constants``).
    site: str
    #: Whether this provider needs a Playwright browser (API-only providers set this ``False``).
    needs_browser: bool

    async def search(self, query: str, max_pages: int, ctx: SearchContext) -> list[SearchCandidate]:
        """Return the search-result candidates for ``query``, fetching at most ``max_pages`` pages.

        :param query: The search query to run.
        :param max_pages: The maximum number of result pages to fetch (the resolved
            ``--max-search-pages``).
        :param ctx: The shared :class:`SearchContext` (httpx client, limiters, robots, lazy browser).
        :returns: The candidates found, as uniform :class:`SearchCandidate` records.
        """
        ...

    def already_stored(self, candidate: SearchCandidate, backend: StorageBackend) -> bool:
        """Report whether ``candidate`` is already in ``backend`` (the pre-scrape dedup check).

        :param candidate: The candidate to check.
        :param backend: The resolved storage backend to check against.
        :returns: ``True`` if the backend already holds this book, else ``False``.
        """
        ...


class LeanpubApiProvider:
    """Leanpub search provider: Leanpub's paginated JSON:API, no browser needed.

    Wraps :func:`~bookscraper.scraping.search_utils.get_leanpub_search_results_via_api` (unchanged)
    and adapts each raw result dict into a :class:`SearchCandidate`. ``already_stored`` delegates to
    the backend's ``leanpub_book_exists`` by ``book_id`` / ``slug``, exactly as the pre-registry
    ``search`` loop did - the behavior is identical, only the shape it lives in changed.
    """

    site = "leanpub"
    needs_browser = False

    async def search(self, query: str, max_pages: int, ctx: SearchContext) -> list[SearchCandidate]:
        """Fetch Leanpub candidates for ``query`` via the JSON:API and adapt them to the uniform shape.

        A result with no ``slug`` cannot be turned into a detail-fetch URL, so it is skipped with a
        warning (the same guard the old pre-scrape loop applied) rather than flowing downstream.

        :param query: The search query to run against Leanpub's bookstore API.
        :param max_pages: The maximum number of result pages to fetch.
        :param ctx: The shared :class:`SearchContext`; its limiters and robots cache are used (no
            browser - this provider is API-only).
        :returns: The Leanpub candidates with a resolvable slug, as :class:`SearchCandidate` records.
        """
        raw_results = await get_leanpub_search_results_via_api(
            query, limiters=ctx.limiters, robots=ctx.robots, max_pages=max_pages
        )
        base_url = site_constants[self.site]["BASE_URL"]
        detail_template = site_constants[self.site]["SINGLE_BOOK_API"]
        candidates: list[SearchCandidate] = []
        for book in raw_results:
            slug = book.get("slug")
            if not slug:
                logger.warning(f"Skipping Leanpub search result without a slug (id {book.get('book_id')}).")
                continue
            candidates.append(
                SearchCandidate(
                    site=self.site,
                    title=book.get("title") or "",
                    url=f"{base_url}/{slug}",
                    detail_ref=detail_template.replace("[slug]", slug),
                    authors=list(book.get("authors") or []),
                    source_id=book.get("book_id"),
                    slug=slug,
                    query=query,
                )
            )
        return candidates

    def already_stored(self, candidate: SearchCandidate, backend: StorageBackend) -> bool:
        """Report whether the Leanpub ``candidate`` already exists in ``backend``.

        :param candidate: The Leanpub candidate to check (its ``source_id`` is the Leanpub book id).
        :param backend: The resolved storage backend.
        :returns: ``True`` if the backend already holds this Leanpub book, else ``False``.
        """
        return backend.leanpub_book_exists(candidate.source_id, candidate.slug)


class AmazonPlaywrightProvider:
    """Amazon search provider: paginated search-results scraping via Playwright (task 03.3/02).

    Wraps :func:`~bookscraper.scraping.search_utils.get_search_results_via_playwright` (the Amazon
    ``match/case`` branch) and adapts each raw result dict into a :class:`SearchCandidate`. Unlike
    the API-only Leanpub provider this one needs a browser (``needs_browser`` is ``True``), so the
    shared :class:`SearchContext` launches Chromium lazily on the first :meth:`search` call.

    The underlying function paginates ``1..max_pages`` inclusive (clamped to
    ``site_constants["amazon"]["SEARCH_MAXIMUM_PAGES"]``), unwraps sponsored ``/sspa/click`` links,
    reads each card's ``data-asin`` as the stable identifier, and stops early on a bot wall / missing
    next button / no-results page. ``already_stored`` dedups by ASIN via
    :meth:`~bookscraper.backends.storage.StorageBackend.book_exists_by_asin`.
    """

    site = "amazon"
    needs_browser = True

    async def search(self, query: str, max_pages: int, ctx: SearchContext) -> list[SearchCandidate]:
        """Scrape Amazon search results for ``query`` and adapt each card to the uniform shape.

        Launches (or reuses) the shared browser via ``ctx.browser()`` and drives the Playwright
        search path with the run's limiters, robots cache, and ``max_pages``. Each raw result dict
        becomes a :class:`SearchCandidate` whose ``source_id`` is the card's ASIN; a card that
        carried no ASIN is still emitted (URL-deduped later) but logged at debug level. Authors,
        which the function joins into one comma-separated string, are split back into a list.

        :param query: The search query to run against Amazon's book search.
        :param max_pages: The maximum number of result pages to fetch (the resolved
            ``--max-search-pages``); clamped to the site limit inside the function.
        :param ctx: The shared :class:`SearchContext` (its lazily launched browser, limiters, and
            robots cache are used).
        :returns: The Amazon candidates as uniform :class:`SearchCandidate` records.
        """
        browser = await ctx.browser()
        raw_results = await get_search_results_via_playwright(
            browser,
            self.site,
            query,
            limiters=ctx.limiters,
            robots=ctx.robots,
            max_pages=max_pages,
        )
        candidates: list[SearchCandidate] = []
        for book in raw_results:
            asin = book.get("asin")
            url = book.get("url") or ""
            if not asin:
                logger.debug(f"Amazon candidate without an ASIN kept for URL-dedup: {book.get('title')!r}.")
            authors_text = book.get("authors_from_search") or ""
            authors = [name.strip() for name in authors_text.split(",") if name.strip()]
            candidates.append(
                SearchCandidate(
                    site=self.site,
                    title=book.get("title") or "",
                    url=url,
                    # The detail scrape fetches the same product URL the card pointed to; subtask 06
                    # wires the generic per-site detail dispatch that consumes it.
                    detail_ref=url,
                    authors=authors,
                    source_id=asin,
                    rating=book.get("rating"),
                    rating_count=book.get("rating_count"),
                    query=query,
                    page=book.get("search_page_number", 1),
                )
            )
        return candidates

    def already_stored(self, candidate: SearchCandidate, backend: StorageBackend) -> bool:
        """Report whether the Amazon ``candidate`` already exists in ``backend`` (by ASIN).

        A candidate with no ASIN cannot be deduped by ASIN here (it is URL-deduped later by the
        pipeline), so it is treated as not-yet-stored.

        :param candidate: The Amazon candidate to check (its ``source_id`` is the ASIN).
        :param backend: The resolved storage backend.
        :returns: ``True`` if the backend already holds this ASIN, else ``False``.
        """
        if not candidate.source_id:
            return False
        return backend.book_exists_by_asin(candidate.source_id)


#: The search-provider registry: site key -> provider instance. The single source of truth for which
#: sites ``search`` can target (``resolve_search_targets`` checks membership here). Leanpub (API) and
#: Amazon (Playwright) are wired; subtask 03 adds the remaining browser-backed providers.
PROVIDERS: dict[str, SearchProvider] = {
    LeanpubApiProvider.site: LeanpubApiProvider(),
    AmazonPlaywrightProvider.site: AmazonPlaywrightProvider(),
}
