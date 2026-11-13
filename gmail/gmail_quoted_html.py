"""Turn a quoted or forwarded HTML document into a fragment.

A reply or forward built by this server is an HTML fragment: it starts with
``<div dir="ltr">`` and nests the original message's HTML inside it. Gmail's
own messages are fragments too, but most other senders (Outlook, Apple Mail,
Thunderbird, bulk mailers) send a whole document: a doctype, ``<html>``, a
``<head>`` with ``<title>``, ``<meta>`` and ``<style>``, and a ``<body>``.

Nested unchanged, that document's ``<html>``, ``<head>`` and ``<body>`` are
the only ones in the new message, and they sit inside the quote. An HTML
parser recovers by moving the quoted ``<html>`` and ``<body>`` attributes onto
the new message's own root and body and by keeping ``<title>``, ``<meta>`` and
``<style>`` where they stand, so the quoted style sheet restyles the reply
text too. Apple Mail has also been seen to list such a reply with the quoted
message's first lines as its preview.

``normalize_quoted_html`` is synchronous, side-effect free and uses the
standard library only, so it can be unit-tested in isolation.
"""

from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser
from typing import Dict, List, Optional, Sequence, Tuple

# Markup that only a whole HTML document carries (plus Outlook's conditional
# comments). Input without any of it is already a fragment.
_DOCUMENT_MARKUP_RE = re.compile(
    r"<(?:html|head|body|meta|title|style|base|link|script)\b"
    r"|<!doctype|<\?xml|<!--\[if",
    re.IGNORECASE,
)

# The tokens that structure a style sheet. Comments and strings are matched
# whole, so a brace or semicolon inside one is not mistaken for syntax, and an
# unterminated one ends the match instead of being rescanned.
_CSS_TOKEN_RE = re.compile(
    r"""/\*.*?(?:\*/|\Z)|"(?:[^"\\\n]|\\.)*"?|'(?:[^'\\\n]|\\.)*'?|[{}();]""",
    re.DOTALL,
)
_CSS_PROPERTY_RE = re.compile(r"-{0,2}[a-z_][a-z0-9_-]*")
_SIMPLE_SELECTOR_RE = re.compile(
    r"([a-z][a-z0-9]*)?(?:\.(-?[a-z_][\w-]*))?(:link)?", re.IGNORECASE
)
_COLOR_DECLARATION_RE = re.compile(r"(?:^|;)\s*color\s*:", re.IGNORECASE)
_SCREEN_MEDIA = frozenset({"", "all", "screen"})
_LINK_TAGS = frozenset({"a", "area"})
# Inlined declarations may add this much text, or the size of the document if
# that is larger. Past it the style sheet is dropped without being inlined.
_MIN_INLINE_BUDGET = 1_000_000

_TAG_NAME_RE = re.compile(r"<[^\s/>]+")
_ATTRIBUTE_RE = re.compile(
    r"""[\s/]*([^\s/>=]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s>]*))?"""
)

_Attrs = Sequence[Tuple[str, Optional[str]]]
# (specificity, source order, is a :link rule, declarations)
_Rule = Tuple[Tuple[int, int], int, bool, str]
_RuleIndex = Dict[Tuple[Optional[str], Optional[str]], List[_Rule]]
# (source offset, kind, raw start tag, tag name, attributes)
_Event = Tuple[int, int, str, str, _Attrs]

_KEEP, _DROP, _TAG, _BODY = range(4)


def _index_rule(
    rules: _RuleIndex, selector: str, declarations: List[str], order: int
) -> None:
    """Add one rule block to ``rules`` under each simple selector it lists."""
    kept = []
    for declaration in declarations:
        name, colon, value = declaration.partition(":")
        name, value = name.strip().lower(), " ".join(value.split())
        if (
            colon
            and value
            and not value.endswith("\\")
            and _CSS_PROPERTY_RE.fullmatch(name)
            and not name.startswith("mso-")
        ):
            kept.append(f"{name}:{value}")
    if not kept:
        return
    style = ";".join(kept)
    for part in selector.split(","):
        match = _SIMPLE_SELECTOR_RE.fullmatch(part.strip())
        if match and (match.group(1) or match.group(2)):
            tag = match.group(1).lower() if match.group(1) else None
            link = bool(match.group(3))
            specificity = (int(match.group(2) is not None) + link, int(tag is not None))
            rules.setdefault((tag, match.group(2)), []).append(
                (specificity, order, link, style)
            )


