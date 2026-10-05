"""Unit tests for bookscraper.isbn (subtask 03.1/02).

These pin the ISBN cleaning / validation / canonicalization contract that the per-site
normalizers (subtask 04) and cross-site deduplication (task 03.3) depend on: raw scraped text
becomes a checksum-valid canonical pair or ``None`` - never an exception - and a lone valid
ISBN-10 always yields its derived ISBN-13. Every ISBN literal below is a real, checksum-valid
value (verified by the validators under test or computed in-test); no invalid value is ever
passed off as "valid".
"""

from __future__ import annotations

import random

import pytest

from bookscraper import isbn


def _make_valid_isbn10(nine_digits: str) -> str:
    """Append the correct ISBN-10 check digit to nine leading digits.

    :param nine_digits: Exactly nine decimal digits forming the ISBN-10 body.
    :returns: A ten-character checksum-valid ISBN-10 (check digit ``X`` when the remainder is ten).
    """
    total = sum(int(ch) * (10 - i) for i, ch in enumerate(nine_digits))
    check = (11 - total % 11) % 11
    return nine_digits + ("X" if check == 10 else str(check))


def test_clean_strips_hyphens_spaces_and_labels():
    """
    [Unit] isbn.clean: removes separators and label text, uppercases x, and maps None to "".

    Scenario:
        Given hyphenated, space-and-letter-labelled, and lowercase-x raw ISBN text (and None)
        When clean() is applied
        Then every non-[0-9Xx] character is removed, x is uppercased, and None yields "".

    Boundaries:
        - Pure string transform; no checksum logic is exercised here
        - clean keeps every digit (stripping a numeric label prefix is extract_first's job)
        - None is a first-class input, not an error

    On failure, first check:
        - Whether the strip regex or the uppercase step changed, or the None guard was dropped
    """
    assert isbn.clean("978-1-09-813102-9") == "9781098131029"
    assert isbn.clean("ISBN: 0-8044-2957-x") == "080442957X"
    assert isbn.clean(None) == ""


def test_valid_isbn10_with_x_check_digit():
    """
    [Unit] isbn.is_valid_isbn10: accepts a well-formed ISBN-10 whose check digit is X.

    Scenario:
        Given the checksum-valid ISBN-10 "080442957X"
        When is_valid_isbn10() is called
        Then it returns True.

    Boundaries:
        - X is only legal in the final position and is weighted as ten
        - Exercises the mod-11 weighted checksum on the happy path

    On failure, first check:
        - Whether the X-handling branch or the weight sequence (10..1) changed
    """
    assert isbn.is_valid_isbn10("080442957X") is True


def test_invalid_isbn10_checksum_rejected():
    """
    [Unit] isbn.is_valid_isbn10: rejects a ten-char string whose checksum does not divide by 11.

    Scenario:
        Given "0804429570" (the valid "080442957X" with a wrong final digit)
        When is_valid_isbn10() is called
        Then it returns False.

    Boundaries:
        - Length and character rules pass; only the checksum fails
        - Isolates the mod-11 remainder check

    On failure, first check:
        - Whether the checksum accumulator or the final modulo comparison regressed
    """
    assert isbn.is_valid_isbn10("0804429570") is False


def test_isbn10_rejects_misplaced_x_and_foreign_chars():
    """
    [Unit] isbn.is_valid_isbn10: rejects wrong length, a non-final X, and a non-ISBN character.

    Scenario:
        Given a nine-char string, a ten-char string with X in the middle, and one ending in a letter
        When is_valid_isbn10() is called on each
        Then every call returns False.

    Boundaries:
        - Covers the length guard, the "X only last" guard, and the non-digit/non-X guard
        - These are the structural rejections that precede the checksum

    On failure, first check:
        - Whether the length check, the X-position check, or the else-reject branch changed
    """
    assert isbn.is_valid_isbn10("123456789") is False
    assert isbn.is_valid_isbn10("12345X7890") is False
    assert isbn.is_valid_isbn10("123456789A") is False


