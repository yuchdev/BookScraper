"""Cross-site, title-based deduplication helpers for the ``search`` pipeline (task 03.3 subtask 05).

The same book often appears on several sites: an O'Reilly title is resold on Amazon, a Packt book is
on both. The canonical ``hash`` (title + authors + year) catches *exact* matches, but site-specific
title decorations ("..., 2nd Edition", a subtitle after ``:``, a ``(... Series)`` parenthetical) and
author-spelling differences defeat it. The only fully reliable cross-site key is the ISBN-13, checked
against the store *after* the detail scrape (see
:meth:`~bookscraper.backends.storage.StorageBackend.book_exists_by_isbn13`).

This module is the *within-run, pre-detail-scrape* half: it collapses obviously-duplicate search
candidates before the expensive detail scrape, so the same book is not fetched once per site. It is
pure (no I/O, no logging) - the caller in :mod:`bookscraper.commands.search` owns the logging of each
dropped candidate.

* :func:`normalize_title` / :func:`normalize_author` lower-case, strip accents/punctuation, and drop
  edition markers and subtitles, turning a decorated title into a stable comparison key.
* :func:`dedup_key` is the ``normalize_title(title) + "|" + normalize_author(first_author)`` join.
* :func:`group_candidates` groups candidates by :func:`dedup_key` and keeps one per key using the
  publisher-over-reseller precedence :data:`DEDUP_SITE_PRIORITY`.

There is deliberately **no** fuzzy / Levenshtein matching here: a false merge discards a distinct book
forever, so this subtask stays conservative and exact-after-normalization. Fuzzy matching is recorded
as a possible follow-up.
"""

from __future__ import annotations

import re
import unicodedata

from .search_providers import SearchCandidate

#: Site precedence for keeping one candidate per :func:`dedup_key` group: a publisher site (richer,
#: first-party metadata) beats a reseller. A site not listed here ranks last. The packtpub / oreilly
#: entries never match a live candidate today (those providers were descoped in 03.3 subtask 03), but
#: the full list is kept so the ordering is correct the day the registry grows.
DEDUP_SITE_PRIORITY = ["oreilly", "packtpub", "leanpub", "amazon"]

#: Spelled-out ordinals an edition marker may use (``"second edition"``), alongside the numeric
#: ``"2nd"`` form handled by the regex below.
_ORDINAL_WORDS = (
    "first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth"
)

#: Matches a trailing-or-inline edition marker such as ``"2nd Edition"``, ``"Second Edition"``,
#: ``"Revised Edition"``, or ``"3rd ed."``. Stripped by :func:`normalize_title` so two sites that
#: label the same book with different edition decorations collapse to one key.
_EDITION_RE = re.compile(
    rf"\b(?:\d{{1,3}}(?:st|nd|rd|th)|{_ORDINAL_WORDS}|revised|updated|expanded)\s+ed(?:ition)?\.?\b"
)

#: Matches a parenthetical aside (``"(C++ In-Depth Series)"``) dropped by :func:`normalize_title`.
_PAREN_RE = re.compile(r"\([^)]*\)")

#: Matches any run of characters that is not a lowercase letter or digit; collapsed to a single space
#: by both normalizers so ``"C++"`` and ``"C#"`` both reduce to ``"c"`` and punctuation never splits a
#: key.
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")


