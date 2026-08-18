"""Structural skeleton extractor for raw MIME messages.

Produces a PII-safe structural representation of a raw MIME message: header
name order, recursive MIME tree, HTML tag sequence, HTML literal probes, and
plain-text line structure.  All email addresses, display names, and body text
are redacted so no personal content appears in the output.
"""

import re
from email import message_from_bytes
from html.parser import HTMLParser

EMAIL_RE = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
)

# Elements whose *inner text* must also be redacted (not just dropped as
# ordinary text nodes) because they carry display names or attribution text.
_REDACT_TEXT_ELEMENTS = {"gmail_attr", "gmail_sendername"}

# HTML void elements have no end tag. html.parser fires handle_starttag for a
# bare one (e.g. <br>) but never a matching handle_endtag, so they must not be
# pushed onto the class stack or they desync redact-scope tracking.
_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


def _redact(s: str) -> str:
    return EMAIL_RE.sub("‹email›", s or "")


def _tag_list(html: str) -> list[str]:
    """Return the sequence of HTML tags with attributes; text nodes dropped; emails+hrefs redacted."""
    tags = re.findall(r"<[^>]+>", html)
    out = []
    for t in tags:
        t = _redact(t)
        t = re.sub(r'href="[^"]*"', 'href="‹h›"', t)
        out.append(t)
    return out


def _literal_probes(html: str) -> dict:
    """Report presence + redacted structure of known Gmail scaffolding."""
    probes: dict = {}
    probes["has_gmail_quote_container"] = (
        'class="gmail_quote gmail_quote_container"' in html
    )
    probes["has_gmail_attr"] = 'class="gmail_attr"' in html
    probes["has_blockquote_gmail_quote"] = 'blockquote class="gmail_quote"' in html
    probes["has_gmail_sendername"] = "gmail_sendername" in html
    probes["has_forwarded_literal"] = "Forwarded message" in html

    m = re.search(
        r'<div dir="ltr" class="gmail_attr">(.*?)wrote:<br\s*/?></div>',
        html,
        re.S,
    )
    if m:
        inner = m.group(1)
        on = re.match(r"On .*?, ", inner)
        probes["attr_template"] = (
            "On ‹date›, " if on else ""
        ) + "‹name› &lt;<a href=mailto>‹email›</a>&gt; wrote:"

    m = re.search(
        r"(-{6,} Forwarded message -{6,}.*?)(?:</div>|<blockquote)", html, re.S
    )
    if m:
        block = m.group(1)
        probes["forward_header_block"] = "".join(_tag_list(block))[:600]

    m = re.search(r'<blockquote class="gmail_quote" style="([^"]*)"', html)
    if m:
        probes["blockquote_style"] = m.group(1)

    return probes


def _plain_line_structure(text: str) -> list[str]:
    """Classify each plain-text line by structure (no content)."""
    out = []
    # Normalize CRLF and CR line endings to LF before splitting.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for line in normalized.split("\n"):
        if line == ">":
            out.append("QUOTE_BLANK(>)")
        elif line.startswith("> "):
            out.append("QUOTE(> )")
        elif re.match(r"^On .*wrote:$", line):
            out.append(
                "ATTR_LINE: "
                + re.sub(r"(On .*?, ).*", r"\1‹name› ‹email› wrote:", line)
            )
        elif re.match(r"^-{6,} Forwarded message -{6,}$", line):
            out.append("FWD_SEP: " + line)
        elif re.match(r"^(From|Date|Subject|To|Cc):", line):
            out.append("FWD_HDR: " + line.split(":", 1)[0] + ": ‹v›")
        elif line.strip() == "":
            out.append("BLANK")
        else:
            out.append("‹text›")
    return out


def _build_mime_tree(msg) -> dict:
    """Recursively build a structured dict representing the MIME tree node."""
    ct = msg.get_content_type()
    cte = msg.get("Content-Transfer-Encoding") or "-"
    charset = msg.get_content_charset() or "-"
    node: dict = {"content_type": ct, "charset": charset, "cte": cte}

    disp = msg.get("Content-Disposition", "")
    if disp:
        node["disposition"] = disp.split(";")[0].strip()
    if msg.get_filename():
        node["filename_present"] = True

    if msg.is_multipart():
        boundary = msg.get_boundary() or ""
        node["boundary_pattern"] = re.sub(r"[0-9a-f]", "x", boundary)
        node["parts"] = [_build_mime_tree(p) for p in msg.get_payload()]

    return node


def _header_skeleton(msg) -> list[str]:
    """Return header names in the order they appear."""
    return [k for k, _ in msg.items()]


