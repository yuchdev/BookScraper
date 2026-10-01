"""Offline fixture-serving harness for the real-HTML mock corpus.

This conftest turns the committed ``tests/fixtures/`` corpus into a fully offline test
environment for the *unmodified* scraper (task 06.0, subtask 03). It serves captured HTML
bytes to real headless Chromium (Playwright) and captured JSON bytes to ``httpx`` via an
``httpx.MockTransport``, and it is **default-deny**: every Playwright route and every httpx
request that is not mapped to a corpus fixture is aborted, recorded, and fails the test that
made it (threat model FIX-8, a CRITICAL merge gate). Corpus routes are installed *after* the
production ``route_handler`` so they take precedence and an unmapped document request can
never fall through to ``route.continue_()`` and the network (FIX-9).

The three public fixtures are ``chromium_browser`` (one real headless Chromium per session),
``corpus_router`` (wraps a browser so every page it creates serves the corpus and blocks the
rest) and ``httpx_corpus`` (patches ``httpx.AsyncClient`` onto a corpus ``MockTransport``).
No production code is modified: interception happens purely at the Playwright ``page.route``
and httpx transport boundaries.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Route,
    WebSocketRoute,
    async_playwright,
)

# Env var naming a locally-installed Chromium executable. Playwright 1.63's bundled Chromium
# refuses to launch on macOS 13 (see tests/fixtures/README.md), so the harness lets a machine
# point at a system Chromium; when unset it falls back to Playwright's bundled browser.
CHROMIUM_PATH_ENV = "BOOKSCRAPER_CHROMIUM_PATH"

# tests/mock/scraping/html/conftest.py -> parents[3] == tests/
FIXTURES_ROOT = Path(__file__).resolve().parents[3] / "fixtures"

_HTML_CONTENT_TYPE = "text/html; charset=utf-8"
_JSON_CONTENT_TYPE = "application/json"
_ROBOTS_CONTENT_TYPE = "text/plain; charset=utf-8"

# host substring -> site name, for robots.txt resolution.
_HOST_TO_SITE = {
    "amazon.": "amazon",
    "packtpub.": "packtpub",
    "leanpub.": "leanpub",
    "oreilly.": "oreilly",
}


class OfflineNetworkError(RuntimeError):
    """Raised by the httpx transport when a request is not mapped to a corpus fixture.

    Names the offending URL so a failing test points straight at the missing mapping.
    """


# ---------------------------------------------------------------------------
# Corpus mapping helpers
# ---------------------------------------------------------------------------


def _fixture_for_meta(meta: Path) -> Optional[Path]:
    """Return the fixture body (``.html`` or ``.json``) beside a ``<base>.meta.json``."""
    base = meta.name[: -len(".meta.json")]
    for ext in (".html", ".json"):
        candidate = meta.parent / f"{base}{ext}"
        if candidate.exists():
            return candidate
    return None


def corpus_mapping(kind: str) -> dict[str, Path]:
    """Build a ``{original_url: fixture_path}`` mapping from the committed corpus.

    ``kind`` is a top-level corpus subdir - ``"html"`` (Playwright sites) or ``"json"``
    (Leanpub API). The URL comes from each fixture's sibling ``.meta.json`` (the real page
    URL the fixture was captured/built from), which is exactly what the harness maps against.
    """
    root = FIXTURES_ROOT / kind
    mapping: dict[str, Path] = {}
    for meta in sorted(root.rglob("*.meta.json")):
        fixture = _fixture_for_meta(meta)
        if fixture is None:
            continue
        url = json.loads(meta.read_text(encoding="utf-8"))["url"]
        mapping[url] = fixture
    return mapping


class CorpusMapping:
    """Normalized lookup from a request URL to a corpus fixture path.

    URLs are normalized to scheme+host+path; the query string is ignored *unless* the mapping
    key itself carries one (so the Leanpub ``?include=accepted_authors`` endpoint matches
    exactly while ordinary detail pages match regardless of tracking query params).
    """

    def __init__(self, mapping: dict[str, Path]) -> None:
        self._with_query: dict[str, Path] = {}
        self._no_query: dict[str, Path] = {}
        for url, path in mapping.items():
            parts = urlsplit(url)
            if parts.query:
                self._with_query[self._key(url, keep_query=True)] = path
            else:
                self._no_query[self._key(url, keep_query=False)] = path

    @staticmethod
    def _key(url: str, *, keep_query: bool) -> str:
        parts = urlsplit(url)
        query = parts.query if keep_query else ""
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))

    def lookup(self, url: str) -> Optional[Path]:
        with_key = self._key(url, keep_query=True)
        if with_key in self._with_query:
            return self._with_query[with_key]
        return self._no_query.get(self._key(url, keep_query=False))


def _robots_path_for(url: str) -> Optional[Path]:
    """Return ``tests/fixtures/robots/<site>.txt`` for a ``/robots.txt`` URL, if present."""
    parts = urlsplit(url)
    if parts.path != "/robots.txt":
        return None
    for host_fragment, site in _HOST_TO_SITE.items():
        if host_fragment in parts.netloc:
            candidate = FIXTURES_ROOT / "robots" / f"{site}.txt"
            return candidate if candidate.exists() else None
    return None


# ---------------------------------------------------------------------------
# Recorders (one per boundary)
# ---------------------------------------------------------------------------


class PlaywrightRecorder:
    """Default-deny Playwright route: fulfills mapped URLs, aborts and records the rest."""

    def __init__(self, mapping: CorpusMapping) -> None:
        self._mapping = mapping
        self.served: list[str] = []
        self.unexpected: list[str] = []

    async def handle(self, route: Route) -> None:
        url = route.request.url
        fixture = self._mapping.lookup(url)
        if fixture is not None:
            self.served.append(url)
            await route.fulfill(
                status=200,
                body=fixture.read_bytes(),
                headers={"content-type": _HTML_CONTENT_TYPE},
            )
            return

        robots = _robots_path_for(url)
        if robots is not None:
            self.served.append(url)
            await route.fulfill(
                status=200,
                body=robots.read_bytes(),
                headers={"content-type": _ROBOTS_CONTENT_TYPE},
            )
            return

        # Default-deny (FIX-8/FIX-9): never continue_() to the network for an unmapped URL.
        self.unexpected.append(url)
        await route.abort()

    async def handle_web_socket(self, ws: WebSocketRoute) -> None:
        """Close and record every WebSocket.

        Sanitized fixtures carry no scripts, so a live socket is always an offline-boundary
        violation (default-deny): recording it fails the test, closing it keeps it offline.
        """
        self.unexpected.append(ws.url)
        await ws.close()


class HttpxRecorder:
    """Default-deny httpx transport handler: serves mapped JSON, rejects the rest."""

    def __init__(self, mapping: CorpusMapping) -> None:
        self._mapping = mapping
        self.served: list[str] = []
        self.unexpected: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        fixture = self._mapping.lookup(url)
        if fixture is not None:
            self.served.append(url)
            return httpx.Response(
                200,
                content=fixture.read_bytes(),
                headers={"content-type": _JSON_CONTENT_TYPE},
            )
        # MockTransport never touches the network, so recording + raising is enough to keep
        # the harness offline; the raised error names the URL for a clear failure message.
        self.unexpected.append(url)
        raise OfflineNetworkError(f"Offline harness blocked unmapped httpx request: {url}")


def assert_no_unexpected(recorders: list[Any], *, label: str) -> None:
    """Fail the test (no traceback) naming every unmapped request a recorder blocked."""
    urls = [url for recorder in recorders for url in recorder.unexpected]
    if urls:
        pytest.fail(
            f"Offline harness ({label}): {len(urls)} unmapped request(s) were blocked and "
            "must fail this test:\n  " + "\n  ".join(urls),
            pytrace=False,
        )


# ---------------------------------------------------------------------------
# Playwright route installation (precedence-preserving, FIX-9)
# ---------------------------------------------------------------------------


async def _route_web_sockets(target: Any, recorder: PlaywrightRecorder) -> None:
    """Default-deny WebSockets on a page or context (close + record every socket).

    Playwright 1.63 supports ``route_web_socket``; without a matching handler a socket would
    connect straight to the server (a network escape), so we route ``**/*`` and close them.
    """
    await target.route_web_socket("**/*", recorder.handle_web_socket)


