"""Unit tests for bookscraper.scraping.dedup.

The normalizers and :func:`dedup_key` are pure string functions, and :func:`group_candidates` is a
pure grouping over :class:`SearchCandidate` records - no network, no disk. These tests pin the
title/author normalization rules, the cross-site key equality on real title pairs, the documented
edition limitation, and the publisher-over-reseller precedence.

No true cross-site pair (the same book captured on two different sites) exists in the 06.0 fixture
corpus - each captured title is unique to one site - so the pairs below are built from the real
single-site fixture titles decorated the way a second site realistically would (a subtitle after
``:``, an edition suffix). The base titles and authors are taken verbatim from
``tests/fixtures/**/*.expected.json``.
"""

from bookscraper.scraping.dedup import (
    DEDUP_SITE_PRIORITY,
    dedup_key,
    group_candidates,
    normalize_author,
    normalize_title,
)
from bookscraper.scraping.search_providers import SearchCandidate

#: Real cross-site title/author pairs for ``test_dedup_key_equal_for_cross_site_pair``. Each tuple is
#: ``(site_a_title, site_b_title, first_author)`` where both titles are the SAME book as one site
#: (publisher, bare title) versus another (reseller, subtitle/edition decoration). Base titles/authors
#: come from the 06.0 fixtures (amazon/packtpub/oreilly/leanpub ``*.expected.json``).
CROSS_SITE_PAIRS = [
    # oreilly bare vs amazon subtitled - effective_modern_cpp.expected.json
    (
        "Effective Modern C++",
        "Effective Modern C++: 42 Specific Ways to Improve Your Use of C++11 and C++14",
        "Scott Meyers",
    ),
    # amazon bare vs publisher edition suffix - cpp_primer_5e.expected.json
    ("C++ Primer", "C++ Primer, 5th Edition", "Stanley B. Lippman"),
    # packtpub bare vs amazon subtitled - software_architecture_with_cpp.expected.json
    (
        "Software Architecture with C++",
        "Software Architecture with C++: Design modern systems using effective architecture concepts",
        "Adrian Ostrowski",
    ),
    # packtpub bare vs amazon subtitled, apostrophe author - mastering_the_cpp17_stl.expected.json
    (
        "Mastering the C++17 STL",
        "Mastering the C++17 STL: Make full use of the standard library",
        "Arthur O'Dwyer",
    ),
    # leanpub bare vs amazon subtitled - cpp_best_practices.expected.json
    (
        "C++ Best Practices",
        "C++ Best Practices: Guidelines, Rules, and Best Practices to Build Software with Modern C++",
        "Jason Turner",
    ),
    # amazon bare vs publisher edition suffix - the_cpp_programming_language_4e.expected.json
    ("The C++ Programming Language", "The C++ Programming Language, 4th Edition", "Bjarne Stroustrup"),
]


def _candidate(*, site: str, title: str = "A Book", authors=None) -> SearchCandidate:
    """Build a minimal SearchCandidate for the grouping tests.

    :param site: The candidate's site key (drives :data:`DEDUP_SITE_PRIORITY` precedence).
    :param title: The candidate's title.
    :param authors: The candidate's authors list, or ``None`` for an empty list.
    :returns: The populated :class:`~bookscraper.scraping.search_providers.SearchCandidate`.
    """
    return SearchCandidate(
        site=site,
        title=title,
        url=f"https://{site}.example.com/{title.replace(' ', '-')}",
        detail_ref=f"https://{site}.example.com/{title.replace(' ', '-')}",
        authors=list(authors or []),
    )


def test_normalize_title_strips_edition_and_subtitle():
    """
    [Unit] dedup: verifies normalize_title drops the subtitle after ``:`` and any edition marker.

    Scenario:
        Given a decorated title carrying both an edition suffix and a ``:`` subtitle
        When normalize_title is applied
        Then the result equals the normalized bare title, and parenthetical/accent/punctuation noise
        is folded away too.

    Boundaries:
        - Title normalization only; author handling is a separate test.

    On failure, first check:
        - The subtitle split, _EDITION_RE, _PAREN_RE and _NON_ALNUM_RE steps in normalize_title.
    """
    bare = normalize_title("C++ Primer")
    assert normalize_title("C++ Primer, 5th Edition: A Guided Tour") == bare
    assert normalize_title("C++ Primer (Developer's Library)") == bare
    assert normalize_title("Effective Modern C++: 42 Specific Ways") == normalize_title("Effective Modern C++")
    assert bare == "c primer"
    assert normalize_title("") == ""


def test_normalize_author_folds_case_accents_and_punctuation():
    """
    [Unit] dedup: verifies normalize_author lower-cases, folds accents, and collapses punctuation.

    Scenario:
        Given author names differing only in case, accents, or an apostrophe/period
        When normalize_author is applied
        Then they reduce to the same key, and an empty input yields "".

    Boundaries:
        - Author normalization only; word order is preserved, no initials are inferred.

    On failure, first check:
        - The _strip_accents call and _NON_ALNUM_RE substitution in normalize_author.
    """
    assert normalize_author("Arthur O'Dwyer") == "arthur o dwyer"
    assert normalize_author("Josée Lajoie") == normalize_author("Josee Lajoie")
    assert normalize_author("SCOTT MEYERS") == "scott meyers"
    assert normalize_author("") == ""


