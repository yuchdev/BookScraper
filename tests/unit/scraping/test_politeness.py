"""Unit tests for bookscraper.scraping.politeness (subtask 03.2/01).

Pure logic, no mocks and no real time: a fake clock advances only when the limiter sleeps, so
these assert exact simulated timing of the token bucket and the per-host routing of
``DomainRateLimiters`` without ever waiting on the wall clock.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bookscraper.scraping.politeness import (
    DomainRateLimiters,
    RateLimiter,
    RobotsCache,
    RobotsResponse,
)

# tests/unit/scraping/test_politeness.py -> parents[2] == tests/
_ROBOTS_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "robots"

# A representative browser user-agent for robots matching (never matches a named spider block,
# so it falls to the catch-all ``*`` group - the realistic case for this scraper).
_UA = "Mozilla/5.0 (compatible; bookscraper-tests)"


def _robots_text(site: str) -> str:
    """Return the captured ``robots.txt`` body for ``site`` from the offline fixture corpus."""
    return (_ROBOTS_DIR / f"{site}.txt").read_text(encoding="utf-8")


class CountingFetcher:
    """A fake :data:`RobotsFetcher` that returns a fixed response and counts its invocations.

    Used to assert both the fail-closed/404/parse behaviour (via the canned response) and the
    once-per-host fetch contract (via ``calls``) without any real network.
    """

    def __init__(self, response: RobotsResponse):
        """Store the canned response and start the call counter at zero."""
        self.response = response
        self.calls = 0

    async def __call__(self, robots_url: str) -> RobotsResponse:
        """Record the call and return the canned response (ignoring ``robots_url``)."""
        self.calls += 1
        return self.response


class RaisingFetcher:
    """A fake :data:`RobotsFetcher` that always raises, simulating a transport/fetch failure."""

    async def __call__(self, robots_url: str) -> RobotsResponse:
        """Raise to exercise the cache's fail-closed branch."""
        raise RuntimeError("boom")


async def test_robots_disallowed_path_returns_false():
    """
    [Unit] politeness: verifies a robots.txt-disallowed path is refused and an allowed one passes.

    Scenario:
        Given a RobotsCache fed Amazon's captured robots.txt
        When allowed() is checked for a disallowed /gp/cart path and an allowed /dp/ path
        Then the disallowed path returns False and the allowed path returns True

    Boundaries:
        - Offline: the body comes from the committed fixture, never a live fetch
        - Pure rule evaluation; crawl-delay and fetch-failure are separate tests

    On failure, first check:
        - The can_fetch delegation in RobotsCache.allowed, or the fixture's Disallow lines
    """
    fetcher = CountingFetcher(RobotsResponse(status_code=200, text=_robots_text("amazon")))
    cache = RobotsCache(fetcher, DomainRateLimiters({}, (1_000_000.0, 1_000_000)))

    assert await cache.allowed("https://www.amazon.com/gp/cart", _UA) is False
    assert await cache.allowed("https://www.amazon.com/dp/B0ABCDEFGH/", _UA) is True


async def test_robots_fetched_once_per_host():
    """
    [Unit] politeness: verifies robots.txt is fetched at most once per host per run.

    Scenario:
        Given a RobotsCache with a call-counting fetcher
        When allowed() is checked three times for the same host
        Then the fetcher is invoked exactly once (the parsed rules are cached)

    Boundaries:
        - Single host; per-host isolation is covered by the rate-limiter routing tests

    On failure, first check:
        - The ``host not in self._parsers`` guard (and the lock) in RobotsCache.allowed
    """
    fetcher = CountingFetcher(RobotsResponse(status_code=200, text=_robots_text("leanpub")))
    cache = RobotsCache(fetcher, DomainRateLimiters({}, (1_000_000.0, 1_000_000)))

    for _ in range(3):
        await cache.allowed("https://leanpub.com/api/v1/cache/books/x.json", _UA)

    assert fetcher.calls == 1