async def attach_corpus_route(page: Page, recorder: PlaywrightRecorder) -> None:
    """Install the corpus route on ``page`` and keep it at the highest precedence.

    A baseline ``route("**/*")`` is installed immediately (so a direct ``page.goto`` is
    served offline), and ``page.route`` is wrapped so that whenever production later installs
    its own ``route_handler`` the corpus route is re-added *after* it. Playwright evaluates
    the most-recently-added matching route first, so the corpus route always wins and an
    unmapped document is aborted, never continued to the network (FIX-9). Page-level routes
    also take precedence over context-level ones, so this is what keeps the corpus ahead of a
    production page route even when the page came from a wrapped context.
    """
    original_route = page.route

    await original_route("**/*", recorder.handle)
    await _route_web_sockets(page, recorder)

    async def wrapped_route(pattern: Any, handler: Any, **kwargs: Any) -> None:
        await original_route(pattern, handler, **kwargs)
        await original_route("**/*", recorder.handle)

    page.route = wrapped_route  # type: ignore[method-assign]


async def attach_corpus_context_route(context: BrowserContext, recorder: PlaywrightRecorder) -> None:
    """Install the corpus route at CONTEXT level and wrap the context so it stays default-deny.

    A baseline ``context.route("**/*")`` (plus WebSocket routing) covers any page the context
    creates; ``context.route`` is wrapped so a production ``context.route(...)`` re-appends the
    corpus route last (FIX-9); and ``context.new_page`` is wrapped so every page *also* gets
    the page-level corpus route (which outranks any production page route). Service workers are
    forced off at creation time so a worker cannot bypass routing.
    """
    original_context_route = context.route

    await original_context_route("**/*", recorder.handle)
    await _route_web_sockets(context, recorder)

    async def wrapped_context_route(pattern: Any, handler: Any, **kwargs: Any) -> None:
        await original_context_route(pattern, handler, **kwargs)
        await original_context_route("**/*", recorder.handle)

    context.route = wrapped_context_route  # type: ignore[method-assign]

    original_new_page = context.new_page

    async def wrapped_new_page(*args: Any, **kwargs: Any) -> Page:
        page = await original_new_page(*args, **kwargs)
        await attach_corpus_route(page, recorder)
        return page

    context.new_page = wrapped_new_page  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Chromium launch + skip logic
