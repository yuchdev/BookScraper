# Security

Threat models, security review outputs, and posture documentation for Book Scrapper.

The `security-auditor` agent owns this directory. Every change touching auth,
secrets, external integrations, or untrusted-input ingestion triggers a security
review whose output is stored here.

## Naming convention

`threat-model-<scope>.md` for threat models, `review-<scope>-<YYYY-MM-DD>.md`
for point-in-time reviews.

## What a threat model must contain

1. **Scope** - which components and trust boundaries are in scope.
2. **Assets** - what secrets, PII, and data are handled.
3. **Threat actors** - attacker profiles considered.
4. **STRIDE analysis** - Spoofing, Tampering, Repudiation, Info Disclosure, DoS, Elevation.
5. **Mitigations** - existing controls and open gaps.
6. **Verdict** - CRITICAL (merge blocked) / HIGH / MEDIUM / LOW / INFO.

## Security rules (non-negotiable)

- Never log secrets; rely on this project's log-redaction mechanism (if any)
  and verify it covers new sinks.
- Never hard-code credentials. Read from settings/env.
- Treat all untrusted external input as sensitive - no unredacted raw input
  in logs, exceptions, stored reports, or API error bodies.
- Untrusted input must never reach a shell, SQL string, `eval`, or an AI
  prompt without sanitization/parameterization.

> **SME REVIEW NEEDED (AI-drafted - verify before relying on this):**
>
> **1. Scope.** Two locally-run CLI entry points (`src/scrape_existing_books.py`,
> `src/bookscraper/search_and_scrape.py`). Trust boundaries: the operator-supplied URL
> CSV; four third-party sites/APIs the project does not control; MongoDB Atlas over
> TLS; the local filesystem (CSV exports, `~/.bookscrapper/logs/*.log`, `.env`,
> `~/.bookscrapper/`).
>
> **2. Assets.** `MONGODB_URI` with inline Atlas credentials (from `.env`, falling back
> to `~/.bookscrapper/driver_string.txt`); the X.509 client cert/key at `TLS_CERT_FILE`
> (default `~/.bookscrapper/X509-cert-142838411852079927.pem`) and CA bundle at
> `TLS_CA_FILE`; the `bookscraper_db.books` corpus; scraped author names (personal data)
> and third-party copyrighted descriptions; the per-run log files under `~/.bookscrapper/logs/`.
>
> **3. Threat actors.** A compromised or hostile scrape target serving crafted
> markup/JSON; anyone who can write the input URL CSV; anyone with read access to
> `~/.bookscrapper/`, since up to 10 retained run logs can accumulate sensitive data over time
> (all severities from every module's logger land there, gated only by `--log-severity`); a
> network attacker on the Atlas path.
>
> **4. STRIDE analysis (initial).**
> - *Spoofing* - `identify_website()` routes on a bare substring match, so a URL merely
>   containing `amazon.com` is parsed with Amazon's selectors. No scheme/host allowlist;
>   `validators` is pinned in `requirements.txt` but never imported.
> - *Tampering* - scraped values reach MongoDB documents and CSV rows unvalidated;
>   `$`-prefixed and dotted keys and leading `=`/`+`/`-`/`@` cells are unguarded.
>   `scrape_details.py:60-65` strips HTML from `about_the_book` with regex, not a parser.
> - *Repudiation* - each run log under `~/.bookscrapper/logs/` has no integrity control
>   (`_prune_old_logs()` silently deletes the oldest beyond the 10-file cap with no archival).
> - *Information disclosure* - `exc_info=True` logging of MongoDB failures can write
>   connection strings containing credentials into the run log; since `configure_logging()`
>   attaches the file handler to the root logger, every module's log calls (not just
>   `book_utils.print_log()`) now reach this file, widening what could land there. Run-artifact
>   CSVs
>   (`books.csv`, `scraped_books.csv`, `failed_books.csv`, `other_links.csv`,
>   `failed_urls.csv`) are now gitignored rather than committed; only the curated,
>   hand-maintained input lists under `content/` are tracked.
> - *Denial of service* - `get_leanpub_search_results_via_api` paginates `while True`
>   with no cap and accumulates in memory; `search_and_scrape` gathers every detail fetch
>   at once with no concurrency limit; `scrape_book` leaks a Playwright page per Leanpub
>   call.
> - *Elevation of privilege* - low in-process risk (no shell, `eval`, or SQL), but with
>   X.509 auth, filesystem read access to the PEM is equivalent to database credentials.
>
> **5. Mitigations.** In place: TLS with `tlsAllowInvalidCertificates=False`; secrets read
> from env rather than hard-coded; a unique index on `hash`; `route_handler` blocking
> non-document requests; bounded retries in `scrape_book`. Open gaps: no URL allowlist; no
> `robots.txt`/ToS check for any target; no log-redaction utility; no rate limiting beyond
> the 0.5-1.5s sleep between Leanpub search pages; spoofed `USER_AGENTS` rotation;
> committed run artifacts; and the inert dedup check at `scrape_details.py:317`.
>
> **6. Verdict (draft).** HIGH - driven by credential exposure via unredacted exception
> logging and the unvalidated URL ingestion path. Confirm with a real review before
> treating this as the project's security posture.
