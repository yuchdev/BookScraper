"""Pure HTML/JSON sanitizers for the captured-fixture corpus (task 06.0).

These functions are the security boundary between untrusted, possibly hostile
third-party markup captured from Amazon/Packtpub/Leanpub/O'Reilly and the bytes
that get committed - forever - into a PUBLIC git repository. They are pure (string
-> string / obj -> obj), so ``scripts/capture_fixtures.py`` can compose them and a
unit test can pin every branch without a browser or network.

Design notes:

* No third-party HTML parser is a dependency of this project, and adding one is out
  of scope, so parsing is done with the standard library ``html.parser.HTMLParser``
  into a tiny bounded DOM (``_Element`` / ``_Text``) that is serialized back out.
* Parsing depth is bounded (:data:`MAX_TREE_DEPTH`) so a deeply-nested hostile page
  cannot exhaust the stack (threat-model FIX-11 / CWE-400, CWE-409).
* ``assert_clean`` is the fail-closed final gate (FIX-2): it reuses the vendor
  credential shapes from ``.claude/skills/secret-scan/references/pattern-catalog.md``
  (copied here because that hook is not importable from this package) and extends
  them with corpus-specific detectors (cookies, session/CSRF tokens, JWTs, signed-URL
  params, emails) that the credential-only catalog misses.

Import direction: this module lives under ``scraping/`` and imports only the standard
library - it pulls in nothing from ``backends/``, ``commands/`` or even ``book_utils``.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Bumped whenever the sanitizer rules change; recorded in every fixture's .meta.json
# (FIX-14) so a committed fixture carries an audit trail of how it was cleaned.
SANITIZER_VERSION = "1.0.0"

# Neutral text that replaces long prose (descriptions, reviews, blurbs, bios). It
# deliberately contains the word "test" so the secret-scan placeholder allowlist
# treats any line carrying it as an obvious non-secret.
PROSE_PLACEHOLDER = "Neutralized prose omitted from the public test corpus."

# Replacement for a redacted JSON value. Contains "redacted", so it is allowlisted.
REDACTED_VALUE = "[REDACTED]"

# Replacement for any email address found in markup. Uses the reserved .invalid TLD.
EMAIL_PLACEHOLDER = "user@example.invalid"

# A text node (collapsed) longer than this is treated as prose to be neutralized, and
# a JSON string under a prose-named key longer than this is replaced too.
PROSE_MAX_CHARS = 200

# Text shorter than this is never neutralized, even inside a prose-named container:
# short headings/labels/field values (e.g. an O'Reilly "Book description" heading a
# ``:has-text()`` selector keys off) must stay verbatim; only the long body collapses.
PROSE_MIN_CHARS = 80

# A ``data-*`` value longer than this is an opaque blob and is dropped regardless of
# its name (in addition to the sensitive-name rule below).
DATA_ATTR_MAX_CHARS = 200

# A subtree whose serialized length exceeds this and that contains none of the
# keep-selectors is dropped by ``shrink``.
SHRINK_MIN_SUBTREE_CHARS = 500

# Recursion / nesting caps for hostile input (FIX-11).
MAX_TREE_DEPTH = 200
MAX_JSON_DEPTH = 100


class SanitizationError(Exception):
    """Raised when sanitization fails or ``assert_clean`` still finds a secret.

    A capture that raises this must abort with no file written (fail-closed).
    """


# --------------------------------------------------------------------------- #
# Tiny bounded DOM
# --------------------------------------------------------------------------- #
_VOID_ELEMENTS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)
# Elements whose text content must be emitted verbatim (not HTML-escaped) so JSON/CSS
# inside them is not corrupted on re-serialization.
_RAWTEXT_ELEMENTS = frozenset({"script", "style"})

_DOCUMENT = "#document"


class _Text:
    """A text node. ``data`` is already entity-decoded by the parser."""

    __slots__ = ("data", "parent")

    def __init__(self, data: str) -> None:
        self.data = data
        self.parent: Optional[_Element] = None


class _Element:
    """An element node with ordered ``[name, value]`` attribute pairs and children."""

    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: list[list[Optional[str]]]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[Any] = []
        self.parent: Optional[_Element] = None


class _TreeBuilder(HTMLParser):
    """Builds a bounded ``_Element`` tree from an HTML string."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element(_DOCUMENT, [])
        self.doctype: Optional[str] = None
        self._stack: list[_Element] = [self.root]

    def _append(self, node: Any) -> None:
        parent = self._stack[-1]
        node.parent = parent
        parent.children.append(node)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        el = _Element(tag, [[name, value] for name, value in attrs])
        self._append(el)
        if tag not in _VOID_ELEMENTS and len(self._stack) < MAX_TREE_DEPTH:
            self._stack.append(el)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self._append(_Element(tag, [[name, value] for name, value in attrs]))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self._append(_Text(data))

    def handle_decl(self, decl: str) -> None:
        self.doctype = f"<!{decl}>"