# ---------------------------------------------------------------------------


async def launch_chromium(playwright: Any) -> Browser:
    """Launch headless Chromium, or ``pytest.skip`` the tier when none is usable.

    Honors ``BOOKSCRAPER_CHROMIUM_PATH`` (a system Chromium, for macOS 13 where Playwright's
    bundled Chromium refuses to launch); when that env var is unset it falls back to the
    bundled browser. If no browser can be launched the whole Chromium tier *skips* (never
    fails) with the install hint, so CI can provision one (task 06.2).
    """
    launch_kwargs: dict[str, Any] = {"headless": True}
    exe = os.environ.get(CHROMIUM_PATH_ENV)
    if exe:
        if not Path(exe).exists():
            pytest.skip(
                f"{CHROMIUM_PATH_ENV}={exe!r} does not point at an existing Chromium "
                "executable; unset it to use the bundled browser or install one with "
                "`uv run playwright install chromium`."
            )
        launch_kwargs["executable_path"] = exe

    try:
        return await playwright.chromium.launch(**launch_kwargs)
    except Exception as exc:  # noqa: BLE001 - any launch failure means "no usable browser"
        pytest.skip(
            f"headless Chromium could not be launched ({type(exc).__name__}: {exc}); "
            "install it with `uv run playwright install chromium`."
        )


# ---------------------------------------------------------------------------
# Public fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def chromium_browser() -> Any:
    """Session-scoped real headless Chromium (one launch for the whole package).

    Skips the whole tier if no Chromium is usable (see ``launch_chromium``).
    """
    async with async_playwright() as playwright:
        browser = await launch_chromium(playwright)
        try:
            yield browser
        finally:
            await browser.close()


@pytest_asyncio.fixture(loop_scope="session")
async def corpus_router() -> Any:
    """Return ``install(browser, mapping)`` wrapping a browser for offline corpus serving.

    Every page the browser creates installs the default-deny corpus route; at teardown any
    request that was not mapped fails the test with the offending URL list (FIX-8).
    """
    recorders: list[PlaywrightRecorder] = []
    restorations: list[Callable[[], None]] = []

    def install(browser: Browser, mapping: dict[str, Path]) -> Browser:
        # One install per test is assumed: each call wraps new_page/new_context afresh and
        # registers its own recorder + restore. The session-scoped browser is shared, but a
        # test that calls install() more than once would stack wrappers and leave several
        # recorders to assert at teardown - not a usage this harness supports.
        recorder = PlaywrightRecorder(CorpusMapping(mapping))
        recorders.append(recorder)

        original_new_page = browser.new_page
        original_new_context = browser.new_context

        async def wrapped_new_page(*args: Any, **kwargs: Any) -> Page:
            page = await original_new_page(*args, **kwargs)
            await attach_corpus_route(page, recorder)
            return page

        async def wrapped_new_context(*args: Any, **kwargs: Any) -> BrowserContext:
            # Force service workers off so a worker cannot bypass the corpus routing.
            kwargs["service_workers"] = "block"
            context = await original_new_context(*args, **kwargs)
            await attach_corpus_context_route(context, recorder)
            return context

        browser.new_page = wrapped_new_page  # type: ignore[method-assign]
        browser.new_context = wrapped_new_context  # type: ignore[method-assign]

        def _restore() -> None:
            browser.new_page = original_new_page  # type: ignore[method-assign]
            browser.new_context = original_new_context  # type: ignore[method-assign]

        restorations.append(_restore)
        return browser

    yield install

    for restore in restorations:
        restore()
    assert_no_unexpected(recorders, label="Playwright")


@pytest.fixture
def httpx_corpus(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Return ``install(mapping)`` patching ``httpx.AsyncClient`` onto a corpus transport.

    Production builds its client with ``httpx.AsyncClient()`` (no transport arg), so injecting
    an ``httpx.MockTransport`` via a same-signature factory needs no production change. Any
    unmapped request is recorded and fails the test at teardown (FIX-8).
    """
    recorders: list[HttpxRecorder] = []
    real_async_client = httpx.AsyncClient

    def install(mapping: dict[str, Path]) -> HttpxRecorder:
        recorder = HttpxRecorder(CorpusMapping(mapping))
        recorders.append(recorder)
        transport = httpx.MockTransport(recorder.handle)

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs.setdefault("transport", transport)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return recorder

    yield install

    assert_no_unexpected(recorders, label="httpx")