def test_valid_isbn13():
    """
    [Unit] isbn.is_valid_isbn13: accepts a well-formed 978-prefixed ISBN-13.

    Scenario:
        Given the checksum-valid ISBN-13 "9781098131029"
        When is_valid_isbn13() is called
        Then it returns True.

    Boundaries:
        - Exercises the alternating 1/3 weighted mod-10 checksum on the happy path
        - Prefix and length rules pass

    On failure, first check:
        - Whether the alternating weight pattern or the mod-10 comparison changed
    """
    assert isbn.is_valid_isbn13("9781098131029") is True


def test_isbn13_with_bad_prefix_rejected():
    """
    [Unit] isbn.is_valid_isbn13: rejects a thirteen-digit value whose prefix is not 978/979.

    Scenario:
        Given "9770000000003" (thirteen digits, checksum-valid, but a "977" prefix)
        When is_valid_isbn13() is called
        Then it returns False, isolating the prefix rule from length and checksum.

    Boundaries:
        - The value is deliberately checksum-valid so only the prefix check can reject it
        - 978 and 979 are the sole Bookland prefixes

    On failure, first check:
        - Whether the accepted-prefix set changed in is_valid_isbn13
    """
    assert isbn.is_valid_isbn13("9770000000003") is False


def test_isbn13_rejects_wrong_length_and_non_digits():
    """
    [Unit] isbn.is_valid_isbn13: rejects a too-short value, a non-digit value, and a bad checksum.

    Scenario:
        Given a twelve-char value, a thirteen-char value containing X, and a 978 value with a wrong digit
        When is_valid_isbn13() is called on each
        Then every call returns False.

    Boundaries:
        - Covers the length guard, the all-digits guard, and the checksum rejection
        - X is never legal in an ISBN-13

    On failure, first check:
        - Whether the length/isdigit guard or the mod-10 checksum regressed
    """
    assert isbn.is_valid_isbn13("978109813102") is False
    assert isbn.is_valid_isbn13("978X09813102X") is False
    assert isbn.is_valid_isbn13("9781098131028") is False


def test_isbn10_to_isbn13_conversion():
    """
    [Unit] isbn.isbn10_to_isbn13: converts a valid ISBN-10 to its canonical ISBN-13.

    Scenario:
        Given the valid ISBN-10 "0136816487"
        When isbn10_to_isbn13() is called
        Then it returns "9780136816485", which is itself checksum-valid.

    Boundaries:
        - The ISBN-10 check digit is discarded and a fresh ISBN-13 check digit computed
        - The 978 prefix is always used for conversion

    On failure, first check:
        - Whether the prefix, the nine-digit slice, or the new check-digit formula changed
    """
    converted = isbn.isbn10_to_isbn13("0136816487")
    assert converted == "9780136816485"
    assert isbn.is_valid_isbn13(converted) is True


def test_isbn10_to_isbn13_rejects_invalid_input():
    """
    [Unit] isbn.isbn10_to_isbn13: raises ValueError when its ISBN-10 precondition is broken.

    Scenario:
        Given an invalid ISBN-10 ("0804429570")
        When isbn10_to_isbn13() is called
        Then it raises ValueError rather than returning a bogus ISBN-13.

    Boundaries:
        - This internal conversion fails loudly, unlike the public normalize/extract entry points
        - Guards callers from silently building an ISBN-13 off unvalidated input

    On failure, first check:
        - Whether the leading is_valid_isbn10 guard was removed
    """

    with pytest.raises(ValueError, match="valid ISBN-10"):
        isbn.isbn10_to_isbn13("0804429570")


def test_normalize_derives_13_from_valid_10():
    """
    [Unit] isbn.normalize: derives the ISBN-13 when only a valid ISBN-10 is supplied.

    Scenario:
        Given raw10="080442957X" and raw13=None
        When normalize() is called
        Then it returns the cleaned ISBN-10 and the derived, checksum-valid ISBN-13.

    Boundaries:
        - Covers the derivation branch (isbn13 missing, isbn10 present)
        - The derived ISBN-13 equals isbn10_to_isbn13 of the ISBN-10

    On failure, first check:
        - Whether the "derive when 13 absent" condition in normalize changed
    """
    assert isbn.normalize("080442957X", None) == ("080442957X", "9780804429573")


