"""Small, focused test doubles for the scraping tier.

The scraping code drives two external collaborators that are painful to mock with
raw ``unittest.mock`` chains:

* Playwright's deeply-chained page/locator API
  (``page.locator(sel).first.text_content()``, ``locator.all()``, ``locator.count()``,
  ``card.locator(sub_sel).get_attribute(...)`` ...).
* ``httpx.AsyncClient`` used as an async context manager.

This module hand-rolls just enough of both to keep ``test_scrape_details.py``,
``test_search_utils.py`` and ``test_schema_detection.py`` readable. It is deliberately
not a general Playwright/httpx framework - only the surface the source files touch is
implemented.
"""

from __future__ import annotations

from typing import Any, Optional, Union
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Playwright doubles
# ---------------------------------------------------------------------------

# A "match" is a plain dict describing one matched DOM element:
#   {"text": "...", "attrs": {"href": "..."}, "sub": {selector: FakeLocator}}
# Every key is optional. ``sub`` lets a matched element expose its own nested
# ``card.locator(child_selector)`` lookups (needed by the search-card scraping).
Match = dict[str, Any]
LocatorInit = Union[None, str, Match, list]


def _normalize(matches: LocatorInit) -> list[Match]:
    """Turn the flexible constructor input into a uniform list of match dicts."""
    if matches is None:
        return []
    if isinstance(matches, str):
        return [{"text": matches, "attrs": {}}]
    if isinstance(matches, dict):
        return [matches]
    normalized: list[Match] = []
    for item in matches:
        if isinstance(item, str):
            normalized.append({"text": item, "attrs": {}})
        else:
            normalized.append(item)
    return normalized


class FakeLocator:
    """A stand-in for a Playwright ``Locator``.

    Construct it with the elements it should "match":

    * ``FakeLocator("Some text")`` - one element whose ``text_content()`` is that string.
    * ``FakeLocator(None)`` / ``FakeLocator([])`` - an empty locator (``count() == 0``).
    * ``FakeLocator([{"text": "a"}, {"text": "b"}])`` - two elements.
    * ``FakeLocator([{"attrs": {"href": "/x"}, "sub": {".title": FakeLocator("T")}}])`` -
      one element carrying attributes and its own nested locators.

    Pass ``raises=<exception>`` to make ``count()`` raise, mimicking Playwright raising on
    an invalid selector string (used to test the "bad selector must not abort" path).
    """

    def __init__(self, matches: LocatorInit = None, raises: Optional[BaseException] = None) -> None:
        """Store normalized matches and an optional exception to raise on count()."""
        self._matches: list[Match] = _normalize(matches)
        self._raises = raises

    @property
    def first(self) -> FakeLocator:
        """Return a single-element locator for the first match, or empty."""
        if self._matches:
            return FakeLocator([self._matches[0]])
        return FakeLocator([])

    async def text_content(self, *args: Any, **kwargs: Any) -> Optional[str]:
        """Return the ``text`` of the first match, or None if empty."""
        if not self._matches:
            return None
        return self._matches[0].get("text")

    async def all_text_contents(self, *args: Any, **kwargs: Any) -> list[str]:
        """Return ``text`` values for all matches that carry one."""
        return [m.get("text") for m in self._matches if m.get("text") is not None]

    async def get_attribute(self, name: str, *args: Any, **kwargs: Any) -> Optional[str]:
        """Return the named attribute of the first match, or None."""
        if not self._matches:
            return None
        return self._matches[0].get("attrs", {}).get(name)

    async def all(self) -> list[FakeLocator]:
        """Return each match as its own single-element FakeLocator."""
        return [FakeLocator([m]) for m in self._matches]

    async def count(self) -> int:
        """Return the number of matches, or raise if ``raises`` was set."""
        if self._raises is not None:
            raise self._raises
        return len(self._matches)

    async def click(self, *args: Any, **kwargs: Any) -> None:
        """No-op click stub."""
        return None

    async def is_visible(self, *args: Any, **kwargs: Any) -> bool:
        """Return True if the first match is visible (or absent means False)."""
        if not self._matches:
            return False
        return bool(self._matches[0].get("visible", True))

    def locator(self, selector: str) -> FakeLocator:
        """Nested lookup: a matched element's own ``card.locator(child)`` call."""
        if self._matches:
            sub = self._matches[0].get("sub", {})
            return sub.get(selector, FakeLocator([]))
        return FakeLocator([])


class FakePage:
    """A stand-in for a Playwright ``Page``.

    ``locators`` maps a selector string to the ``FakeLocator`` that
    ``page.locator(selector)`` should return; unknown selectors yield an empty
    locator. ``title`` sets the (awaitable) page title. ``content`` sets the string
    returned by the (awaitable) ``content()`` call used for HTML-snapshot extraction.
    """

    def __init__(
        self,
        locators: Optional[dict[str, FakeLocator]] = None,
        title: str = "",
        url: str = "https://example.test/",
        content: str = "",
    ) -> None:
        """Set up AsyncMock stubs and the locator map."""
        self._locators: dict[str, FakeLocator] = locators or {}
        self.url = url
        self.title = AsyncMock(return_value=title)
        self.content = AsyncMock(return_value=content)
        self.goto = AsyncMock()
        self.set_content = AsyncMock()
        self.close = AsyncMock()
        self.route = AsyncMock()
        self.set_extra_http_headers = AsyncMock()
        self.wait_for_selector = AsyncMock()
        self.wait_for_load_state = AsyncMock()
        self.wait_for_timeout = AsyncMock()

    def locator(self, selector: str) -> FakeLocator:
        """Return the pre-configured FakeLocator for selector, or an empty one."""
        return self._locators.get(selector, FakeLocator([]))


