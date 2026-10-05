"""Unit tests for bookscraper.scraping.text (pure HTML-to-text conversion).

No mocks, no I/O: the module is a single pure function, so these tests feed it strings and
assert on the returned plain text. They pin the behaviour the Leanpub detail path (and, from
03.1, the Playwright description path) relies on - a ``None`` fragment, block-tag line breaks,
and entity unescaping ordered after tag stripping.
"""

from bookscraper.scraping.text import html_to_text


def test_html_to_text_none_is_empty():
    """
    [Unit] text: verifies a None fragment converts to an empty string.

    Scenario:
        Given html_to_text is called with None (the Leanpub about_the_book absent/null case)
        When the function returns
        Then the result is exactly "" (never a TypeError, the old unescape(None) crash)

    Boundaries:
        - Only the None short-circuit is exercised; non-None conversion is covered separately
        - Guards the fix that makes a missing about_the_book non-fatal

    On failure, first check:
        - The ``if html is None`` guard at the top of html_to_text was removed or reordered
    """
    assert html_to_text(None) == ""


def test_html_to_text_paragraphs_and_lists():
    """
    [Unit] text: verifies block tags become line breaks and remaining tags are stripped.

    Scenario:
        Given a fragment with <p>, <br>, <ul>/<li> markup and surrounding tags
        When html_to_text converts it
        Then <br> yields a newline, </p> a blank line, </li> a newline, every other tag is
        removed, no angle brackets remain, and runs of 3+ newlines collapse to a blank line

    Boundaries:
        - Covers the block-tag-to-whitespace mapping and tag stripping; entity unescaping is
          a separate test
        - Asserts the collapse rule (never 3+ consecutive newlines)

    On failure, first check:
        - The _BR_RE / _CLOSE_P_RE / _CLOSE_LI_RE / _TAG_RE substitutions or the newline-collapse
    """
    html = "<p>Hello<br>world</p><ul><li>Item one</li><li>Item two</li></ul>"
    result = html_to_text(html)

    assert "<" not in result and ">" not in result
    assert "\n\n\n" not in result
    assert result == "Hello\nworld\n\nItem one\nItem two"


def test_html_to_text_unescapes_after_strip():
    """
    [Unit] text: verifies entities are unescaped only after tags are stripped.

    Scenario:
        Given a fragment whose text contains an escaped tag entity (``&lt;script&gt;``) and a
        plain entity (``&amp;``) alongside real markup
        When html_to_text converts it
        Then &amp; becomes "&" and the escaped &lt;script&gt; survives as literal text rather
        than being re-interpreted and stripped as a real <script> tag

    Boundaries:
        - Pins the unescape-after-strip ordering (the security-relevant rule); the plain
          block-tag mapping is a separate test

    On failure, first check:
        - html_to_text unescapes before stripping tags, letting an entity-encoded tag be
          removed as though it were real markup
    """
    html = "<p>Tom &amp; Jerry use &lt;script&gt; tags</p>"
    result = html_to_text(html)

    assert result == "Tom & Jerry use <script> tags"