def _style_rules(css: str) -> _RuleIndex:
    """Index the ``tag``, ``.class`` and ``tag.class`` rules of a style sheet.

    Only what a style attribute can express is kept. At-rules and their blocks
    (``@media``, ``@font-face``), every other selector and Office-only
    ``mso-*`` declarations are skipped, and so is a declaration with an
    unterminated string or bracket, which would swallow the ones after it.
    """
    rules: _RuleIndex = {}
    pieces: List[str] = []
    declarations: List[str] = []
    selector = ""
    depth = parens = position = order = 0
    usable, nested = True, False
    # An HTML comment wrapper around the sheet is not part of any selector.
    text = css.replace("<!--", " ").replace("-->", " ")
    for token in _CSS_TOKEN_RE.finditer(text):
        pieces.append(text[position : token.start()])
        position = token.end()
        value = token.group()
        if value.startswith("/*"):
            pieces.append(" ")
            continue
        if value[0] in "\"'":
            usable = usable and len(value) > 1 and value[-1] == value[0]
            pieces.append(value)
            continue
        if value in "()" or parens > 0:
            # Inside brackets everything is data, as in url(data:image/png;base64,...);
            # an unclosed bracket swallows the rest of the sheet, as in browsers.
            parens += {"(": 1, ")": -1}.get(value, 0)
            pieces.append(value)
            continue
        if depth == 1 and value != "{" and usable and parens == 0:
            declarations.append("".join(pieces))
        if value == "{":
            if depth == 0:
                selector, declarations, nested = "".join(pieces), [], False
            else:
                nested = True
            depth += 1
        elif value == "}" and depth:
            depth -= 1
            if depth == 0 and not nested:
                _index_rule(rules, selector, declarations, order)
                order += 1
        pieces, usable, parens = [], True, 0
    return rules


def _matching_style(rules: _RuleIndex, tag: str, attrs: _Attrs) -> str:
    """Return the declarations of every rule that matches an element.

    They come in cascade order (specificity, then source order) and are not
    merged: the reader's CSS parser resolves duplicates and ``!important``
    exactly as it would have in the style sheet.
    """
    first: Dict[str, Optional[str]] = {}
    for name, value in attrs:
        first.setdefault(name, value)
    matched = list(rules.get((tag, None), ()))
    for name in dict.fromkeys((first.get("class") or "").split()):
        matched.extend(rules.get((None, name), ()))
        matched.extend(rules.get((tag, name), ()))
    if tag not in _LINK_TAGS or "href" not in first:
        # ":link" never matches a named anchor such as <a name="bookmark">.
        matched = [rule for rule in matched if not rule[2]]
    return ";".join(rule[3] for rule in sorted(matched))


def _applies_on_screen(attrs: _Attrs) -> bool:
    """Whether a ``<style>`` element's rules apply to an on-screen reader."""
    media = next((value or "" for name, value in attrs if name == "media"), "")
    return any(part.strip() in _SCREEN_MEDIA for part in media.lower().split(","))


def _scan_attributes(
    raw: str,
) -> Optional[Tuple[int, List[Tuple[str, int, int, Optional[str]]]]]:
    """Locate the attributes of a raw start tag.

    Returns the offset where the attributes end and one ``(name, start, end,
    raw value)`` per attribute, or None when the tag does not scan cleanly.
    """
    name = _TAG_NAME_RE.match(raw)
    if not name:
        return None
    position = name.end()
    attributes = []
    while True:
        match = _ATTRIBUTE_RE.match(raw, position)
        if not match:
            break
        attributes.append(
            (match.group(1).lower(), match.start(1), match.end(), match.group(2))
        )
        position = match.end()
    if raw[position:].strip(" \t\n\r\f/") != ">":
        return None
    return position, attributes


def _style_attribute(raw_value: Optional[str], css: str) -> str:
    """Render a style attribute holding ``css`` followed by the element's own.

    The element's own declarations are copied as written, in their own quotes,
    and stay last so they still win over the style sheet.
    """
    escaped = _html.escape(css)
    own = raw_value or ""
    quote = '"'
    if len(own) > 1 and own[0] in "\"'" and own[-1] == own[0]:
        quote, own = own[0], own[1:-1]
    else:
        own = own.replace('"', "&quot;")
    own = own.strip()
    return f"style={quote}{escaped};{own}{quote}" if own else f'style="{escaped}"'