class FakeContext:
    """A stand-in for a Playwright ``BrowserContext`` for the capture path.

    ``cookies`` is the list ``context.cookies()`` returns; if ``cookies_after_clear`` is
    given it is what ``cookies()`` returns after ``clear_cookies()`` (defaults to empty,
    i.e. clearing works). ``origins`` seeds ``storage_state()['origins']``.
    """

    def __init__(
        self,
        page: Optional[FakePage] = None,
        cookies: Optional[list] = None,
        cookies_after_clear: Optional[list] = None,
        origins: Optional[list] = None,
    ) -> None:
        """Set up AsyncMock stubs, cookie lists, and storage state."""
        self._cookies = list(cookies or [])
        self._cookies_after_clear = list(cookies_after_clear or [])
        self._cleared = False
        self.storage_state = AsyncMock(return_value={"cookies": [], "origins": list(origins or [])})
        self.new_page = AsyncMock(return_value=page if page is not None else FakePage())
        self.close = AsyncMock()

    async def cookies(self, *args: Any, **kwargs: Any) -> list:
        """Return post-clear cookies if cleared, else the initial list."""
        return self._cookies_after_clear if self._cleared else self._cookies

    async def clear_cookies(self, *args: Any, **kwargs: Any) -> None:
        """Mark cookies as cleared so subsequent cookies() returns the after-clear list."""
        self._cleared = True


class FakeBrowser:
    """A stand-in for a Playwright ``Browser`` whose ``new_page()`` is awaitable.

    Pass a single ``page`` (returned every call) or a ``page_factory`` callable
    (invoked per call, e.g. to hand out a fresh page per attempt / per URL). ``close()``
    is an ``AsyncMock`` so ``await browser.close()`` works. ``context`` (a
    ``FakeContext``) is what ``new_context()`` returns for the capture path.
    """

    def __init__(
        self,
        page: Optional[FakePage] = None,
        page_factory: Optional[Any] = None,
        context: Optional[FakeContext] = None,
    ) -> None:
        """Configure new_page/new_context stubs from a fixed page or factory callable."""
        if page_factory is not None:
            self.new_page = AsyncMock(side_effect=page_factory)
        else:
            self.new_page = AsyncMock(return_value=page if page is not None else FakePage())
        self.new_context = AsyncMock(return_value=context if context is not None else FakeContext())
        self.version = "chromium-test"
        self.close = AsyncMock()


def build_playwright_factory(browser: FakeBrowser) -> MagicMock:
    """Build an ``async_playwright()`` stand-in whose context manager yields an object
    with ``chromium.launch`` returning ``browser`` (an awaitable).

    Patch ``schema_detection.async_playwright`` (or any module using the same shape) with
    the returned factory.
    """
    playwright_obj = MagicMock()
    playwright_obj.chromium.launch = AsyncMock(return_value=browser)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=playwright_obj)
    cm.__aexit__ = AsyncMock(return_value=False)

    return MagicMock(return_value=cm)


# ---------------------------------------------------------------------------
# httpx doubles
# ---------------------------------------------------------------------------


class FakeResponse:
    """A stand-in for an ``httpx.Response`` as used by the scraping code.

    * ``raise_for_status()`` raises ``raise_status_exc`` if given, else no-ops.
    * ``json()`` raises ``json_exc`` if given, else returns ``json_data``.
    """

    def __init__(
        self,
        json_data: Any = None,
        json_exc: Optional[BaseException] = None,
        raise_status_exc: Optional[BaseException] = None,
        text: str = "",
    ) -> None:
        """Store the canned response data and optional exception triggers."""
        self._json_data = json_data
        self._json_exc = json_exc
        self._raise_status_exc = raise_status_exc
        self.text = text

    def raise_for_status(self) -> None:
        """Raise raise_status_exc if set, otherwise no-op."""
        if self._raise_status_exc is not None:
            raise self._raise_status_exc

    def json(self) -> Any:
        """Raise json_exc if set, otherwise return json_data."""
        if self._json_exc is not None:
            raise self._json_exc
        return self._json_data


def build_async_client_factory(
    responses: Optional[list] = None,
    get_side_effect: Optional[list] = None,
) -> tuple[MagicMock, MagicMock]:
    """Build a replacement for ``httpx.AsyncClient`` used as an async context manager.

    Returns ``(factory, client)`` where ``factory`` is what you patch
    ``httpx.AsyncClient`` with (calling it returns an async CM whose ``__aenter__``
    yields ``client``), and ``client.get`` is an ``AsyncMock``.

    Provide either ``responses`` (each awaited ``client.get`` returns the next one)
    or ``get_side_effect`` (entries that are exceptions are raised, matching how
    ``AsyncMock.side_effect`` treats an iterable).
    """
    client = MagicMock()
    if get_side_effect is not None:
        client.get = AsyncMock(side_effect=list(get_side_effect))
    else:
        client.get = AsyncMock(side_effect=list(responses or []))

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=cm)
    return factory, client