async def test_robots_fetch_error_fails_closed():
    """
    [Unit] politeness: verifies a robots.txt fetch failure disallows the whole host (fail closed).

    Scenario:
        Given a RobotsCache whose fetcher raises
        When allowed() is checked for any path on that host
        Then it returns False (never scrape blind on an inconclusive robots.txt)

    Boundaries:
        - Transport failure only; a 5xx is classified the same way via the status branch

    On failure, first check:
        - The ``except Exception`` fail-closed branch in RobotsCache._load
    """
    cache = RobotsCache(RaisingFetcher(), DomainRateLimiters({}, (1_000_000.0, 1_000_000)))

    assert await cache.allowed("https://www.packtpub.com/product/x", _UA) is False


async def test_robots_404_allows_all():
    """
    [Unit] politeness: verifies a 404 robots.txt allows every path (RFC 9309 convention).

    Scenario:
        Given a RobotsCache whose fetcher returns HTTP 404
        When allowed() is checked for an arbitrary path
        Then it returns True (no robots.txt means no restrictions)

    Boundaries:
        - Status classification only; the body is irrelevant on a 404

    On failure, first check:
        - The ``status == 404`` branch in RobotsCache._load
    """
    fetcher = CountingFetcher(RobotsResponse(status_code=404, text="not found"))
    cache = RobotsCache(fetcher, DomainRateLimiters({}, (1_000_000.0, 1_000_000)))

    assert await cache.allowed("https://example.test/anything", _UA) is True


async def test_crawl_delay_lowers_rate_limit():
    """
    [Unit] politeness: verifies a robots.txt Crawl-delay lowers the host's rate limiter.

    Scenario:
        Given a host configured at 2.0 req/s and a robots.txt declaring Crawl-delay: 2
        When allowed() loads those rules
        Then that host's limiter rate is lowered to 1/2 = 0.5 req/s (slower than configured)

    Boundaries:
        - Only a delay slower than configured lowers the rate; a faster one is ignored

    On failure, first check:
        - The crawl_delay/restrict_rate call in RobotsCache._load, or RateLimiter.restrict_rate
    """
    robots_text = "User-agent: *\nCrawl-delay: 2\nDisallow: /private\n"
    limiters = DomainRateLimiters({"leanpub.com": (2.0, 4)}, (0.5, 1))
    cache = RobotsCache(CountingFetcher(RobotsResponse(status_code=200, text=robots_text)), limiters)
    url = "https://leanpub.com/api/v1/cache/books/x.json"

    assert await cache.allowed(url, _UA) is True
    assert limiters.for_url(url)._rate == 0.5


class FakeClock:
    """A deterministic monotonic clock whose ``sleep`` advances virtual time instead of waiting.

    ``now()`` is the injected ``clock`` and ``sleep(d)`` is the injected ``sleep``; because sleep
    moves the same clock forward by exactly ``d`` seconds, a token bucket under test refills by the
    precise amount it slept for, with no real time elapsed.
    """

    def __init__(self):
        """Start virtual time at zero."""
        self.now = 0.0

    def time(self) -> float:
        """Return the current virtual time in seconds."""
        return self.now

    async def sleep(self, duration: float):
        """Advance virtual time by ``duration`` seconds (the fake 'await asyncio.sleep')."""
        self.now += duration


async def test_rate_limiter_spaces_requests_at_configured_rate():
    """
    [Unit] politeness: verifies a 0.5 req/s limiter spaces 5 acquires over at least 8 simulated s.

    Scenario:
        Given a RateLimiter at 0.5 req/s, burst 1, driven by a fake clock
        When acquire() is awaited five times in a row
        Then the first proceeds immediately and the rest are spaced 2 s apart, so at least 8 s of
        simulated time has passed by the fifth

    Boundaries:
        - Fake clock only; no real time passes and no busy-wait is exercised
        - Steady-state spacing, not the burst allowance (covered separately)

    On failure, first check:
        - The refill math (elapsed * rate) or the shortfall sleep in RateLimiter.acquire
    """
    clock = FakeClock()
    limiter = RateLimiter(0.5, burst=1, clock=clock.time, sleep=clock.sleep)

    times: list[float] = []
    for _ in range(5):
        await limiter.acquire()
        times.append(clock.now)

    assert times == [0.0, 2.0, 4.0, 6.0, 8.0]
    assert clock.now >= 8.0