def _strip_accents(text: str) -> str:
    """Remove combining diacritical marks so ``"Josée"`` compares equal to ``"Josee"``.

    :param text: The already-lower-cased text to fold.
    :returns: ``text`` with Unicode combining marks removed (NFKD decomposition).
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_title(title: str) -> str:
    """Reduce a site-decorated title to a stable, comparable key.

    Lower-cases, drops any subtitle after the first ``:``, removes ``(...)`` parentheticals and
    edition markers (``"2nd Edition"``, ``"Revised Edition"``), folds accents, and collapses every
    other punctuation run to a single space. A book whose *main* title legitimately contains a ``:``
    loses everything after it - an accepted limitation, since dropping subtitles is what lets a
    publisher's bare title match a reseller's subtitled one.

    :param title: The raw search-result title (may be empty).
    :returns: The normalized title key (``""`` for an empty input).
    """
    if not title:
        return ""
    text = title.lower()
    text = text.split(":", 1)[0]
    text = _strip_accents(text)
    text = _PAREN_RE.sub(" ", text)
    text = _EDITION_RE.sub(" ", text)
    text = _NON_ALNUM_RE.sub(" ", text)
    return " ".join(text.split())


def normalize_author(author: str) -> str:
    """Reduce an author name to a stable, comparable key.

    Lower-cases, folds accents, and collapses punctuation (so ``"Arthur O'Dwyer"`` and
    ``"Arthur O Dwyer"`` match). Word order is preserved; no initials or honorifics are inferred.

    :param author: The raw author name (may be empty).
    :returns: The normalized author key (``""`` for an empty input).
    """
    if not author:
        return ""
    text = _strip_accents(author.lower())
    text = _NON_ALNUM_RE.sub(" ", text)
    return " ".join(text.split())


def dedup_key(title: str, first_author: str) -> str:
    """Build the within-run cross-site dedup key for a candidate.

    The key is ``normalize_title(title) + "|" + normalize_author(first_author)``; it intentionally
    carries no publication year, so two editions of the same book share a key (the canonical ``hash``,
    which does include the year, is what keeps distinct editions apart at save time).

    :param title: The candidate's title.
    :param first_author: The candidate's first author (``""`` when it has none).
    :returns: The dedup key, two normalized halves joined by ``"|"``.
    """
    return f"{normalize_title(title)}|{normalize_author(first_author)}"


def _priority_rank(site: str) -> int:
    """Return the :data:`DEDUP_SITE_PRIORITY` rank of ``site`` (lower wins; unknown sites rank last).

    :param site: The candidate's site key.
    :returns: The index of ``site`` in :data:`DEDUP_SITE_PRIORITY`, or ``len(DEDUP_SITE_PRIORITY)``
        when it is not listed.
    """
    if site in DEDUP_SITE_PRIORITY:
        return DEDUP_SITE_PRIORITY.index(site)
    return len(DEDUP_SITE_PRIORITY)


def _candidate_key(candidate: SearchCandidate) -> str:
    """Compute :func:`dedup_key` for ``candidate`` using its title and first author.

    :param candidate: The candidate to key.
    :returns: The candidate's dedup key.
    """
    first_author = candidate.authors[0] if candidate.authors else ""
    return dedup_key(candidate.title, first_author)


def group_candidates(
    candidates: list[SearchCandidate],
) -> tuple[list[SearchCandidate], list[tuple[SearchCandidate, SearchCandidate]]]:
    """Group ``candidates`` by :func:`dedup_key` and keep one per key by :data:`DEDUP_SITE_PRIORITY`.

    Within each group the highest-priority site wins (a publisher over a reseller); ties - including
    two candidates from the same site - keep the first-seen one. The kept list preserves the
    first-seen order of the keys, so the pipeline's downstream ordering is stable.

    This function is pure: it neither logs nor mutates its inputs. The caller logs each dropped
    candidate (at info level, naming the kept one's URL) from the returned pairs.

    :param candidates: The post-filter survivors across every site and query of the run.
    :returns: A ``(kept, dropped)`` tuple - ``kept`` is one candidate per key; ``dropped`` pairs each
        discarded candidate with the kept candidate that superseded it (``(dropped, kept)``).
    """
    groups: dict[str, list[SearchCandidate]] = {}
    order: list[str] = []
    for candidate in candidates:
        key = _candidate_key(candidate)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(candidate)

    kept: list[SearchCandidate] = []
    dropped: list[tuple[SearchCandidate, SearchCandidate]] = []
    for key in order:
        members = groups[key]
        # min() is stable: on a priority tie it returns the first-seen member, so same-site
        # duplicates and equal-priority sites keep the earliest candidate.
        winner = min(members, key=lambda candidate: _priority_rank(candidate.site))
        kept.append(winner)
        dropped.extend((member, winner) for member in members if member is not winner)
    return kept, dropped