def test_normalize_keeps_both_and_skips_derivation_when_13_present():
    """
    [Unit] isbn.normalize: returns both ISBNs verbatim and skips derivation when a valid 13 is given.

    Scenario:
        Given a valid ISBN-10 and a valid, unrelated ISBN-13
        When normalize() is called
        Then both cleaned values are returned and the ISBN-13 is not re-derived from the ISBN-10;
            and when only a valid ISBN-13 is given (raw10=None), the ISBN-10 is None.

    Boundaries:
        - Covers the "13 already present" path and the "raw10 falsy, no log" path
        - normalize never raises on a None input

    On failure, first check:
        - Whether normalize started overwriting a supplied ISBN-13 or mishandled a None ISBN-10
    """
    assert isbn.normalize("0136816487", "9781098131029") == ("0136816487", "9781098131029")
    assert isbn.normalize(None, "9781098131029") == (None, "9781098131029")


def test_normalize_na_sentinel_becomes_none():
    """
    [Unit] isbn.normalize: maps the "N/A" sentinel (and other garbage) to a (None, None) pair.

    Scenario:
        Given raw10="N/A" and raw13="N/A"
        When normalize() is called
        Then it returns (None, None) without raising.

    Boundaries:
        - "N/A" cleans to "" and fails both validators; the rejection is logged at debug level
        - Exercises both reject-and-log paths of the internal resolver

    On failure, first check:
        - Whether clean() stopped stripping the sentinel or a validator accepted an empty string
    """
    assert isbn.normalize("N/A", "N/A") == (None, None)


def test_extract_first_from_labelled_text():
    """
    [Unit] isbn.extract_first: pulls the ISBN out of label-prefixed free text (13 then 10).

    Scenario:
        Given "ISBN-13 : 978-1-09-813102-9" and "ISBN-10: 0-8044-2957-X" and text with no ISBN
        When extract_first() is called
        Then it returns the embedded ISBN-13, the embedded ISBN-10, and None respectively.

    Boundaries:
        - The leading label digits ("13", "10") must not be mistaken for the ISBN
        - Covers the 13-match branch, the 10-match branch, and the no-match-returns-None branch

    On failure, first check:
        - Whether the candidate regex or the per-candidate length/validity checks changed
    """
    assert isbn.extract_first("ISBN-13 : 978-1-09-813102-9") == "9781098131029"
    assert isbn.extract_first("ISBN-10: 0-8044-2957-X") == "080442957X"
    assert isbn.extract_first("no isbn anywhere in here") is None


def test_extract_first_skips_invalid_candidate():
    """
    [Unit] isbn.extract_first: returns None when a numeric run is ISBN-shaped but checksum-invalid.

    Scenario:
        Given text embedding "1234567890" (ten digits, invalid checksum)
        When extract_first() is called
        Then it returns None, having matched a candidate but rejected it.

    Boundaries:
        - Covers the "candidate matched, both validity checks fail, loop continues" path
        - Distinguishes a bad candidate from no candidate at all

    On failure, first check:
        - Whether extract_first began returning an unvalidated candidate
    """
    assert isbn.extract_first("page ref 1234567890 end") is None


def test_every_converted_isbn13_is_valid():
    """
    [Unit] isbn.isbn10_to_isbn13: 200 random valid ISBN-10s all convert to valid ISBN-13s.

    Scenario:
        Given 200 checksum-valid ISBN-10s built from a fixed-seed stdlib random stream
        When each is converted via isbn10_to_isbn13()
        Then every result passes is_valid_isbn13().

    Boundaries:
        - Property-style coverage over the generator; no new dependency (stdlib random, seed fixed)
        - Each ISBN-10 is checksum-valid by construction, so the conversion precondition holds

    On failure, first check:
        - Whether the ISBN-13 check-digit formula or the ISBN-10 generation helper drifted
    """
    rng = random.Random(20261006)
    for _ in range(200):
        isbn10 = _make_valid_isbn10("".join(str(rng.randint(0, 9)) for _ in range(9)))
        assert isbn.is_valid_isbn10(isbn10) is True
        assert isbn.is_valid_isbn13(isbn.isbn10_to_isbn13(isbn10)) is True
