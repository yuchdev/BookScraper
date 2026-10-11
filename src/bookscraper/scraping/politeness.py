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
import logging
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import mktime_tz, parsedate_tz
from typing import Optional, TypeVar, Union
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from ..book_utils import print_log
from .parameters import DEFAULT_RATE_LIMIT, RATE_LIMITS

# Dedicated logger so robots.txt decisions land in the run log alongside everything else.
_robots_logger = logging.getLogger("robots")

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

    def restrict_rate(self, max_rate_per_sec: float):
        """Lower the steady-state refill rate to ``max_rate_per_sec`` iff that is slower.

        Used to honor a robots.txt ``Crawl-delay``: the directive can only ever make this
        limiter *more* conservative, never faster, so a value that is non-positive or at least as
        fast as the current rate is ignored. The bucket capacity and current token count are left
        untouched; only the future refill rate changes.

        :param max_rate_per_sec: The slowest rate the directive permits, in tokens per second.
        """
        if 0 < max_rate_per_sec < self._rate:
            self._rate = float(max_rate_per_sec)


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


# Circuit-breaker defaults (subtask 03.2/04): open after this many consecutive BLOCKED results
# for a domain, and stay open for this cooldown. The cooldown is deliberately longer than any
# realistic single run, so a tripped breaker effectively skips that domain for the rest of the run.
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_COOLDOWN_S = 900.0


class CircuitBreaker:
    """A transport-free per-domain circuit breaker for repeated bot-wall blocks (subtask 03.2/04).

    This is a pure state machine with an injectable clock - no network, no Playwright/httpx, no
    logging. A caller feeds it the outcome of each scrape (:meth:`record_blocked` /
    :meth:`record_success`) and asks :meth:`is_open` before issuing a request; deciding *what* to
    do when the breaker is open (skip the request, log a warning) is the caller's job, keeping this
    class side-effect-free and unit-testable against a fake clock.

    After ``threshold`` **consecutive** :attr:`record_blocked` calls the breaker opens and stays
    open for ``cooldown_s`` seconds; while open, :meth:`is_open` is ``True`` so the caller skips
    every further request to that domain. A :meth:`record_success` resets the consecutive-block
    counter (a single block followed by a success never trips it). Once the cooldown elapses the
    breaker half-opens (resets), letting a later request retry the domain.
    """

    def __init__(
        self,
        threshold: int = CIRCUIT_BREAKER_THRESHOLD,
        cooldown_s: float = CIRCUIT_BREAKER_COOLDOWN_S,
        *,
        clock: Clock = time.monotonic,
    ):
        """Configure the open threshold, cooldown, and (injectable) time source.

        :param threshold: Consecutive BLOCKED results that open the breaker; must be at least 1.
        :param cooldown_s: Seconds the breaker stays open once tripped; must be positive.
        :param clock: A monotonic time source returning seconds (injected in tests).
        :raises ValueError: If ``threshold`` is less than 1 or ``cooldown_s`` is not positive.
        """
        if threshold < 1:
            raise ValueError(f"threshold must be at least 1, got {threshold!r}")
        if cooldown_s <= 0:
            raise ValueError(f"cooldown_s must be positive, got {cooldown_s!r}")
        self._threshold = threshold
        self._cooldown_s = float(cooldown_s)
        self._clock = clock
        self._consecutive_blocks = 0
        self._opened_at: Optional[float] = None

    def is_open(self) -> bool:
        """Return whether the breaker is currently open (so the caller should skip the request).

        A breaker that was opened more than ``cooldown_s`` ago half-opens here: its state is reset
        and ``False`` is returned, letting the next request retry the domain.

        :returns: ``True`` while the breaker is open and within its cooldown, else ``False``.
        """
        if self._opened_at is None:
            return False
        if self._clock() - self._opened_at >= self._cooldown_s:
            # Cooldown elapsed: half-open by resetting, so a later request may retry the domain.
            self._opened_at = None
            self._consecutive_blocks = 0
            return False
        return True

    def record_blocked(self) -> bool:
        """Record one BLOCKED result; return whether this call just opened the breaker.

        A no-op (returning ``False``) once already open. Otherwise it increments the consecutive
        count and, on reaching ``threshold``, opens the breaker and returns ``True`` so the caller
        can emit the single "circuit opened" warning exactly once.

        :returns: ``True`` iff this call transitioned the breaker from closed to open.
        """
        if self._opened_at is not None:
            return False
        self._consecutive_blocks += 1
        if self._consecutive_blocks >= self._threshold:
            self._opened_at = self._clock()
            return True
        return False

    def record_success(self):
        """Reset the consecutive-block counter after a successful scrape of the domain."""
        self._consecutive_blocks = 0