def _get_html_and_plain(msg) -> tuple[str, str]:
    html = plain = ""
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/html" and not html:
            payload = part.get_payload(decode=True) or b""
            html = payload.decode(part.get_content_charset() or "utf-8", "replace")
        elif ct == "text/plain" and not plain:
            payload = part.get_payload(decode=True) or b""
            plain = payload.decode(part.get_content_charset() or "utf-8", "replace")
    return html, plain


# ---------------------------------------------------------------------------
# HTML sanitizer
# ---------------------------------------------------------------------------


class _Sanitizer(HTMLParser):
    """Drop text nodes; redact emails and hrefs in tag attributes.

    Special handling: text inside elements with class ``gmail_attr`` or
    ``gmail_sendername`` is also suppressed (those elements carry attribution
    text / sender display names that would otherwise leak through as text nodes
    of a recognised structural element).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._out: list[str] = []
        # Stack of element class values so we can detect redact-text elements.
        self._class_stack: list[set[str]] = []

    def _in_redact_element(self) -> bool:
        return any(classes & _REDACT_TEXT_ELEMENTS for classes in self._class_stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        cls_tokens: set[str] = set()
        parts = [f"<{tag}"]
        for name, value in attrs:
            if value is None:
                parts.append(f" {name}")
            elif name == "href":
                parts.append(f' {name}="‹h›"')
            else:
                redacted = _redact(value)
                parts.append(f' {name}="{redacted}"')
                if name == "class":
                    cls_tokens = set(value.split())
        parts.append(">")
        self._out.append("".join(parts))
        # Void elements have no end tag, so don't track a scope for them.
        if tag not in _VOID_ELEMENTS:
            self._class_stack.append(cls_tokens)

    def handle_endtag(self, tag: str) -> None:
        self._out.append(f"</{tag}>")
        if tag not in _VOID_ELEMENTS and self._class_stack:
            self._class_stack.pop()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Self-closing tags — treat like start+end with no text children.
        self.handle_starttag(tag, attrs)
        # handle_starttag only pushed for non-void tags; balance that here.
        if tag not in _VOID_ELEMENTS and self._class_stack:
            self._class_stack.pop()

    def handle_data(self, data: str) -> None:
        # Suppress all text nodes; also suppress inner text of redact elements.
        if self._in_redact_element():
            return
        # Plain text nodes outside redact elements are also dropped — the
        # skeleton preserves only structural markup.

    def handle_entityref(self, name: str) -> None:
        # Drop entity references (they are text content).
        pass

    def handle_charref(self, name: str) -> None:
        # Drop character references (they are text content).
        pass

    def result(self) -> str:
        return "".join(self._out)


def sanitize_html(html: str) -> str:
    """Return a structural skeleton of *html* with all text content removed.

    - Text nodes are dropped entirely.
    - Text *inside* ``gmail_attr`` and ``gmail_sendername`` elements is also
      suppressed (fixes attribution / sender-name leakage).
    - Email addresses in attributes are replaced with a placeholder.
    - ``href`` attribute values are replaced with a placeholder.
    - Tag names, remaining attributes (class, style, dir, …), and Gmail
      literal scaffolding tokens (e.g. ``Forwarded message``) embedded in
      tag attributes are preserved verbatim.
    """
    parser = _Sanitizer()
    parser.feed(html)
    return parser.result()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_skeleton(raw_bytes: bytes) -> dict:
    """Parse *raw_bytes* as a MIME message and return its structural skeleton.

    Returns a dict with keys:

    ``headers_order``
        List of header name strings, in the order they appear (names only).
        Header *value* fidelity (Content-Type boundary shape, transfer
        encoding, etc.) is asserted separately via the full MIME-tree /
        golden-body comparison, not here.

    ``mime_tree``
        List containing the single root MIME tree node (a dict); multipart
        nodes carry a ``parts`` list of child nodes and a ``boundary_pattern``
        (boundary with hex digits masked to ``x``).

    ``html_tags``
        List of raw tag strings extracted from the HTML part, with email
        addresses and hrefs redacted.

    ``html_probes``
        Dict of boolean / string probes for known Gmail scaffolding markers.

    ``plain_structure``
        List of line-structure labels for the plain-text part.
    """
    msg = message_from_bytes(raw_bytes)
    html, plain = _get_html_and_plain(msg)

    return {
        "headers_order": _header_skeleton(msg),
        "mime_tree": [_build_mime_tree(msg)],
        "html_tags": _tag_list(html) if html else [],
        "html_probes": _literal_probes(html) if html else {},
        "plain_structure": _plain_line_structure(plain) if plain else [],
    }
