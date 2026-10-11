"""Shared Playwright browser-context factory for every scrape.

Both the detail/search scrapers (:mod:`.scrape_details`, :mod:`.search_utils`) and the
schema-detection snapshot path (:mod:`.schema_detection`) open their pages through
:func:`new_scrape_context`, so what detect-schema sees is exactly what the scraper sees.

Two things this factory fixes (task 03.2 subtask 05):

* **One isolated context per scrape attempt.** A fresh :class:`BrowserContext` has its own cookie
  jar, so concurrent scrapes can no longer leak cookies into one another the way pages created on
  the shared default context did.
* **A single, internally consistent identity.** The old code set only the ``User-Agent`` *header*
  (``page.set_extra_http_headers``) while ``navigator.userAgent`` and the ``Sec-CH-UA`` client
  hints still reported the real Chromium, so the page advertised two contradictory identities. A
  context-level ``user_agent`` keeps them in agreement, and the default ``"native"`` mode sets no
  override at all (Playwright's bundled Chromium is already internally consistent) - the honest
  default per the project's "identify and back off, never disguise" rule.
"""

from __future__ import annotations

import random
import re

from playwright.async_api import Browser, BrowserContext

from . import parameters

# The two honest ``parameters.USER_AGENT_MODE`` values. ``"native"`` overrides nothing;
# ``"rotate"`` sets a Chromium-family UA whose major version matches the installed engine.
USER_AGENT_MODE_NATIVE = "native"
USER_AGENT_MODE_ROTATE = "rotate"

# Context defaults shared by both modes: a stable locale and a desktop viewport, so a page's
# layout/content does not vary run to run with the host's screen size or locale.
CONTEXT_LOCALE = "en-US"
CONTEXT_VIEWPORT = {"width": 1366, "height": 900}

# Leading integer of a Playwright ``browser.version`` string (e.g. ``"120.0.6099.109"`` -> 120).
_VERSION_MAJOR_RE = re.compile(r"\s*(\d+)")


def chromium_major(version: str) -> int:
    """Return the leading major-version integer of a Playwright ``browser.version`` string.

    ``browser.version`` looks like ``"120.0.6099.109"``; only the leading ``120`` is needed to
    build a user-agent that agrees with the installed engine. A value with no leading integer
    (an unexpected build string) falls back to ``parameters.DEFAULT_CHROMIUM_MAJOR`` rather than
    raising, so a surprising version never aborts a scrape.

    :param version: The ``browser.version`` string reported by Playwright's Chromium.
    :returns: The Chromium major version, or the configured fallback when it cannot be parsed.
    """
    match = _VERSION_MAJOR_RE.match(version or "")
    if match is None:
        return parameters.DEFAULT_CHROMIUM_MAJOR
    return int(match.group(1))


def build_user_agents(major: int) -> list[str]:
    """Return the rotate-mode user-agent list with ``major`` filled into every template.

    :param major: The Chromium major version to splice into each ``{major}`` placeholder.
    :returns: One concrete user-agent string per entry in ``parameters.USER_AGENT_TEMPLATES``.
    """
    return [template.format(major=major) for template in parameters.USER_AGENT_TEMPLATES]


def resolve_rotate_user_agent(browser: Browser) -> str:
    """Pick a random Chromium-family user-agent matching the installed Chromium major version.

    The chosen string's ``Chrome/<major>`` matches ``browser.version``, so the presented header
    agrees with the engine that actually renders the page (no cross-engine spoofing).

    :param browser: The launched Playwright browser whose version sets the UA major.
    :returns: A single user-agent string for this scrape's context.
    """
    major = chromium_major(browser.version)
    return random.choice(build_user_agents(major))


async def new_scrape_context(browser: Browser) -> BrowserContext:
    """Open one isolated :class:`BrowserContext` for a single scrape attempt.

    The context carries its own cookie jar (so concurrent scrapes never share cookies) and a
    stable locale/viewport. Its user-agent is governed by ``parameters.USER_AGENT_MODE``:

    * ``"native"`` (the default): no ``user_agent`` is passed, so the context inherits Playwright's
      bundled, internally consistent Chromium identity - the honest default.
    * ``"rotate"``: a Chromium-family user-agent whose major version matches ``browser.version`` is
      set, keeping the header and the engine in agreement.

    :param browser: The launched Playwright browser to open the context on.
    :returns: A freshly opened browser context, to be closed by the caller.
    """
    context_kwargs: dict = {"locale": CONTEXT_LOCALE, "viewport": CONTEXT_VIEWPORT}
    if parameters.USER_AGENT_MODE == USER_AGENT_MODE_ROTATE:
        context_kwargs["user_agent"] = resolve_rotate_user_agent(browser)
    return await browser.new_context(**context_kwargs)