class DomainCircuitBreakers:
    """A per-host registry of :class:`CircuitBreaker` instances (subtask 03.2/04).

    Mirrors :class:`DomainRateLimiters`: each registrable host (leading ``www.`` stripped) gets its
    own breaker, created lazily on first use, so one blocked domain never short-circuits another.
    One instance is created per run in the command and threaded down to the scrapers; a shared
    module-level instance (:func:`default_breakers`) backs call sites given no explicit registry.
    """

    def __init__(
        self,
        threshold: int = CIRCUIT_BREAKER_THRESHOLD,
        cooldown_s: float = CIRCUIT_BREAKER_COOLDOWN_S,
        *,
        clock: Clock = time.monotonic,
    ):
        """Store the per-breaker threshold/cooldown and the (injectable) time source.

        :param threshold: Consecutive BLOCKED results that open each host's breaker.
        :param cooldown_s: Seconds each host's breaker stays open once tripped.
        :param clock: A monotonic time source passed through to every created breaker.
        """
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._breakers: dict[str, CircuitBreaker] = {}

    def for_url(self, url: str) -> CircuitBreaker:
        """Return (creating on first use) the :class:`CircuitBreaker` for ``url``'s host.

        :param url: The URL about to be requested.
        :returns: The host's circuit breaker.
        """
        host = DomainRateLimiters.registrable_host(url)
        breaker = self._breakers.get(host)
        if breaker is None:
            breaker = CircuitBreaker(self._threshold, self._cooldown_s, clock=self._clock)
            self._breakers[host] = breaker
        return breaker


def build_default_breakers() -> DomainCircuitBreakers:
    """Construct a :class:`DomainCircuitBreakers` with the configured threshold/cooldown defaults.

    Used both to seed the module-level shared instance and by each command to create its own
    per-run registry, so a breaker's state never leaks across runs in a long-lived process.

    :returns: A registry wired to ``CIRCUIT_BREAKER_THRESHOLD`` / ``CIRCUIT_BREAKER_COOLDOWN_S``.
    """
    return DomainCircuitBreakers(CIRCUIT_BREAKER_THRESHOLD, CIRCUIT_BREAKER_COOLDOWN_S)


# Shared fallback instance for call sites that do not (yet) receive a per-run registry. Built once
# on first use; its lazily-created breakers live for the process, which is correct for a single run.
_DEFAULT_BREAKERS: Optional[DomainCircuitBreakers] = None


def default_breakers() -> DomainCircuitBreakers:
    """Return the process-wide shared :class:`DomainCircuitBreakers`, creating it on first use.

    :returns: The module-level fallback registry used when no per-run instance is passed in.
    """
    global _DEFAULT_BREAKERS
    if _DEFAULT_BREAKERS is None:
        _DEFAULT_BREAKERS = build_default_breakers()
    return _DEFAULT_BREAKERS


# Shared backoff / Retry-After primitives (subtask 03.2/03). These stay transport-agnostic like
# the rest of the module: ``retry_async`` wraps any awaitable and ``parse_retry_after`` takes a
# plain header string, so the httpx-specific wrappers (``is_transient`` / ``httpx_retry_after``)
# live in ``scrape_details`` where the transport import already is.
T = TypeVar("T")

# A mapping from a raised exception to a server-requested delay in seconds (e.g. derived from a
# ``Retry-After`` header), or ``None`` to fall back to jittered backoff.
RetryAfter = Callable[[BaseException], Optional[float]]

