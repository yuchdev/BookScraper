"""Unit tests for bookscraper.scraping.politeness (subtask 03.2/01).

Pure logic, no mocks and no real time: a fake clock advances only when the limiter sleeps, so
these assert exact simulated timing of the token bucket and the per-host routing of
``DomainRateLimiters`` without ever waiting on the wall clock.
"""

from __future__ import annotations

import pytest

from bookscraper.scraping.politeness import DomainRateLimiters, RateLimiter


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
