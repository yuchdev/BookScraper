# Task 03.3 - Multi-Site Search Pipeline

**Parent milestone:** [plan.md](/docs/roadmap/0001-generic-implementation/plan.md)
**Status:** ⬜ Not started
**Category:** feature | **Priority:** P1
**Depends on:** [Task 03.0](/docs/roadmap/0001-generic-implementation/03.0-scraping-engine-hardening/README.md), [Task 03.2](/docs/roadmap/0001-generic-implementation/03.2-politeness-and-anti-bot/README.md), [Task 05.0](/docs/roadmap/0001-generic-implementation/05.0-cli-improvements/README.md)

## Scope

`bookscraper search` is the "discover new books" workflow, but only Leanpub works end-to-end:

- `SITES_TO_SCRAPE = ["leanpub"]`, and `commands/search.py` has an `if site_name.lower() == "leanpub":` branch
  with no `else`.
- `get_search_results_via_playwright()` exists (Amazon, with sponsored-link unwrapping) but nothing calls it, and
  its pagination loop is `range(1, SEARCH_MAXIMUM_PAGES)`, which for `2` fetches **one** page.
- `--max-search-pages` is parsed by `cli.py` but read by nobody.
- `SCRAPE_FILTERS` (rating threshold, minimum ISBNs) is defined in `parameters.py` and used nowhere.
- The "Deduplicate Cross-Site using ISBN / Fuzzy search / Hash" step is a comment.

This task turns `search` into a generic provider pipeline:
**search → pre-dedup → filter → detail scrape → cross-site dedup → save**.

## Subtasks

| #  | Document                                                             | Status         | Blocks     |
|----|----------------------------------------------------------------------|----------------|------------|
| 01 | [Search provider registry](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/01-search-provider-registry.md)     | ⬜ Not started | 02, 03, 06 |
| 02 | [Amazon search provider](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/02-amazon-search-provider.md)         | ⬜ Not started | 04         |
| 03 | [Packtpub & O'Reilly search providers](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/03-packtpub-and-oreilly-search.md) | ⬜ Not started | 04 |
| 04 | [Apply SCRAPE_FILTERS](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/04-apply-scrape-filters.md)             | ⬜ Not started | -          |
| 05 | [Cross-site deduplication](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/05-cross-site-deduplication.md)     | ⬜ Not started | -          |
| 06 | [Generic detail-scrape dispatch](/docs/roadmap/0001-generic-implementation/03.3-multi-site-search-pipeline/06-generic-detail-dispatch.md) | ⬜ Not started | -         |

## Key constraints

- Must not start before task 03.2's rate limiter, robots check, and bot-wall breaker are in place.
- Every provider has real-HTML (or real-JSON) fixtures from task 06.0 **before** its implementation PR.
- `SITES_TO_SCRAPE` / `SEARCH_QUERIES` stay as defaults. `--site` / `--query` (task 05.0) override them.