def _parse(html: str) -> _Element:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    if builder.doctype is not None:
        # Stash the doctype on the root so the serializer can re-emit it.
        builder.root.attrs.append(["__doctype__", builder.doctype])
    return builder.root


def _escape_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _emit(node: Any, parts: list[str]) -> None:
    if isinstance(node, _Text):
        parts.append(_escape_text(node.data))
        return
    if node.tag == _DOCUMENT:
        for child in node.children:
            _emit(child, parts)
        return
    parts.append("<" + node.tag)
    for name, value in node.attrs:
        if value is None:
            parts.append(" " + name)
        else:
            parts.append(f' {name}="{_escape_attr(value)}"')
    parts.append(">")
    if node.tag in _VOID_ELEMENTS:
        return
    if node.tag in _RAWTEXT_ELEMENTS:
        for child in node.children:
            if isinstance(child, _Text):
                parts.append(child.data)
    else:
        for child in node.children:
            _emit(child, parts)
    parts.append("</" + node.tag + ">")


def _serialize(node: Any) -> str:
    parts: list[str] = []
    if isinstance(node, _Element) and node.tag == _DOCUMENT:
        doctype = _attr(node, "__doctype__")
        if doctype:
            parts.append(doctype)
    _emit(node, parts)
    return "".join(parts)


def _iter_elements(node: Any) -> Any:
    """Yield ``node`` (if a real element) and all descendant elements, in order."""
    if isinstance(node, _Element) and node.tag != _DOCUMENT:
        yield node
    for child in node.children:
        if isinstance(child, _Element):
            yield from _iter_elements(child)


def _filter_tree(el: _Element, should_remove: Any) -> None:
    """Drop whole subtrees for which ``should_remove(child)`` is True; recurse into the rest."""
    kept: list[Any] = []
    for child in el.children:
        if isinstance(child, _Element):
            if should_remove(child):
                continue
            _filter_tree(child, should_remove)
        kept.append(child)
    el.children = kept


def _attr(el: _Element, name: str, default: Optional[str] = None) -> Optional[str]:
    lowered = name.lower()
    for attr_name, value in el.attrs:
        if attr_name.lower() == lowered:
            return value if value is not None else default
    return default


def _set_attr(el: _Element, name: str, value: str) -> None:
    lowered = name.lower()
    for pair in el.attrs:
        if pair[0].lower() == lowered:
            pair[1] = value
            return
    el.attrs.append([name, value])


# --------------------------------------------------------------------------- #
# CSS selector matching (bounded subset)
# --------------------------------------------------------------------------- #
# Supports: comma groups; descendant (" "), child (">"), adjacent ("+") and general
# sibling ("~") combinators; compound selectors of tag / #id / .class / [attr] /
# [attr="value"] (presence and exact "=" only); "*"; and Playwright's :has-text("...")
# / :has-text('...') pseudo-class (case-insensitive, whitespace-normalized substring
# match against the element's text content). This is enough to recognise the elements
# the site_constants detail selectors extract.
#
# FAIL-CLOSED: any other pseudo-class, any pseudo-element ("::..."), any attribute
# operator other than "=", or otherwise unparseable syntax raises SanitizationError
# rather than being silently ignored - a silently-widened keep-selector would protect
# prose from neutralization and defeat the public-repo text policy.
_IDENT_CHARS = frozenset("-_")


class _Compound:
    __slots__ = ("tag", "id", "classes", "attrs", "has_text")

    def __init__(self) -> None:
        self.tag: Optional[str] = None
        self.id: Optional[str] = None
        self.classes: list[str] = []
        self.attrs: list[tuple[str, Optional[str]]] = []
        self.has_text: list[str] = []


def _read_ident(text: str, i: int) -> tuple[str, int]:
    start = i
    while i < len(text) and (text[i].isalnum() or text[i] in _IDENT_CHARS):
        i += 1
    return text[start:i], i


def _parse_attr(inner: str, compound: _Compound) -> None:
    if "=" not in inner:
        compound.attrs.append((inner.strip(), None))
        return
    name, _, raw = inner.partition("=")
    name = name.strip()
    if name and name[-1] in "~|^$*":
        raise SanitizationError(f"Unsupported attribute operator in selector: [{inner}]")
    compound.attrs.append((name, raw.strip().strip("\"'")))


def _parse_has_text(text: str, i: int, compound: _Compound) -> int:
    # text[i] == "(" immediately after ":has-text"
    end = text.find(")", i)
    if end == -1:
        raise SanitizationError(f"Unterminated :has-text() in selector: {text!r}")
    arg = text[i + 1 : end].strip()
    if len(arg) >= 2 and arg[0] in "\"'" and arg[-1] == arg[0]:
        arg = arg[1:-1]
    elif arg.startswith("/"):
        raise SanitizationError(f"Regex :has-text() is not supported: {text!r}")
    compound.has_text.append(" ".join(arg.split()))
    return end + 1


