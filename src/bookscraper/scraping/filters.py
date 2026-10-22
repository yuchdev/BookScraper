"""Pure, I/O-free application of ``SCRAPE_FILTERS`` to the search pipeline (task 03.3 subtask 04).

``parameters.SCRAPE_FILTERS`` (a rating threshold plus a minimum-ISBN requirement) was dead config
until this subtask: nothing read it. This module is its single reader, split into the two stages the
``search`` pipeline applies it at:

* :func:`passes_search_filters` runs **before** the expensive detail scrape, on a
  :class:`~bookscraper.scraping.search_providers.SearchCandidate`'s rating, so a low-rated book is
  dropped without ever being fetched in full.
* :func:`passes_detail_filters` runs **after** the detail scrape, on the normalized
  :meth:`~bookscraper.models.BookRecord.to_document` document, counting its non-null
  ``isbn10``/``isbn13`` fields against ``min_isbns_required``.

Both functions are pure: they inspect their inputs and return a ``(keep, reason)`` verdict, never
touch the network or disk, and never mutate their arguments - the caller in
:mod:`bookscraper.commands.search` owns the counting, CSV diagnostic, and ``--no-filters`` bypass.

Per-site overrides: Leanpub books rarely carry ISBNs, so ``SCRAPE_FILTERS["per_site"]`` relaxes the
ISBN requirement for Leanpub. :func:`resolve_site_filters` merges a site's override onto the base
filters, and both stages resolve through it keyed on the candidate's / document's ``site``.
"""

from __future__ import annotations

from typing import Optional

from .search_providers import SearchCandidate


def resolve_site_filters(filters: dict, site: str) -> dict:
    """Merge any per-site override in ``filters["per_site"]`` onto the base filter keys for ``site``.

    The ``per_site`` block itself is never returned as a filter key; only its selected site's
    overrides (if any) are layered on top of the base keys. A site with no override yields the base
    filters unchanged. The returned dict is a fresh copy, so a caller cannot mutate the shared
    :data:`~bookscraper.scraping.parameters.SCRAPE_FILTERS` through it.

    :param filters: The full ``SCRAPE_FILTERS`` mapping (base keys plus an optional ``per_site``).
    :param site: The site key to resolve overrides for (e.g. ``"leanpub"``).
    :returns: The effective filter mapping for ``site``.
    """
    merged = {key: value for key, value in filters.items() if key != "per_site"}
    per_site = filters.get("per_site") or {}
    merged.update(per_site.get(site) or {})
    return merged


def passes_search_filters(candidate: SearchCandidate, filters: dict) -> tuple[bool, Optional[str]]:
    """Decide whether ``candidate`` survives the pre-detail-scrape rating filter.

    Applied before the detail scrape. When ``enable_rating_filter`` is false the candidate is always
    kept. Otherwise a rated candidate below ``min_rating`` is rejected, and an unrated candidate
    (``rating is None``) is kept only when ``include_new_books_without_rating`` is true.

    :param candidate: The search candidate whose ``site`` and ``rating`` are inspected.
    :param filters: The full ``SCRAPE_FILTERS`` mapping; per-site overrides are resolved internally.
    :returns: A ``(keep, reason)`` tuple - ``(True, None)`` to keep, or ``(False, reason)`` with a
        short, credential-free reason string naming why the candidate was dropped.
    """
    site_filters = resolve_site_filters(filters, candidate.site)
    if not site_filters.get("enable_rating_filter"):
        return True, None
    if candidate.rating is None:
        if site_filters.get("include_new_books_without_rating"):
            return True, None
        return False, "unrated book excluded"
    min_rating = site_filters.get("min_rating")
    if min_rating is not None and candidate.rating < min_rating:
        return False, f"rating {candidate.rating} < {min_rating}"
    return True, None


def passes_detail_filters(doc: dict, filters: dict) -> tuple[bool, Optional[str]]:
    """Decide whether a scraped detail document ``doc`` survives the ISBN-count filter.

    Applied after the detail scrape, on the normalized
    :meth:`~bookscraper.models.BookRecord.to_document` shape. Counts the non-null ``isbn10`` and
    ``isbn13`` fields and rejects the document when that count is below the (per-site resolved)
    ``min_isbns_required``.

    :param doc: The normalized detail document; its ``site``, ``isbn10`` and ``isbn13`` are read.
    :param filters: The full ``SCRAPE_FILTERS`` mapping; per-site overrides are resolved internally.
    :returns: A ``(keep, reason)`` tuple - ``(True, None)`` to keep, or ``(False, reason)`` with a
        short reason string naming the ISBN shortfall.
    """
    site_filters = resolve_site_filters(filters, doc.get("site") or "")
    min_isbns = site_filters.get("min_isbns_required", 0)
    isbn_count = sum(1 for key in ("isbn10", "isbn13") if doc.get(key))
    if isbn_count < min_isbns:
        return False, f"{isbn_count} ISBN(s) < {min_isbns} required"
    return True, None