# The process-wide jitter source used when a caller passes no ``rng`` to :func:`retry_async`. A
# module-level singleton (rather than a ``random.Random()`` call in the default argument) keeps
# the default evaluated once and ruff-clean; tests pass a seeded ``random.Random`` for determinism.
_DEFAULT_RNG = random.Random()


def _utcnow() -> datetime:
    """Return the current time as an aware UTC :class:`datetime`.

    The default clock for :func:`parse_retry_after`'s HTTP-date branch; tests inject their own so
    no wall-clock time is read.

    :returns: The current UTC time as a timezone-aware datetime.
    """
    return datetime.now(UTC)


def parse_retry_after(value: Optional[str], *, now: Callable[[], datetime] = _utcnow) -> Optional[float]:
    """Parse a ``Retry-After`` header value into a non-negative delay in seconds.

    Accepts the two RFC 9110 spellings - a delta-seconds integer (``"120"``) or an HTTP-date
    (``"Wed, 21 Oct 2026 07:28:00 GMT"``) - and returns how many seconds to wait from now. A
    missing, blank, or unparseable value yields ``None`` (so the caller falls back to jittered
    backoff), and a past HTTP-date clamps to ``0.0`` rather than going negative.

    :param value: The raw header value, or ``None`` when the header was absent.
    :param now: An injectable clock returning an aware UTC datetime (used for the HTTP-date form);
        defaults to :func:`_utcnow`. Injected in tests so no wall-clock time is read.
    :returns: The delay in seconds (``>= 0``), or ``None`` when the value cannot be parsed.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    # delta-seconds form: a bare non-negative integer. ``isdigit`` (no sign, no decimal point)
    # keeps this a pure test rather than a try/float that would swallow the HTTP-date case.
    if text.isdigit():
        return float(text)
    # HTTP-date form. ``parsedate_tz`` returns ``None`` (never raises) on an unparseable value, so
    # a malformed date falls back to jittered backoff without swallowing an exception.
    parsed = parsedate_tz(text)
    if parsed is None:
        return None
    return max(0.0, mktime_tz(parsed) - now().timestamp())


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    is_retryable: Callable[[BaseException], bool],
    retry_after: RetryAfter = lambda e: None,
    sleep: Sleep = asyncio.sleep,
    rng: random.Random = _DEFAULT_RNG,
) -> T:
    """Run ``fn`` with full-jitter exponential backoff, retrying only retryable failures.

    Awaits ``fn()`` up to ``attempts`` times. After a failure that ``is_retryable`` accepts (and
    when a retry remains) it sleeps before the next try: ``retry_after(exc)`` wins when it returns
    a value (capped at ``max_delay``), otherwise a full-jitter backoff
    ``rng.uniform(0, min(max_delay, base_delay * 2**(n-1)))`` where ``n`` is the 1-based number of
    the attempt that just failed. A non-retryable failure, or the final attempt's failure,
    re-raises unchanged (never wrapped), so the caller sees the original exception.

    :param fn: The zero-argument coroutine factory to attempt.
    :param attempts: The maximum number of attempts (``>= 1``); ``1`` disables retrying.
    :param base_delay: The backoff base in seconds (the first retry's uncapped ceiling).
    :param max_delay: The hard cap in seconds on any single sleep (jitter or ``Retry-After``).
    :param is_retryable: Predicate deciding whether a raised exception is worth retrying.
    :param retry_after: Maps a raised exception to a server-requested delay in seconds, or ``None``
        to fall back to jittered backoff (e.g. from a ``Retry-After`` header).
    :param sleep: The awaitable sleep taking a duration in seconds (injected in tests so no real
        time passes).
    :param rng: The random source for jitter (seed it in tests for determinism).
    :returns: Whatever ``fn`` returns on its first successful attempt.
    :raises Exception: The exception from a non-retryable or final-attempt failure, re-raised
        unchanged.
    """
    for n in range(1, attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: X002 - retry boundary: a retryable failure is backed off and re-attempted while a non-retryable one re-raises unchanged
            if not is_retryable(exc):
                raise
            override = retry_after(exc)
            if override is not None:
                delay = min(max_delay, override)
            else:
                delay = rng.uniform(0.0, min(max_delay, base_delay * 2 ** (n - 1)))
            await sleep(delay)
    # The final attempt is outside the loop: its exception (if any) propagates unchanged, so there
    # is no unreachable post-loop branch to leave uncovered.
    return await fn()


async def bounded_gather(
    coros_or_factories: Sequence[Union[Awaitable[T], Callable[[], Awaitable[T]]]],
    *,
    limit: int,
    on_result: Optional[Callable[[Union[T, BaseException]], None]] = None,
) -> list[Union[T, BaseException]]:
    """Await every item with at most ``limit`` in flight, preserving input order in the results.

    A sliding window built on a single :class:`asyncio.Semaphore`: each item acquires a slot before
    it runs and releases it on completion, so a slow item never blocks the whole run and a freed
    slot is immediately reused by the next queued item (unlike fixed ``gather`` batches, which pay
    for the slowest item in every batch). Each entry is either a coroutine/awaitable (awaited
    directly) or a zero-argument factory (called inside its slot to build the awaitable, so the
    awaitable is not even created until a slot is free).

    Exceptions are captured per item exactly like ``asyncio.gather(..., return_exceptions=True)``:
    a failing item's slot in the returned list holds the raised exception instead of a value, and
    one failure never cancels its siblings. The result list is index-aligned with the input no
    matter what order the items actually finished in.

    :param coros_or_factories: The awaitables (or zero-argument awaitable factories) to run.
    :param limit: The maximum number of items in flight at once; must be at least 1.
    :param on_result: An optional callback invoked with each item's result (value or captured
        exception) as it completes, in completion order - used for live progress reporting. It
        must not raise.
    :returns: A list of each item's result (or captured exception), index-aligned with the input.
    :raises ValueError: If ``limit`` is less than 1.
    """
    if limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit!r}")
    semaphore = asyncio.Semaphore(limit)
    results: list[Union[T, BaseException]] = [None] * len(coros_or_factories)  # type: ignore[list-item]

    async def _run(index: int, item: Union[Awaitable[T], Callable[[], Awaitable[T]]]):
        """Acquire a slot then await (or build-then-await) ``item``, storing its result at ``index``."""
        async with semaphore:
            try:
                awaitable = item() if callable(item) else item
                result: Union[T, BaseException] = await awaitable
            except Exception as exc:  # noqa: X002 - per-item capture boundary: a failing item's exception is stored in its result slot like gather return_exceptions rather than cancelling its siblings
                result = exc
            results[index] = result
            if on_result is not None:
                on_result(result)

    await asyncio.gather(*(_run(i, item) for i, item in enumerate(coros_or_factories)))
    return results


@dataclass(frozen=True)
class RobotsResponse:
    """A transport-agnostic robots.txt fetch result handed to :class:`RobotsCache`.

    The cache never imports a transport: a caller's injected fetcher does the actual HTTP and
    hands back just the status code and body as this small record, so :mod:`politeness` stays
    unit-testable with a plain fake fetcher and carries no Playwright/httpx import.

    :ivar status_code: The HTTP status of the ``robots.txt`` response.
    :ivar text: The decoded ``robots.txt`` body (empty string is fine).
    """

    status_code: int
    text: str


# A ``robots.txt`` fetcher: given the absolute ``robots.txt`` URL it returns a RobotsResponse (or
# raises, which the cache treats as a fetch failure and fails closed). The production fetcher wraps
# the shared httpx client in scrape_details; tests pass a fake coroutine.
RobotsFetcher = Callable[[str], Awaitable[RobotsResponse]]


class RobotsCache:
    """Per-run robots.txt gate: fetches each host's rules once, then answers :meth:`allowed`.

    The rules for a host are fetched (via the injected :data:`RobotsFetcher`) and parsed with the
    stdlib :class:`urllib.robotparser.RobotFileParser` the first time any URL on that host is
    checked, then cached for the rest of the run. Concurrent first-checks for the same host are
    serialized by a lock so the fetch really happens only once.

    The failure policy is deliberately conservative (*identify and back off*, never scrape blind):

    - A fetch that raises, or any non-2xx status other than 404 (including every 5xx), **fails
      closed** - the host is disallowed for the rest of the run, with a warning.
    - A 404 means "no robots.txt", so everything on that host is allowed (RFC 9309).
    - On a 2xx the rules are parsed and honored; a ``Crawl-delay`` lowers that host's
      :class:`RateLimiter` rate to ``1 / crawl_delay`` when that is slower than configured.
    """

    def __init__(self, fetcher: RobotsFetcher, limiters: DomainRateLimiters):
        """Wire the cache to its robots.txt fetcher and the per-run rate-limiter registry.

        :param fetcher: The injected coroutine that fetches a ``robots.txt`` URL (see
            :data:`RobotsFetcher`); the cache never performs I/O itself.
        :param limiters: The run's :class:`DomainRateLimiters`, so a parsed ``Crawl-delay`` can
            throttle the very limiter the scrapers acquire for that host.
        """
        self._fetcher = fetcher
        self._limiters = limiters
        # host -> parser (2xx) or None (404 / allow-all). A host in _denied is fail-closed.
        self._parsers: dict[str, Optional[RobotFileParser]] = {}
        self._denied: set[str] = set()
        self._lock = asyncio.Lock()

    async def allowed(self, url: str, user_agent: str) -> bool:
        """Return whether ``robots.txt`` permits ``user_agent`` to fetch ``url``.

        Loads and caches the host's rules on first use (fetched at most once per host per run).

        :param url: The absolute URL about to be fetched.
        :param user_agent: The user-agent the request will present (matched against the rules).
        :returns: ``True`` if the fetch is permitted; ``False`` if disallowed or fail-closed.
        """
        host = DomainRateLimiters.registrable_host(url)
        if host not in self._parsers and host not in self._denied:
            async with self._lock:
                if host not in self._parsers and host not in self._denied:
                    await self._load(url, host, user_agent)
        if host in self._denied:
            return False
        parser = self._parsers.get(host)
        if parser is None:
            return True
        return parser.can_fetch(user_agent, url)

    async def _load(self, url: str, host: str, user_agent: str):
        """Fetch, classify, and cache ``robots.txt`` for ``host`` (applying any ``Crawl-delay``).

        :param url: A sample URL on the host, used to derive the ``robots.txt`` URL and to key the
            host's rate limiter for a ``Crawl-delay`` adjustment.
        :param host: The registrable-host cache key.
        :param user_agent: The user-agent whose ``Crawl-delay`` (if any) is applied.
        """
        robots_url = self._robots_url(url)
        try:
            response = await self._fetcher(robots_url)
        except Exception as exc:  # noqa: X002 - fail-closed boundary: any robots.txt fetch failure disallows the host for the rest of the run rather than scraping blind
            self._deny(host, robots_url, f"fetch failed ({type(exc).__name__})")
            return
        status = response.status_code
        if status == 404:
            self._parsers[host] = None  # No robots.txt -> allow everything (RFC 9309).
            return
        if not 200 <= status < 300:
            self._deny(host, robots_url, f"HTTP {status}")
            return
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        self._parsers[host] = parser
        delay = parser.crawl_delay(user_agent)
        if delay:
            self._limiters.for_url(url).restrict_rate(1.0 / float(delay))

    def _deny(self, host: str, robots_url: str, reason: str):
        """Mark ``host`` fail-closed for the run and warn (console + run log).

        :param host: The registrable host to disallow.
        :param robots_url: The ``robots.txt`` URL whose fetch was inconclusive.
        :param reason: A short secret-free reason (status code or exception class).
        """
        self._denied.add(host)
        _robots_logger.warning(f"robots.txt unavailable for {robots_url} ({reason}); disallowing host (fail closed).")
        print_log(
            f"robots.txt unavailable for {robots_url} ({reason}); disallowing this host for the run.",
            "warning",
        )

    @staticmethod
    def _robots_url(url: str) -> str:
        """Return the ``https://<host>/robots.txt`` URL for ``url``'s host.

        :param url: The absolute URL whose host's ``robots.txt`` to locate.
        :returns: The ``robots.txt`` URL (always ``https``, per the subtask spec).
        """
        parts = urlparse(url)
        netloc = parts.netloc or parts.path
        return f"https://{netloc}/robots.txt"