def _parse_pseudo(text: str, i: int, compound: _Compound) -> int:
    if text.startswith("::", i):
        raise SanitizationError(f"Unsupported pseudo-element in selector: {text!r}")
    name, j = _read_ident(text, i + 1)
    if name.lower() != "has-text" or j >= len(text) or text[j] != "(":
        raise SanitizationError(f"Unsupported pseudo-class ':{name}' in selector: {text!r}")
    return _parse_has_text(text, j, compound)


def _parse_compound(text: str) -> _Compound:
    compound = _Compound()
    i = 0
    while i < len(text):
        char = text[i]
        if char == "*":
            i += 1
        elif char == "#":
            compound.id, i = _read_ident(text, i + 1)
        elif char == ".":
            cls, i = _read_ident(text, i + 1)
            compound.classes.append(cls)
        elif char == "[":
            end = text.find("]", i)
            if end == -1:
                raise SanitizationError(f"Unterminated attribute selector: {text!r}")
            _parse_attr(text[i + 1 : end], compound)
            i = end + 1
        elif char == ":":
            i = _parse_pseudo(text, i, compound)
        elif char.isalnum():
            tag, i = _read_ident(text, i)
            compound.tag = tag.lower()
        else:
            raise SanitizationError(f"Unsupported selector syntax {char!r} in: {text!r}")
    return compound


def _split_group(group: str) -> list[tuple[str, _Compound]]:
    """Split one selector group into (combinator, compound) pairs, left to right.

    The first pair's combinator is always " " (descendant, unused for the leftmost).
    """
    tokens: list[tuple[str, str]] = []
    current = ""
    combinator = " "
    i = 0
    while i < len(group):
        char = group[i]
        if char in " \t\n>+~":
            if current:
                tokens.append((combinator, current))
                current = ""
                combinator = " "
            if char in ">+~":
                combinator = char
            i += 1
            continue
        if char == "[":
            end = group.find("]", i)
            end = len(group) - 1 if end == -1 else end
            current += group[i : end + 1]
            i = end + 1
            continue
        if char == "(":
            end = group.find(")", i)
            end = len(group) - 1 if end == -1 else end
            current += group[i : end + 1]
            i = end + 1
            continue
        current += char
        i += 1
    if current:
        tokens.append((combinator, current))
    return [(combinator, _parse_compound(text)) for combinator, text in tokens]


def _classes_of(el: _Element) -> set[str]:
    value = _attr(el, "class")
    return set(value.split()) if value else set()


def _local_name(tag: str) -> str:
    """Local name of a possibly-namespaced tag (``svg:script`` -> ``script``)."""
    return tag.rsplit(":", 1)[-1].lower()


def _text_content(el: _Element) -> str:
    """Whitespace-normalized concatenation of visible descendant text (Playwright-style).

    Script/style subtrees are excluded: their contents are code, not text content, and
    counting them would let a large ld+json blob look like prose (V-5).
    """
    parts: list[str] = []

    def collect(node: _Element) -> None:
        for child in node.children:
            if isinstance(child, _Text):
                parts.append(child.data)
            elif _local_name(child.tag) not in ("script", "style"):
                collect(child)

    collect(el)
    return " ".join("".join(parts).split())


def _matches_compound(el: _Element, compound: _Compound) -> bool:
    if compound.tag is not None and el.tag.lower() != compound.tag:
        return False
    if compound.id is not None and _attr(el, "id") != compound.id:
        return False
    if compound.classes:
        classes = _classes_of(el)
        if not all(cls in classes for cls in compound.classes):
            return False
    for name, value in compound.attrs:
        actual = _attr(el, name)
        if actual is None:
            return False
        if value is not None and actual != value:
            return False
    if compound.has_text:
        text = _text_content(el).lower()
        if not all(needle.lower() in text for needle in compound.has_text):
            return False
    return True


def _element_siblings(el: _Element) -> list[_Element]:
    if el.parent is None:
        return [el]
    return [child for child in el.parent.children if isinstance(child, _Element)]


def _matches_sequence(el: _Element, pairs: list[tuple[str, _Compound]]) -> bool:
    combinator, compound = pairs[-1]
    if not _matches_compound(el, compound):
        return False
    rest = pairs[:-1]
    if not rest:
        return True
    if combinator == ">":
        return el.parent is not None and _matches_sequence(el.parent, rest)
    if combinator in ("+", "~"):
        siblings = _element_siblings(el)
        index = siblings.index(el)
        candidates = [siblings[index - 1]] if combinator == "+" and index > 0 else siblings[:index]
        return any(_matches_sequence(sib, rest) for sib in candidates)
    # descendant: any ancestor matches the rest.
    ancestor = el.parent
    while ancestor is not None and ancestor.tag != _DOCUMENT:
        if _matches_sequence(ancestor, rest):
            return True
        ancestor = ancestor.parent
    return False