def test_dedup_key_equal_for_cross_site_pair():
    """
    [Unit] dedup: verifies the dedup key is identical for the same book decorated by two sites.

    Scenario:
        Given real fixture titles presented bare on one site and subtitled/edition-suffixed on another
        When dedup_key is computed for each with the shared first author
        Then the two keys are equal (so the within-run grouping collapses them to one candidate).

    Boundaries:
        - Six real pairs built from the 06.0 fixtures; exact-after-normalization, no fuzzy matching.

    On failure, first check:
        - normalize_title's subtitle/edition stripping and the dedup_key join format.
    """
    for title_a, title_b, author in CROSS_SITE_PAIRS:
        assert dedup_key(title_a, author) == dedup_key(title_b, author), (title_a, title_b)


def test_dedup_key_differs_for_different_editions_when_year_differs():
    """
    [Unit] dedup: documents that the dedup key does NOT separate editions - the stated limitation.

    Scenario:
        Given two editions of one book (4th vs 5th) that would carry different publication years
        When dedup_key is computed for each
        Then the keys are EQUAL, because the key deliberately strips edition markers and carries no
        year. Distinct editions are kept apart only by the canonical hash (title + authors + year) at
        save time and by the ISBN-13 store check, never by this title-only key.

    Boundaries:
        - Documents the accepted limitation; a false merge of two editions within a single run keeps
          the higher-priority site's edition, which is why the store-level ISBN-13 check is the real
          cross-edition guard.

    On failure, first check:
        - Whether normalize_title started retaining edition markers (it must not), or dedup_key grew a
          year component (it must not).
    """
    fourth = dedup_key("The C++ Programming Language, 4th Edition", "Bjarne Stroustrup")
    third = dedup_key("The C++ Programming Language, 3rd Edition", "Bjarne Stroustrup")
    assert fourth == third


def test_candidate_priority_keeps_publisher_over_reseller():
    """
    [Unit] dedup: verifies group_candidates keeps the publisher candidate over the reseller.

    Scenario:
        Given an O'Reilly (publisher) and an Amazon (reseller) candidate for the same book
        When group_candidates groups them
        Then exactly the O'Reilly candidate is kept and the Amazon one is reported dropped, paired with
        the kept O'Reilly candidate - regardless of input order.

    Boundaries:
        - One dedup group; DEDUP_SITE_PRIORITY precedence (oreilly before amazon).

    On failure, first check:
        - _priority_rank and the min()-by-rank selection in group_candidates.
    """
    oreilly = _candidate(site="oreilly", title="Effective Modern C++", authors=["Scott Meyers"])
    amazon = _candidate(
        site="amazon",
        title="Effective Modern C++: 42 Specific Ways",
        authors=["Scott Meyers"],
    )

    kept, dropped = group_candidates([amazon, oreilly])
    assert kept == [oreilly]
    assert dropped == [(amazon, oreilly)]

    # Order-independent: the publisher still wins when it is seen first.
    kept_reordered, dropped_reordered = group_candidates([oreilly, amazon])
    assert kept_reordered == [oreilly]
    assert dropped_reordered == [(amazon, oreilly)]


def test_group_candidates_keeps_distinct_books_and_breaks_ties_by_first_seen():
    """
    [Unit] dedup: verifies distinct books are all kept and same-site ties keep the first-seen one.

    Scenario:
        Given two candidates with different dedup keys plus a same-site duplicate of the first
        When group_candidates groups them
        Then both distinct books survive in first-seen order and the same-site duplicate is dropped in
        favor of the first occurrence.

    Boundaries:
        - Covers the no-collision path and the equal-priority (same-site) tie-break; min() is stable.

    On failure, first check:
        - The first-seen ``order`` bookkeeping and the stable min() tie-break in group_candidates.
    """
    first = _candidate(site="leanpub", title="C++17 in Detail", authors=["Bartlomiej Filipek"])
    second = _candidate(site="leanpub", title="C++ Lambda Story", authors=["Bartlomiej Filipek"])
    first_dup = _candidate(site="leanpub", title="C++17 in Detail", authors=["Bartlomiej Filipek"])

    kept, dropped = group_candidates([first, second, first_dup])
    assert kept == [first, second]
    assert dropped == [(first_dup, first)]


def test_dedup_site_priority_order():
    """
    [Unit] dedup: verifies DEDUP_SITE_PRIORITY is exactly the publisher-first order the spec mandates.

    Scenario:
        Given the module-level DEDUP_SITE_PRIORITY list
        When its order is inspected
        Then it is ["oreilly", "packtpub", "leanpub", "amazon"] - publishers before the reseller.

    Boundaries:
        - A plain data-sanity pin; packtpub/oreilly never match a live candidate today but the full
          list is kept for when the registry grows.

    On failure, first check:
        - The DEDUP_SITE_PRIORITY literal in scraping/dedup.py.
    """
    assert DEDUP_SITE_PRIORITY == ["oreilly", "packtpub", "leanpub", "amazon"]
