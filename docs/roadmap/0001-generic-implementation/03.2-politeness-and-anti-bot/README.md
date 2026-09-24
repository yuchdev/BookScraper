# Task 03.2 - Politeness & Anti-Bot Resilience

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** ⬜ Not started
**Category:** feature | **Priority:** P1
**Depends on:** [Task 03.0](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/README.md)

## Scope

The scraper's only politeness today is a random 3-5 s sleep between batches of 10 concurrent page loads, and a
0.5-1.5 s sleep between Leanpub API pages. Nothing limits per-host request rate, reads `robots.txt`, honors
`Retry-After`, recognizes a CAPTCHA page, or stops after a site starts blocking. Before task 03.3 fans `search`
out to Amazon, Packtpub, and O'Reilly, that has to exist. Without it, a run risks both getting the machine's IP
blocked and storing a bot-wall page as a "book".

## Subtasks

| #  | Document                                                                  | Status         | Blocks |
|----|---------------------------------------------------------------------------|----------------|--------|
| 01 | [Per-domain rate limiter](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/01-per-domain-rate-limiter.md)            | ⬜ Not started | 03, 06 |
| 02 | [robots.txt compliance](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/02-robots-txt-compliance.md)                | ⬜ Not started | -      |
| 03 | [Shared backoff & Retry-After](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/03-shared-backoff-and-retry-after.md) | ⬜ Not started | 04    |
| 04 | [Bot-wall detection & circuit breaker](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/04-bot-wall-detection.md)    | ⬜ Not started | -      |
| 05 | [Browser context & user-agent hygiene](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/05-browser-context-and-user-agents.md) | ⬜ Not started | - |
| 06 | [Bounded concurrency](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/06-bounded-concurrency.md)                    | ⬜ Not started | -      |

## Key constraints

- All politeness primitives live in one new module, `scraping/politeness.py`, and have **no** Playwright or httpx
  imports (they wrap callables), so they are unit-testable with a fake clock.
- Defaults are conservative: being slow is always acceptable, being blocked isn't.
- No evasion: this task makes the scraper *identify and back off*, never disguise or bypass (no CAPTCHA solving, no
  fingerprint spoofing, no proxy rotation). The agent guidelines' dual-use boundary applies.