def _select(root: _Element, selector: str) -> list[_Element]:
    results: list[_Element] = []
    seen: set[int] = set()
    for group in selector.split(","):
        group = group.strip()
        if not group:
            continue
        pairs = _split_group(group)
        if not pairs:
            continue
        for el in _iter_elements(root):
            if id(el) not in seen and _matches_sequence(el, pairs):
                seen.add(id(el))
                results.append(el)
    return results


# --------------------------------------------------------------------------- #
# URL / tracking helpers (FIX-5)
# --------------------------------------------------------------------------- #
_TRACKER_HOSTS = frozenset(
    {
        "google-analytics.com",
        "googletagmanager.com",
        "doubleclick.net",
        "facebook.com",
        "facebook.net",
        "scorecardresearch.com",
        "adnxs.com",
        "amazon-adsystem.com",
        "fls-na.amazon.com",
        "fls-na.amazon-adsystem.com",
        "bat.bing.com",
    }
)

# Query params dropped whatever their value: tracking IDs, signed-URL params and
# session hints (FIX-5). Compared case-insensitively.
_STRIP_PARAM_EXACT = frozenset(
    {
        "ref",
        "tag",
        "qid",
        "sr",
        "crid",
        "signature",
        "key-pair-id",
        "policy",
        "session-id",
        "sid",
        "smid",
        "csm-hit",
    }
)
_STRIP_PARAM_PREFIXES = ("pd_rd_", "pf_rd_", "x-amz-")


def strip_url_params(url: str) -> str:
    """Drop tracking / signed-URL / session query params from ``url``.

    Scenario: a captured ``href``/``src``/``srcset``/``action`` or ld+json URL still
    carries an AWS presigned signature or a session id.
    Boundaries: only the query string is rewritten; scheme/host/path/fragment are
    preserved, and a URL with no query is returned unchanged.
    On failure: an unparseable URL is returned unchanged (best effort).
    """
    if "?" not in url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    kept: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in _STRIP_PARAM_EXACT or any(lowered.startswith(p) for p in _STRIP_PARAM_PREFIXES):
            continue
        kept.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


def _strip_srcset(value: str) -> str:
    cleaned: list[str] = []
    for candidate in value.split(","):
        candidate = candidate.strip()
        if not candidate:
            continue
        bits = candidate.split(None, 1)
        bits[0] = strip_url_params(bits[0])
        cleaned.append(" ".join(bits))
    return ", ".join(cleaned)


# --------------------------------------------------------------------------- #
# strip_scripts_and_styles
# --------------------------------------------------------------------------- #
def _is_ld_json(el: _Element) -> bool:
    return el.tag == "script" and (_attr(el, "type", "") or "").strip().lower() == "application/ld+json"


def strip_scripts_and_styles(html: str) -> str:
    """Remove ``<script>`` (except ld+json), ``<style>``, ``<noscript>``, ``<iframe>``
    and inline ``on*=`` handlers.

    Scenario: raw page markup carries executable / styling / tracking noise.
    Boundaries: ``<script type="application/ld+json">`` is kept verbatim here (it is
    sanitized separately by :func:`sanitize_ld_json`).
    On failure: never - malformed markup parses best-effort.
    """
    root = _parse(html)

    def should_remove(el: _Element) -> bool:
        local = _local_name(el.tag)
        if local == "script":
            # Only a real (non-namespaced) ld+json script is kept; svg:script etc. go.
            return not (el.tag == "script" and _is_ld_json(el))
        return local in ("style", "noscript", "iframe")

    _filter_tree(root, should_remove)
    for el in _iter_elements(root):
        el.attrs = [pair for pair in el.attrs if not pair[0].lower().startswith("on")]
    return _serialize(root)


# --------------------------------------------------------------------------- #
# strip_tracking
# --------------------------------------------------------------------------- #
def _is_tracker(el: _Element) -> bool:
    if el.tag not in ("img", "link"):
        return False
    url = _attr(el, "src") or _attr(el, "href") or ""
    host = urlsplit(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in _TRACKER_HOSTS)


def strip_tracking(html: str) -> str:
    """Remove tracker ``<img>``/``<link>`` beacons and strip tracking/signed/session
    query params from ``href``/``src``/``srcset``/``action`` (FIX-5).

    Scenario: analytics beacons and presigned/session URLs ride along in markup.
    Boundaries: only known tracker hosts drop the element; every other URL keeps its
    host/path and only loses the stripped params.
    On failure: never - unparseable URLs pass through unchanged.
    """
    root = _parse(html)
    _filter_tree(root, _is_tracker)
    for el in _iter_elements(root):
        for pair in el.attrs:
            name, value = pair[0].lower(), pair[1]
            if value is None:
                continue
            if name in ("href", "src", "action"):
                pair[1] = strip_url_params(value)
            elif name == "srcset":
                pair[1] = _strip_srcset(value)
    return _serialize(root)