def _with_style(raw: str, css: str) -> str:
    """Return a raw start tag with ``css`` added to its style attribute."""
    scanned = _scan_attributes(raw)
    if scanned is None:
        return raw
    end, attributes = scanned
    for name, start, stop, value in attributes:
        if name == "style":
            return f"{raw[:start]}{_style_attribute(value, css)}{raw[stop:]}"
    return f"{raw[:end]} {_style_attribute(None, css)}{raw[end:]}"


def _body_wrapper(raw: str, css: str) -> str:
    """Return the ``<div>`` standing in for a ``<body>`` start tag, or ``""``.

    The div carries what still means something outside a document: the
    body's ``dir``, ``lang`` and ``style`` (with ``css`` from the style sheet).
    """
    kept: Dict[str, str] = {}
    scanned = _scan_attributes(raw)
    for name, start, stop, value in scanned[1] if scanned else []:
        if name not in ("dir", "lang", "style") or name in kept:
            continue
        if value and value.strip("\"' \t\n\r\f"):
            kept[name] = raw[start:stop]
            if name == "style" and css:
                kept[name] = _style_attribute(value, css)
    if css and "style" not in kept:
        kept["style"] = _style_attribute(None, css)
    return f"<div {' '.join(kept.values())}>" if kept else ""


class _DocumentFlattener(HTMLParser):
    """Record which parts of an HTML document survive as a fragment.

    The parser only decides what is kept. Every kept piece is then copied from
    the source text as it was written, so entities, attribute quoting and even
    malformed markup reach the reader unchanged.
    """

    _HEAD_ONLY_TAGS = frozenset({"meta", "link", "base"})
    _DROPPED_BLOCKS = frozenset({"title", "style", "script"})
    _HEAD_TAGS = _HEAD_ONLY_TAGS | _DROPPED_BLOCKS | {"noscript"}
    _DROPPED_END_TAGS = _HEAD_ONLY_TAGS | _DROPPED_BLOCKS | {"html", "head", "body"}

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self._source = source
        self._line_starts = [0]
        self._line_starts.extend(m.end() for m in re.finditer("\n", source))
        self._events: List[_Event] = []
        self._css: List[str] = []
        self._block: Optional[str] = None
        self._collect_css = False
        self._in_head = False

    def _record(
        self, kind: int, raw: str = "", tag: str = "", attrs: _Attrs = ()
    ) -> None:
        line, column = self.getpos()
        offset = self._line_starts[line - 1] + column
        self._events.append((offset, kind, raw, tag, attrs))

    def _start(self, tag: str, attrs: _Attrs, opens: bool) -> None:
        if self._block:
            self._record(_DROP)
            return
        if self._in_head and tag not in self._HEAD_TAGS:
            self._in_head = False  # any other element ends the head, as in browsers
        if tag in self._DROPPED_BLOCKS:
            if opens:
                self._block = tag
                self._collect_css = tag == "style" and _applies_on_screen(attrs)
            self._record(_DROP)
            return
        if tag == "head":
            self._in_head = True
        if self._in_head or tag == "html" or tag in self._HEAD_ONLY_TAGS:
            self._record(_DROP)
            return
        kind = _BODY if tag == "body" else _TAG
        self._record(kind, self.get_starttag_text() or "", tag, attrs)

    def handle_starttag(self, tag: str, attrs: _Attrs) -> None:
        self._start(tag, attrs, opens=True)

    def handle_startendtag(self, tag: str, attrs: _Attrs) -> None:
        self._start(tag, attrs, opens=False)

    def handle_endtag(self, tag: str) -> None:
        if self._block:
            if tag == self._block:
                self._block = None
                self._collect_css = False
            self._record(_DROP)
            return
        if tag == "head":
            self._in_head = False
        dropped = self._in_head or tag in self._DROPPED_END_TAGS
        self._record(_DROP if dropped else _KEEP)

    def handle_data(self, data: str) -> None:
        if self._block:
            if self._collect_css:
                self._css.append(data)
            self._record(_DROP)
            return
        if self._in_head:
            if not data.strip(" \t\n\r\f"):
                self._record(_DROP)
                return
            self._in_head = False  # text ends the head, as in browsers
        self._record(_KEEP)

    # Comments (Outlook's conditional ones included), the doctype, processing
    # instructions and marked-section markers such as <![if !supportLists]> are
    # dropped. What stands between two markers is kept, as browsers show it.
    def handle_comment(self, data: str) -> None:
        self._record(_DROP)

    def handle_decl(self, decl: str) -> None:
        self._record(_DROP)

    def handle_pi(self, data: str) -> None:
        self._record(_DROP)

    def unknown_decl(self, data: str) -> None:
        # A CDATA section is text inside SVG or MathML, so it is kept as written.
        self._record(_KEEP if data[:6].upper() == "CDATA[" else _DROP)

    def _inline_styles(self) -> Dict[int, str]:
        """Map an event's index to the style sheet declarations for its tag."""
        rules = _style_rules("\n".join(self._css))
        budget = max(len(self._source), _MIN_INLINE_BUDGET)
        styles: Dict[int, str] = {}
        for index, (_offset, kind, _raw, tag, attrs) in enumerate(self._events):
            if rules and kind in (_TAG, _BODY):
                css = _matching_style(rules, tag, attrs)
                budget -= len(css)
                if budget < 0:
                    return {}
                if css:
                    styles[index] = css
        return styles

    def _wrapper(self, styles: Dict[int, str]) -> Tuple[str, bool]:
        """Return the div standing in for ``<body>`` and whether it sets a colour."""
        for index, (_offset, kind, raw, _tag, attrs) in enumerate(self._events):
            if kind == _BODY:
                css = styles.get(index, "")
                wrapper = _body_wrapper(raw, css)
                if wrapper:
                    own = next((v or "" for name, v in attrs if name == "style"), "")
                    return wrapper, bool(_COLOR_DECLARATION_RE.search(f"{css};{own}"))
        return "", False

    def fragment(self) -> str:
        """Return the fragment; call once, after feeding the whole document."""
        self.close()
        source, events = self._source, self._events
        styles = self._inline_styles()
        wrapper, body_colour = self._wrapper(styles)
        pieces: List[str] = []
        for index, (offset, kind, raw, tag, _attrs) in enumerate(events):
            end = events[index + 1][0] if index + 1 < len(events) else len(source)
            if kind == _KEEP:
                pieces.append(source[offset:end])
            elif kind in (_TAG, _BODY):
                if not source.startswith(raw, offset):
                    raise ValueError("start tag is not where the parser reported it")
                if kind == _TAG:
                    css = styles.get(index, "")
                    if tag == "table" and body_colour:
                        # Without a doctype a table takes its text colour from
                        # <body>, not from its parent: point it at the div.
                        css = f"color:inherit;{css}" if css else "color:inherit"
                    pieces.append(_with_style(raw, css) if css else raw)
                pieces.append(source[offset + len(raw) : end])
        fragment = "".join(pieces).strip()
        # Like the body it stands in for, the div holds the whole fragment: what
        # follows </body>, and what surrounds a document nested in a fragment.
        return f"{wrapper}{fragment}</div>" if wrapper else fragment


def normalize_quoted_html(html_text: str) -> str:
    """Return a quoted or forwarded message's HTML as a fragment that can be nested.

    A fragment without document markup, such as Gmail's own HTML, is returned
    unchanged. A whole document loses its ``<html>`` and ``<body>`` tags (the
    body's ``dir``, ``lang`` and ``style`` move to a ``<div>`` around the
    fragment), its ``<head>`` with ``<title>``, ``<meta>``, ``<link>``,
    ``<base>`` and ``<style>``, scripts, comments (Outlook's conditional
    blocks included) and the doctype.

    Simple style sheet rules (``tag``, ``.class``, ``tag.class``) are inlined
    as style attributes first, so the quoted message keeps its fonts and
    spacing; rules a style attribute cannot express are dropped with the
    sheet. Everything else, text and tags alike, is copied as written.
    """
    if not html_text or not _DOCUMENT_MARKUP_RE.search(html_text):
        return html_text
    try:
        flattener = _DocumentFlattener(html_text)
        flattener.feed(html_text)
        return flattener.fragment()
    except Exception:  # Arbitrary inbound HTML must never block a reply.
        return html_text
