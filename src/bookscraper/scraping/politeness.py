"""Politeness primitives for the scraping tier - rate limiting (and, in later subtasks, shared
backoff / bot-wall detection).

Everything here is deliberately transport-agnostic: there are **no** Playwright or httpx imports,
the time source and the sleep coroutine are injectable, and nothing reaches the network. A caller
wraps its own request with :meth:`RateLimiter.acquire`; the limiter only decides *when* that
request may proceed. That keeps the whole module unit-testable against a fake clock (no real time
passes in tests) and lets subtasks 02-06 layer robots.txt, ``Retry-After`` backoff, and
circuit-breaking on top without entangling them with the request machinery.

The design goal is *identify and back off*, never disguise or bypass: being slow is always
acceptable, being blocked is not.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Optional
from urllib.parse import urlparse

from .parameters import DEFAULT_RATE_LIMIT, RATE_LIMITS

# Injectable collaborators, aliased so signatures stay readable and later subtasks can reuse them:
# a monotonic time source and an awaitable sleep. The production defaults are
# ``time.monotonic`` / ``asyncio.sleep``; tests pass a fake clock whose ``sleep`` advances its own
# virtual time so no real time passes.
Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class RateLimiter:
    """An async token-bucket rate limiter that spaces requests without busy-waiting.

    The bucket holds up to ``burst`` tokens and refills continuously at ``rate_per_sec`` tokens
    per second. :meth:`acquire` consumes one token, sleeping (never spinning) for exactly as long
    as it takes the bucket to earn the next token when it is empty. The bucket starts full, so the
    first ``burst`` acquires proceed immediately and only sustained traffic is throttled to the
    configured rate.

    A single :class:`asyncio.Lock` serializes acquirers, so concurrent callers queue in FIFO order
    and the configured spacing holds across an entire ``asyncio.gather`` batch, not just one task.
    """

    def __init__(
        self,
        rate_per_sec: float,
        burst: int = 1,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ):
        """Configure the bucket's rate, capacity, and (injectable) time source.

        :param rate_per_sec: Steady-state refill rate in tokens (requests) per second; must be
            positive.
        :param burst: Bucket capacity - the number of requests allowed back-to-back from a full
            bucket; must be at least 1.
        :param clock: A monotonic time source returning seconds (injected in tests).
        :param sleep: An awaitable sleep taking a duration in seconds (injected in tests).
        :raises ValueError: If ``rate_per_sec`` is not positive or ``burst`` is less than 1.
        """
        if rate_per_sec <= 0:
            raise ValueError(f"rate_per_sec must be positive, got {rate_per_sec!r}")
        if burst < 1:
            raise ValueError(f"burst must be at least 1, got {burst!r}")
        self._rate = float(rate_per_sec)
        self._capacity = float(burst)
        self._tokens = float(burst)  # Start full so a cold limiter allows an immediate burst.
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Block until a token is available, then consume it.

        Refills the bucket for the elapsed time, and if it is still short of one token sleeps for
        precisely the shortfall's worth of refill time before rechecking - so the method waits
        rather than busy-loops, and wakes with just enough credit to proceed.
        """
        async with self._lock:
            while True:
                now = self._clock()
                elapsed = now - self._updated
                self._updated = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                shortfall = 1.0 - self._tokens
                await self._sleep(shortfall / self._rate)


class DomainRateLimiters:
    """A per-host registry of :class:`RateLimiter` instances.

    Each distinct registrable host gets its own limiter (created lazily on first use), so one
    slow site never throttles another and a run issues no more than each host's configured rate.
    Hosts are keyed with any leading ``www.`` stripped, so ``www.amazon.com`` and ``amazon.com``
    share one bucket. An unlisted host falls back to ``default``.

    One instance is created per run in the command and threaded down to the scrapers; a shared
    module-level instance (:func:`default_limiters`) backs call sites that have not been updated
    to pass one explicitly.
    """

    def __init__(
        self,
        config: dict[str, tuple[float, int]],
        default: tuple[float, int],
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ):
        """Store the per-host config, the fallback rate, and the (injectable) time source.

        :param config: A mapping of registrable host to ``(requests_per_second, burst)``.
        :param default: The ``(requests_per_second, burst)`` used for any host not in ``config``.
        :param clock: A monotonic time source passed through to every created limiter.
        :param sleep: An awaitable sleep passed through to every created limiter.
        """
        self._config = config
        self._default = default
        self._clock = clock
        self._sleep = sleep
        self._limiters: dict[str, RateLimiter] = {}

    @staticmethod
    def registrable_host(url: str) -> str:
        """Return the lowercased host of ``url`` with any leading ``www.`` removed.

        This is the limiter key: it collapses the ``www.`` and bare spellings of a host onto one
        bucket. A URL with no host (or a bare path) yields an empty string, which still works as a
        stable fallback key.

        :param url: The absolute (or scheme-relative) URL whose host to extract.
        :returns: The limiter key for ``url``.
        """
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host

    def for_url(self, url: str) -> RateLimiter:
        """Return (creating on first use) the :class:`RateLimiter` for ``url``'s host.

        :param url: The URL about to be requested.
        :returns: The host's limiter, built from ``config`` or the ``default`` rate.
        """
        host = self.registrable_host(url)
        limiter = self._limiters.get(host)
        if limiter is None:
            rate, burst = self._config.get(host, self._default)
            limiter = RateLimiter(rate, burst, clock=self._clock, sleep=self._sleep)
            self._limiters[host] = limiter
        return limiter


def build_default_limiters() -> DomainRateLimiters:
    """Construct a :class:`DomainRateLimiters` from the configured ``RATE_LIMITS`` defaults.

    Used both to seed the module-level shared instance and by each command to create its own
    per-run registry, so the two can never drift from the configured rates.

    :returns: A registry wired to ``parameters.RATE_LIMITS`` / ``parameters.DEFAULT_RATE_LIMIT``.
    """
    return DomainRateLimiters(RATE_LIMITS, DEFAULT_RATE_LIMIT)


# Shared fallback instance for call sites that do not (yet) receive a per-run registry. Built once
# at import; its lazily-created limiters live for the process, which is correct for a single run.
_DEFAULT_LIMITERS: Optional[DomainRateLimiters] = None


def default_limiters() -> DomainRateLimiters:
    """Return the process-wide shared :class:`DomainRateLimiters`, creating it on first use.

    :returns: The module-level fallback registry used when no per-run instance is passed in.
    """
    global _DEFAULT_LIMITERS
    if _DEFAULT_LIMITERS is None:
        _DEFAULT_LIMITERS = build_default_limiters()
    return _DEFAULT_LIMITERS