# --------------------------------------------------------------------------- #
# strip_secrets
# --------------------------------------------------------------------------- #
_SENSITIVE_ATTR_RE = re.compile(r"csrf|token|session|nonce", re.IGNORECASE)

# Segment-based sensitive-name matching (used for both JSON keys and data-* names).
# A name is split on ``_ - .`` and camelCase boundaries; it is sensitive if any lowered
# segment - or an adjacent segment pair like ``api``+``key`` - is in the relevant set.
# This redacts ``authToken``/``api_key``/``X-CSRF-Token`` but NOT ``accepted_authors``,
# ``author``, ``authors`` or ``sidebar`` (the substring rule wrongly hit these).
_KEY_SPLIT_RE = re.compile(r"[_\-.]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SENSITIVE_DATA_SEGMENTS = frozenset(
    {
        "session",
        "sid",
        "csrf",
        "token",
        "uid",
        "ubid",
        "customer",
        "csm",
        "state",
        "config",
        "props",
        "auth",
        "jwt",
        "secret",
        "apikey",
    }
)


def _name_segments(name: str) -> list[str]:
    return [part.lower() for part in _KEY_SPLIT_RE.split(name) if part]


def _has_sensitive_segment(name: str, segments: frozenset[str]) -> bool:
    segs = _name_segments(name)
    if any(seg in segments for seg in segs):
        return True
    return any((a + b) in segments for a, b in zip(segs, segs[1:], strict=False))


_SENSITIVE_META_RE = re.compile(
    r"csrf|token|session|cookie|geo|latitude|longitude|ip[-_]?address|ab[-_]?test|ubid|customer|nonce",
    re.IGNORECASE,
)
# An email address. The negative lookahead after ``@`` rejects retina asset descriptors
# (``sprite@2x.png``) and the TLD lookahead rejects image-extension "TLDs" (R-4), so a
# real address is rewritten but a cover-image filename is left intact.
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@(?!\d+x[.\-])[A-Za-z0-9.\-]+"
    r"\.(?!(?:png|jpe?g|gif|webp|svg|avif|bmp|ico|tiff?)\b)[A-Za-z]{2,}"
)


def _replace_emails(text: str) -> str:
    return _EMAIL_RE.sub(EMAIL_PLACEHOLDER, text)


def _is_sensitive_meta(el: _Element) -> bool:
    if el.tag != "meta":
        return False
    for key in ("name", "property", "http-equiv", "itemprop"):
        value = _attr(el, key)
        if value and _SENSITIVE_META_RE.search(value):
            return True
    return False


def strip_secrets(html: str) -> str:
    """Blank hidden-input values, drop identity/tracking attributes and meta tags,
    and rewrite emails.

    Scenario: a logged-out page still echoes a CSRF token, a session/customer id in a
    ``data-*`` attribute, a cookie value in a meta tag, or an email in the markup.
    Boundaries: removes attributes whose name matches csrf/token/session/nonce; drops
    ``data-*``/``data-a-*`` that are sensitive by name (FIX-3), JSON-shaped (R-2, no
    selector needs a JSON-valued data-*), or over 200 chars; length-gates/strips opaque
    ``style`` values (V-6); strips identity/tracking ``<meta>``; replaces every email
    with a reserved address.
    On failure: never - best-effort over malformed markup.
    """
    root = _parse(html)
    _filter_tree(root, _is_sensitive_meta)
    for el in _iter_elements(root):
        if el.tag == "input" and (_attr(el, "type", "") or "").lower() == "hidden":
            _set_attr(el, "value", "")
        kept: list[list[Optional[str]]] = []
        for name, value in el.attrs:
            lowered = name.lower()
            if _SENSITIVE_ATTR_RE.search(lowered):
                continue
            if lowered.startswith("data-"):
                if _has_sensitive_segment(name, _SENSITIVE_DATA_SEGMENTS):
                    continue
                # R-2: drop any JSON-shaped data-* wholesale. No site_constants selector
                # reads a JSON-valued data-*, so dropping is both simpler and strictly
                # safer than trying to redact opaque values nested at any depth/key.
                if value is not None and _looks_like_json(value):
                    continue
                if value is not None and len(value) > DATA_ATTR_MAX_CHARS:
                    continue
            if lowered == "style" and value is not None and _is_opaque_style(value):
                continue
            kept.append([name, value])
        el.attrs = kept
    return _replace_emails(_serialize(root))