async def test_rate_limiter_allows_burst_then_throttles():
    """
    [Unit] politeness: verifies a burst of 2 proceeds immediately, then the rate throttles.

    Scenario:
        Given a RateLimiter at 1.0 req/s, burst 2, driven by a fake clock
        When acquire() is awaited three times in a row
        Then the first two proceed at t=0 (the full bucket) and the third waits 1 s

    Boundaries:
        - Fake clock only; exercises the burst capacity and the first throttle after it drains

    On failure, first check:
        - The initial token count (bucket must start full) or the capacity cap in acquire
    """
    clock = FakeClock()
    limiter = RateLimiter(1.0, burst=2, clock=clock.time, sleep=clock.sleep)

    times: list[float] = []
    for _ in range(3):
        await limiter.acquire()
        times.append(clock.now)

    assert times == [0.0, 0.0, 1.0]


@pytest.mark.parametrize("bad_rate", [0.0, -1.0])
async def test_rate_limiter_rejects_non_positive_rate(bad_rate: float):
    """
    [Unit] politeness: verifies a non-positive rate is rejected rather than silently accepted.

    Scenario:
        Given a rate of zero or negative
        When RateLimiter is constructed
        Then a ValueError is raised (fail loudly, never divide by zero later)

    Boundaries:
        - Construction-time validation only; no acquire is attempted

    On failure, first check:
        - The rate_per_sec guard in RateLimiter.__init__
    """
    with pytest.raises(ValueError, match="rate_per_sec"):
        RateLimiter(bad_rate)


async def test_rate_limiter_rejects_burst_below_one():
    """
    [Unit] politeness: verifies a burst below 1 is rejected (a zero-capacity bucket never fills).

    Scenario:
        Given a burst of 0
        When RateLimiter is constructed
        Then a ValueError is raised

    Boundaries:
        - Construction-time validation only; no acquire is attempted

    On failure, first check:
        - The burst guard in RateLimiter.__init__
    """
    with pytest.raises(ValueError, match="burst"):
        RateLimiter(1.0, burst=0)


def test_domain_limiters_share_limiter_for_www_and_bare_host():
    """
    [Unit] politeness: verifies www.host and the bare host resolve to one shared limiter.

    Scenario:
        Given a DomainRateLimiters configured for "amazon.com"
        When for_url is called for a www.amazon.com and a bare amazon.com URL
        Then both return the identical RateLimiter instance (one bucket per registrable host)

    Boundaries:
        - Routing/identity only; no acquire or timing is exercised

    On failure, first check:
        - The leading-"www." strip in DomainRateLimiters.registrable_host or the per-host cache

    """
    limiters = DomainRateLimiters({"amazon.com": (0.5, 1)}, (0.5, 1))

    first = limiters.for_url("https://www.amazon.com/dp/123")
    second = limiters.for_url("https://amazon.com/gp/product/456")

    assert first is second


def test_unknown_domain_uses_default():
    """
    [Unit] politeness: verifies a host missing from the config falls back to the default rate.

    Scenario:
        Given a DomainRateLimiters whose config lists only "leanpub.com" with a default of (0.5, 1)
        When for_url is called for an unlisted host
        Then the returned limiter carries the default rate and burst

    Boundaries:
        - Inspects the resolved limiter's configured rate/capacity, not its runtime timing

    On failure, first check:
        - The config.get(host, self._default) fallback in DomainRateLimiters.for_url
    """
    limiters = DomainRateLimiters({"leanpub.com": (2.0, 4)}, (0.5, 1))

    limiter = limiters.for_url("https://unknown-site.example/book/1")

    assert limiter._rate == 0.5
    assert limiter._capacity == 1.0


def test_registrable_host_handles_hostless_url():
    """
    [Unit] politeness: verifies a URL with no host yields a stable empty key, not a crash.

    Scenario:
        Given a bare path with no scheme or host
        When registrable_host is called
        Then it returns an empty string (a usable, stable fallback key)

    Boundaries:
        - Host extraction only; no limiter is created

    On failure, first check:
        - The ``urlparse(url).hostname or ""`` guard in registrable_host
    """
    assert DomainRateLimiters.registrable_host("/just/a/path") == ""
