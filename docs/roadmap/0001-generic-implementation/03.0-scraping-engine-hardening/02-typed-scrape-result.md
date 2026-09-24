# 02 - Typed scrape result

**Parent task:** [README.md](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/README.md)
**Status:** ⬜ Not started
**Depends on:** [01-page-lifecycle-and-leaks.md](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/01-page-lifecycle-and-leaks.md)
**Role:** Architect → Python Expert

## Context

`scrape_book()` returns a union of three shapes: a `dict` on success, `(None, "DUPLICATE")`, or
`(None, "FAILED")`. Its Leanpub path returns a fourth shape: bare `None`. `commands/scrape_urls.py` pattern-matches
on `isinstance`/`len(result) == 2`, and anything unexpected falls into `UNKNOWN_ERROR`. A 404, a timeout, and a
parse error all collapse into `FAILED`, so `failed_books.csv` can't tell "the URL is dead" from "our selector
broke".

## Requirements

- New module `src/bookscraper/scraping/results.py`:

  ```python
  class ScrapeStatus(str, Enum):
      SUCCESS = "SUCCESS"
      DUPLICATE = "DUPLICATE"
      NOT_FOUND = "NOT_FOUND"      # 404 / page-not-found title
      TIMEOUT = "TIMEOUT"          # all attempts timed out
      BLOCKED = "BLOCKED"          # bot wall detected (populated by task 03.2 subtask 04)
      PARSE_ERROR = "PARSE_ERROR"  # page loaded, required field missing
      FAILED = "FAILED"            # anything else

  @dataclass(frozen=True)
  class ScrapeResult:
      status: ScrapeStatus
      url: str
      site: str
      book: Optional[dict] = None   # set iff status is SUCCESS
      error: Optional[str] = None   # short, secret-free reason; None on SUCCESS/DUPLICATE
      attempts: int = 1
  ```

  `ScrapeResult.__post_init__` enforces `book is not None` ⇔ `status is SUCCESS`.
- `scrape_book()` and `get_leanpub_book_details()` return `ScrapeResult` on every path. `get_leanpub_book_details`
  keeps a thin wrapper, or a flag, so `commands/search.py` keeps receiving what it needs. Update that caller in the
  same subtask.
- `commands/scrape_urls.py`: replace the `isinstance` ladder with a `match result.status`. `failed_books.csv` gains
  an `error` column and uses the enum's value as `status`. Delete the `UNKNOWN_ERROR` branch, since it can no
  longer happen.
- `commands/search.py`: the failure records use `result.url` / `result.error`. This fixes today's `"url": "N/A"`,
  caused by reading a `url` key that Leanpub candidates don't have (they carry `book_url`).
- Add `ScrapeStatus` counts to the end-of-run summary in both commands.

## Files

- Create `src/bookscraper/scraping/results.py`.
- Modify `src/bookscraper/scraping/scrape_details.py`, `src/bookscraper/commands/scrape_urls.py`,
  `src/bookscraper/commands/search.py`.
- Create `tests/unit/scraping/test_results.py`; modify `tests/mock/scraping/test_scrape_details.py`,
  `tests/mock/commands/test_scrape_urls.py`, `tests/mock/commands/test_search.py`.

## Tests

- `test_scrape_result_success_requires_book`
- `test_scrape_result_non_success_forbids_book`
- `test_scrape_book_404_returns_not_found`
- `test_scrape_book_exhausted_timeouts_return_timeout_with_attempt_count`
- `test_leanpub_failure_returns_failed_result_not_none`
- `test_scrape_urls_failed_csv_has_status_and_error_columns`
- `test_search_failed_record_uses_real_book_url`
- `test_run_summary_counts_each_status`

## Success criteria

- [ ] No function in `scraping/` returns a bare tuple or `None` as a status signal.
- [ ] `grep -n "UNKNOWN_ERROR" src/` returns nothing.