def _looks_like_json(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("{") or stripped.startswith("[")


def _is_opaque_style(value: str) -> bool:
    return "url(data:" in value.lower() or len(value) > DATA_ATTR_MAX_CHARS


# --------------------------------------------------------------------------- #
# shrink
# --------------------------------------------------------------------------- #
def shrink(html: str, keep_selectors: list[str], min_chars: int = SHRINK_MIN_SUBTREE_CHARS) -> str:
    """Drop large subtrees (reviews, carousels) containing none of ``keep_selectors``.

    Scenario: a captured page is far larger than the size budget, mostly from
    recommendation/review blocks the selectors never touch.
    Boundaries: a subtree that contains (or is an ancestor of) a keep-selector match
    is always kept; only subtrees larger than ``min_chars`` and free of matches drop.
    On failure: never - a page with no oversized noise is returned unchanged.
    """
    root = _parse(html)
    needed: set[int] = set()
    for selector in keep_selectors:
        for el in _select(root, selector):
            node: Optional[_Element] = el
            while node is not None:
                needed.add(id(node))
                node = node.parent

    def should_remove(el: _Element) -> bool:
        if id(el) in needed:
            return False
        return len(_serialize(el)) > min_chars

    _filter_tree(root, should_remove)
    return _serialize(root)


# --------------------------------------------------------------------------- #
# neutralize_prose
# --------------------------------------------------------------------------- #
_PROSE_CONTAINER_RE = re.compile(
    r"description|review|about|bio|blurb|editorial|synopsis|summary|comment|abstract",
    re.IGNORECASE,
)


def _is_prose_container(el: _Element) -> bool:
    for key in ("id", "class"):
        value = _attr(el, key)
        if value and _PROSE_CONTAINER_RE.search(value):
            return True
    return False


def _contains_protected(el: _Element, protected: set[int]) -> bool:
    return any(id(descendant) in protected for descendant in _iter_elements(el))


def _neutralize_direct_text(el: _Element, max_chars: int) -> None:
    prose_container = _is_prose_container(el)
    for child in el.children:
        if isinstance(child, _Text):
            stripped = child.data.strip()
            if not stripped:
                continue
            # Short text (headings/labels/field values) stays verbatim even in a prose
            # container, so a `:has-text()` selector keyed off it still matches.
            if len(stripped) > max_chars or (prose_container and len(stripped) >= PROSE_MIN_CHARS):
                child.data = PROSE_PLACEHOLDER


def _neutralize(el: _Element, protected: set[int], max_chars: int) -> None:
    # Called only on elements that are neither protected nor inside a protected subtree.
    if _contains_protected(el, protected):
        # Cannot collapse wholesale (would clobber a factual field); descend instead.
        for child in el.children:
            if isinstance(child, _Element) and id(child) not in protected:
                _neutralize(child, protected, max_chars)
        _neutralize_direct_text(el, max_chars)
        return
    # No protected descendant: if this element's aggregate text reads as prose, collapse
    # the whole subtree to one placeholder (catches prose chunked across sub-200-char
    # sibling spans, V-5). A prose-named container with only short text (a heading/label)
    # is left alone so its selector text survives.
    aggregate_len = len(_text_content(el))
    if aggregate_len > max_chars or (_is_prose_container(el) and aggregate_len >= PROSE_MIN_CHARS):
        el.children = [_Text(PROSE_PLACEHOLDER)]
        return
    for child in el.children:
        if isinstance(child, _Element):
            _neutralize(child, protected, max_chars)
    _neutralize_direct_text(el, max_chars)


def neutralize_prose(html: str, keep_selectors: list[str], max_chars: int = PROSE_MAX_CHARS) -> str:
    """Replace long/free-form prose with a neutral placeholder, keeping factual metadata.

    How "prose" is decided: an element reads as prose when its aggregate collapsed text
    content (its own and descendant text, excluding script/style) exceeds ``max_chars``
    (~200), OR it signals prose by id/class (description, review, about, bio, blurb,
    editorial, synopsis, summary, comment, abstract). Using the *aggregate* length means
    prose split across several short sibling spans is still caught (V-5). Any element
    matched by ``keep_selectors`` (and its subtree) is preserved verbatim - the caller
    passes every detail selector EXCEPT the description, so the description element stays
    in the DOM (its selector still matches) but its long text collapses to the
    placeholder. Script/style subtrees are also protected so a sanitized ld+json blob is
    never mistaken for prose.

    Scenario: the public repo must not carry copyrighted descriptions/reviews.
    Boundaries: short factual values (title, authors, ISBN, dates) are untouched.
    On failure: never - a page with no prose is returned unchanged.
    """
    root = _parse(html)
    protected: set[int] = set()
    for el in _iter_elements(root):
        if _local_name(el.tag) in ("script", "style"):
            protected.add(id(el))
    for selector in keep_selectors:
        for el in _select(root, selector):
            for descendant in _iter_elements(el):
                protected.add(id(descendant))
    _neutralize(root, protected, max_chars)
    return _serialize(root)


# --------------------------------------------------------------------------- #
# JSON sanitizers
# --------------------------------------------------------------------------- #
# Redacted on a SEGMENT match (not a substring), so ``accepted_authors``/``author`` are
# left intact while ``authToken``/``auth_token``/``sessionId``/``api_key``/``X-CSRF-Token``
# still redact. ``authorization`` is treated as sensitive.
_SENSITIVE_KEY_SEGMENTS = frozenset(
    {
        "email",
        "token",
        "session",
        "sid",
        "password",
        "passwd",
        "secret",
        "auth",
        "authtoken",
        "apikey",
        "csrf",
        "jwt",
        "cookie",
        "bearer",
        "authorization",
    }
)
_JSON_PROSE_KEY_RE = re.compile(r"description|about|review|blurb|bio|abstract|synopsis", re.IGNORECASE)


def _sanitize_json_string(value: str) -> str:
    result = value
    if "://" in result:
        result = strip_url_params(result)
    return _replace_emails(result)


def sanitize_json(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact secrets, neutralize prose and clean URLs/emails in a JSON value.

    Scenario: a Leanpub API body or a kept ld+json blob carries an email, a token, a
    long description, or a signed image URL.
    Boundaries: keys whose segments match the sensitive set (email/token/session/auth/
    ...) are redacted - segment-matched, so ``accepted_authors`` survives; string values
    under prose-named keys longer than ~200 chars become the placeholder; URL and email
    strings anywhere are cleaned. Recursion is capped (FIX-11) - past the cap the value
    is returned as-is rather than recursing further.
    On failure: never - non-container leaves pass through.
    """
    if _depth >= MAX_JSON_DEPTH:
        return obj
    if isinstance(obj, dict):
        cleaned: dict[Any, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and _has_sensitive_segment(key, _SENSITIVE_KEY_SEGMENTS):
                cleaned[key] = REDACTED_VALUE
            elif (
                isinstance(key, str)
                and _JSON_PROSE_KEY_RE.search(key)
                and isinstance(value, str)
                and len(value) > PROSE_MAX_CHARS
            ):
                cleaned[key] = PROSE_PLACEHOLDER
            else:
                cleaned[key] = sanitize_json(value, _depth + 1)
        return cleaned
    if isinstance(obj, list):
        return [sanitize_json(item, _depth + 1) for item in obj]
    if isinstance(obj, str):
        return _sanitize_json_string(obj)
    return obj


def sanitize_ld_json(html: str) -> str:
    """Parse every kept ld+json blob, run it through :func:`sanitize_json`, re-embed it.

    Scenario: ld+json routinely carries signed image URLs, emails and tracking ids
    (FIX-4).
    Boundaries: only ``<script type="application/ld+json">`` is touched; a blob that
    fails to parse is emptied (fail-safe) rather than kept verbatim. Any other script
    element (including one a literal ``</script>`` breakout split off, V-3) is removed
    so the invariant "only sanitized ld+json survives as script" holds even when this
    function is called on its own.
    On failure: never raises - an unparseable blob is dropped.
    """
    root = _parse(html)
    _filter_tree(root, lambda el: _local_name(el.tag) == "script" and not _is_ld_json(el))
    for el in _iter_elements(root):
        if not _is_ld_json(el):
            continue
        raw = "".join(child.data for child in el.children if isinstance(child, _Text))
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            el.children = []
            continue
        el.children = [_Text(_encode_ld_json(sanitize_json(data)))]
    return _serialize(root)


def _encode_ld_json(obj: Any) -> str:
    """Serialize ``obj`` as JSON safe to embed verbatim in a rawtext ``<script>``.

    ``<``, ``>`` and ``&`` are emitted as JSON ``\\u00XX`` escapes so a value containing
    ``</script>`` cannot break out of the element (V-3, CWE-116/CWE-79). These escapes
    are valid JSON: ``json.loads`` decodes them back to the original characters, so the
    embedded blob still round-trips to exactly the sanitized object.
    """
    text = json.dumps(obj, ensure_ascii=False)
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


# --------------------------------------------------------------------------- #
# Top-level composition
# --------------------------------------------------------------------------- #
def sanitize_html(html: str, keep_selectors: Optional[list[str]] = None) -> str:
    """Run the full HTML sanitize pipeline the capture always applies."""
    selectors = keep_selectors or []
    result = strip_scripts_and_styles(html)
    result = sanitize_ld_json(result)
    result = strip_tracking(result)
    result = strip_secrets(result)
    result = neutralize_prose(result, selectors)
    return result


def sanitize_text(text: str) -> str:
    """Minimal sanitizer for plain-text fixtures (robots.txt): rewrite emails."""
    return _replace_emails(text)


# --------------------------------------------------------------------------- #
# assert_clean - the fail-closed final gate (FIX-2)
# --------------------------------------------------------------------------- #
# Vendor credential shapes, copied from
# .claude/skills/secret-scan/references/pattern-catalog.md (that hook lives outside the
# package and imports a private _common module, so it cannot be imported here).
_CREDENTIAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "AWS access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "AWS secret access key": re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}"),
    "Anthropic API key": re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    "OpenAI API key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{20,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    "Private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    "Generic assigned secret": re.compile(
        r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[=:]\s*['\"](?P<value>[^'\"\s]{8,})['\"]"
    ),
    "Postgres URL with password": re.compile(r"postg(?:res|resql)://[^:\s]+:[^@\s]+@"),
}

# Corpus-specific detectors the credential-only catalog misses (FIX-2).
_EXTENDED_PATTERNS: dict[str, re.Pattern[str]] = {
    "Cookie header": re.compile(r"(?i)\bset-cookie\s*:"),
    "Session/CSRF token value": re.compile(
        r"(?i)\b(session[-_]?id|sessionid|csrf[-_]?token|xsrf[-_]?token|ubid[-_]?\w+)\b\s*[=:]\s*\S"
    ),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"),
    "Signed URL param": re.compile(
        r"(?i)(x-amz-(signature|credential|security-token|expires)|key-pair-id|cloudfront-signature)"
    ),
    "Email address": _EMAIL_RE,
}

_ALL_DETECTORS: list[tuple[str, re.Pattern[str]]] = list(_CREDENTIAL_PATTERNS.items()) + list(
    _EXTENDED_PATTERNS.items()
)

# Exact placeholder values this sanitizer emits; a detector hit is a self-match (not a
# leaked secret) ONLY on exact equality with one of these (R-1). No substring or shape
# match - that fuzziness let a payload prefixed with "REDACTED" or wrapped in <...> slip
# through. The example-email exemption is anchored to the exact RFC-2606 reserved
# domains, so ``steal@evilexample.com`` is NOT exempt (R-4).
_PLACEHOLDER_VALUES = frozenset({REDACTED_VALUE, EMAIL_PLACEHOLDER, PROSE_PLACEHOLDER})
_EXAMPLE_EMAIL_RE = re.compile(r"@example\.(?:invalid|com|net|org)$", re.IGNORECASE)

# Whitespace (incl. U+2028/U+2029/NBSP via ``\s``) plus zero-width separators, removed
# before the third assert_clean scan so a token split by an invisible gap is caught (R-3).
_ALL_WHITESPACE_RE = re.compile(r"[\s​‌‍⁠﻿]+")


def _match_is_placeholder(name: str, match: re.Match[str]) -> bool:
    """True when a detector hit is EXACTLY one of our own emitted placeholders."""
    if name == "Email address":
        return bool(_EXAMPLE_EMAIL_RE.search(match.group(0)))
    if name == "Generic assigned secret":
        return (match.groupdict().get("value") or "") in _PLACEHOLDER_VALUES
    return match.group(0) in _PLACEHOLDER_VALUES


def _collapse_all_whitespace(text: str) -> str:
    # Remove our own emitted placeholders first, so collapsing whitespace cannot fuse a
    # placeholder (e.g. the reserved-domain email) with adjacent text into a spurious hit.
    for placeholder in (EMAIL_PLACEHOLDER, PROSE_PLACEHOLDER, REDACTED_VALUE):
        text = text.replace(placeholder, " ")
    return _ALL_WHITESPACE_RE.sub("", text)


def _scan_segment(segment: str, where: str) -> None:
    for name, pattern in _ALL_DETECTORS:
        for match in pattern.finditer(segment):
            if _match_is_placeholder(name, match):
                continue
            raise SanitizationError(f"assert_clean: possible {name} at {where}")


def assert_clean(text: str) -> None:
    """Fail-closed final gate: raise :class:`SanitizationError` if any secret survives.

    Scenario: the last check before sanitized bytes (and the .meta.json) are written to
    permanent public history.
    Boundaries: runs the vendor credential catalog PLUS corpus-specific detectors
    (cookies, session/CSRF tokens, JWTs, signed-URL params, emails) over three views:
    the serialized bytes (line by line), the whitespace-normalized concatenated text
    content of the parsed tree (so a secret split across sibling text nodes is caught,
    V-4), and a fully whitespace-stripped variant of that text (so a token split by a
    space or an invisible U+2028/zero-width separator is caught, R-3). A hit is skipped
    only when the matched value is *exactly* one of the placeholders this sanitizer
    emits (R-1) - never because a word appears on the line. Cannot be bypassed by
    ``--force``.
    On failure: raises with the detector name (never the matched value).
    """
    if not isinstance(text, str):
        raise SanitizationError("assert_clean requires a string")
    for lineno, line in enumerate(text.splitlines(), start=1):
        _scan_segment(line, f"line {lineno}")
    tree_text = _text_content(_parse(text))
    _scan_segment(tree_text, "concatenated text content")
    _scan_segment(_collapse_all_whitespace(tree_text), "whitespace-stripped text content")
