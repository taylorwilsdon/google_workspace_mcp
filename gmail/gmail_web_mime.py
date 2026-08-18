"""Pure helpers for building Gmail-web faithful MIME content.

These functions are deliberately synchronous and side-effect free so they can be
unit-tested in isolation. Network-bound work (People API name resolution, parent
message fetching) happens in the async Gmail tools, which feed the resulting
strings into these builders and into ``_prepare_gmail_message``.

The one verbatim external constant here is Gmail's blockquote CSS string
(``BLOCKQUOTE_STYLE``); it is Gmail's own markup, not user data.
"""

from __future__ import annotations

import base64
import html as _html
import quopri
import re
import secrets
import unicodedata
from datetime import datetime
from email.header import Header
from email.utils import encode_rfc2231, formataddr
from typing import List, Optional, Tuple

# Gmail's byte-identical blockquote style string for quoted replies.
BLOCKQUOTE_STYLE = (
    "margin:0px 0px 0px 0.8ex;border-left:1px solid rgb(204,204,204);padding-left:1ex"
)

# Three-letter day/month names matching Gmail's attribution line.
_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MON = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def gmail_boundary() -> str:
    """Return a Gmail-style multipart boundary.

    Matches ``^0{12}[0-9a-f]{16}$`` -- the literal ``000000000000`` prefix
    followed by exactly 16 random lowercase hex characters (total length 28).
    """
    # 8 random bytes -> exactly 16 hex chars.
    return "000000000000" + secrets.token_hex(8)


def format_display_address(name: Optional[str], email: str) -> str:
    """Format ``Display Name <email>`` for a header.

    - No name -> bare address (so unresolved lookups still send).
    - Non-ASCII names are RFC2047 encoded-word wrapped.
    - Names with commas/specials are RFC5322 quoted.

    ``email.utils.formataddr`` handles RFC5322 quoting; for non-ASCII it falls
    back to RFC2047 via ``email.headerregistry.Address``.
    """
    # Strip CR/LF/NUL from the address too: formataddr does not sanitize it, so
    # an unvalidated CRLF-bearing address would be RFC5322 header injection.
    safe_email = _strip_header_controls(email)
    if not name or not name.strip():
        return safe_email
    safe_name = _strip_header_controls(name)
    try:
        safe_name.encode("ascii")
        # ASCII name: formataddr applies RFC5322 quoting when needed.
        return formataddr((safe_name, safe_email))
    except UnicodeEncodeError:
        # Non-ASCII name: RFC2047 encoded-word for the display phrase.
        encoded_name = Header(safe_name, "utf-8").encode(maxlinelen=998)
        return f"{encoded_name} <{safe_email}>"


# A reply prefix (Re:/RE:) counts as already-present even behind leading list
# or ticket tags like ``[list]`` -- so inheriting a parent subject never
# gains a second Re:. Detection only; tags/prefixes are never stripped.
_REPLY_PREFIX_RE = re.compile(r"^\s*(?:\[[^\]]*\]\s*)*[Rr][Ee]\s*:")


def normalize_reply_subject(subject: str) -> str:
    """Return *subject* with exactly one leading ``Re:`` reply prefix.

    Prepends ``"Re: "`` only when *subject* is not already a reply. An existing
    ``Re:``/``RE:`` marker counts even when preceded by leading list/ticket tags
    (e.g. ``[list] Re: ...``), so a reply that inherits its parent's subject
    never gains a second ``Re:``. Existing prefixes and bracketed tags are kept
    verbatim (never stripped). Idempotent.
    """
    if _REPLY_PREFIX_RE.match(subject):
        return subject
    return f"Re: {subject}"


def _escape_body(text: str) -> str:
    """Escape body text for inclusion in HTML (Gmail entity rules)."""
    # html.escape covers & < > and (quote=True) " and '. Gmail emits &#39; for
    # the apostrophe; html.escape emits &#x27; -- normalize to match.
    escaped = _html.escape(text, quote=True)
    return escaped.replace("&#x27;", "&#39;")


def plain_body_to_html(text: str) -> str:
    """Convert a plain-text body to Gmail's per-line ``<div>`` HTML.

    Each line becomes ``<div>line</div>``; blank lines become ``<div><br></div>``.
    Returned value is the inner HTML (caller wraps it in the ltr container).
    """
    lines = text.split("\n")
    parts = []
    for line in lines:
        if line == "":
            parts.append("<div><br></div>")
        else:
            parts.append(f"<div>{_escape_body(line)}</div>")
    return "".join(parts)


def base_text_direction(text: str) -> str:
    """Return ``"rtl"`` or ``"ltr"`` for *text*'s base paragraph direction.

    Follows the Unicode Bidirectional Algorithm's first-strong-character rule:
    the direction is decided by the first character with a strong directional
    type -- ``R``/``AL`` (right-to-left scripts) yield ``"rtl"``, ``L`` yields
    ``"ltr"``. Leading bidi-neutral characters (whitespace, digits, punctuation,
    currency signs) are skipped. Text with no strong character defaults to
    ``"ltr"`` -- matching Gmail's own compose default and keeping left-to-right
    output byte-identical.
    """
    for ch in text:
        bidi = unicodedata.bidirectional(ch)
        if bidi == "L":
            return "ltr"
        if bidi in ("R", "AL"):
            return "rtl"
    return "ltr"


def new_message_html(body_html: str, direction: str = "ltr") -> str:
    """Wrap body HTML in Gmail's ``<div dir="...">`` container.

    ``direction`` is ``"ltr"`` (default, byte-identical to historical output) or
    ``"rtl"``. Callers resolve the value from :func:`base_text_direction` (or an
    explicit user choice) before wrapping. Embedded opposite-direction runs
    (e.g. Latin words or numerals inside a right-to-left body) render correctly
    via the browser's Unicode bidi algorithm regardless of the base.
    """
    return f'<div dir="{direction}">{body_html}</div>'


def _format_attribution_when(dt: datetime) -> str:
    """Format the ``On <Dow>, <D> <Mon> <YYYY> at <H>:<MM>`` clause.

    Day and hour are NOT zero-padded; minute IS zero-padded.
    """
    dow = _DOW[dt.weekday()]
    mon = _MON[dt.month - 1]
    return f"On {dow}, {dt.day} {mon} {dt.year} at {dt.hour}:{dt.minute:02d}"


def format_attribution_plain(name: str, email: str, dt: datetime) -> str:
    """Plain-text reply attribution line."""
    return f"{_format_attribution_when(dt)}, {name} <{email}> wrote:"


def format_attribution_html(name: str, email: str, dt: datetime) -> str:
    """HTML reply attribution div (the ``gmail_attr`` div, trailing ``<br>``)."""
    when = _format_attribution_when(dt)
    safe_name = _escape_body(name)
    safe_email = _html.escape(email)
    return (
        '<div dir="ltr" class="gmail_attr">'
        f"{when}, {safe_name} "
        f'&lt;<a href="mailto:{safe_email}">{safe_email}</a>&gt; wrote:<br></div>'
    )


def build_quote_plain(parent_text: str) -> str:
    """Prefix each parent line with Gmail's one-level ``> `` quote marker.

    Blank lines become a bare ``>`` (no trailing space). Inner quoting in the
    parent body is inherited verbatim (Gmail only adds its own one level).
    """
    out = []
    for line in parent_text.split("\n"):
        out.append(">" if line == "" else f"> {line}")
    return "\n".join(out)


def build_quote_html(parent_html: str) -> str:
    """Wrap the parent HTML body in Gmail's blockquote (no attribution div).

    The attribution div is assembled separately so the container can be built
    as: container-open + attribution + blockquote + container-close.
    """
    return (
        f'<blockquote class="gmail_quote" style="{BLOCKQUOTE_STYLE}">'
        f"{parent_html}</blockquote>"
    )


def build_quote_container_html(attribution_html: str, parent_html: str) -> str:
    """Assemble the full ``gmail_quote gmail_quote_container`` div for a reply."""
    return (
        '<div class="gmail_quote gmail_quote_container">'
        f"{attribution_html}"
        f"{build_quote_html(parent_html)}"
        "</div>"
    )


def render_forward_recipients_html(raw_header: str) -> str:
    """Render a raw address-list header (To/Cc) as Gmail-web forwarded-header HTML.

    Each recipient becomes ``{name} &lt;<a href="mailto:addr">addr</a>&gt;`` when it
    carries a display name, or a bare ``<a href="mailto:addr">addr</a>`` when it does
    not (matching Gmail's forwarded To: rendering). Names and addresses are HTML-escaped.
    """
    from email.utils import getaddresses

    parts = []
    for name, email in getaddresses([raw_header or ""]):
        if not email:
            continue
        esc_email = _html.escape(email)
        link = f'<a href="mailto:{esc_email}">{esc_email}</a>'
        if name and name.strip():
            parts.append(f"{_escape_body(name)} &lt;{link}&gt;")
        else:
            parts.append(link)
    return ", ".join(parts)


def build_forwarded_container_html(
    from_name: Optional[str],
    from_email: str,
    date_str: str,
    subject: str,
    to_rendered: str,
    orig_html: str,
) -> str:
    """Assemble the Gmail-web ``gmail_quote_container`` div for a forwarded message.

    The attribution block follows Gmail's exact forward header layout:
    a ``---------- Forwarded message ---------`` separator followed by
    From/Date/Subject/To lines inside a ``gmail_attr`` div.  No blockquote
    is used — forwarded content is embedded directly after the attr div.

    Args:
        from_name: Sender display name; empty/whitespace → bare-email rendering.
        from_email: Sender email address.
        date_str: Pre-formatted date string (passed through verbatim).
        subject: Email subject (HTML-escaped by this function).
        to_rendered: Pre-rendered recipient HTML (inserted verbatim).
        orig_html: Original message HTML body (inserted verbatim).

    Returns:
        A string containing the full forwarded container div.
    """
    safe_email = _html.escape(from_email)
    safe_subject = _escape_body(subject)

    if from_name and from_name.strip():
        safe_name = _escape_body(from_name)
        from_field = (
            f'<strong class="gmail_sendername" dir="auto">{safe_name}</strong> '
            f'<span dir="auto">&lt;<a href="mailto:{safe_email}">{safe_email}</a>&gt;</span>'
        )
    else:
        from_field = f'<span dir="auto">&lt;<a href="mailto:{safe_email}">{safe_email}</a>&gt;</span>'

    attr_div = (
        '<div dir="ltr" class="gmail_attr">'
        "---------- Forwarded message ---------<br>"
        f"From: {from_field}<br>"
        f"Date: {_escape_body(date_str)}<br>"
        f"Subject: {safe_subject}<br>"
        f"To: {to_rendered}<br>"
        "</div>"
    )

    return f'<div class="gmail_quote gmail_quote_container">{attr_div}{orig_html}</div>'


def build_forwarded_plain(
    from_name: Optional[str],
    from_email: str,
    date_str: str,
    subject: str,
    to_rendered_plain: str,
    orig_plain: str,
) -> str:
    """Assemble the plain-text body for a forwarded message.

    Follows Gmail's exact plain-text forward layout: a separator line,
    From/Date/Subject/To header lines, a blank line, then the original body
    verbatim (NOT ``> ``-quoted).

    Args:
        from_name: Sender display name; empty/whitespace → bare email only.
        from_email: Sender email address.
        date_str: Pre-formatted date string.
        subject: Email subject (not escaped — plain text context).
        to_rendered_plain: Pre-rendered plain-text recipient string.
        orig_plain: Original message plain-text body (inserted verbatim).

    Returns:
        A plain-text string with the forwarded message structure.
    """
    if from_name and from_name.strip():
        from_field = f"{from_name} <{from_email}>"
    else:
        from_field = from_email

    return (
        "---------- Forwarded message ---------\n"
        f"From: {from_field}\n"
        f"Date: {date_str}\n"
        f"Subject: {subject}\n"
        f"To: {to_rendered_plain}\n"
        "\n"
        f"{orig_plain}"
    )


def _qp_encode(text: str) -> str:
    """Quoted-printable encode text with CRLF line endings (76-col soft wrap)."""
    # Normalize to CRLF so quopri's soft-wrapping operates on canonical lines.
    raw = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    encoded = quopri.encodestring(raw)
    return encoded.decode("ascii")


def choose_cte(text: str) -> str:
    """Choose Content-Transfer-Encoding for a text body part.

    Returns ``"base64"`` if base64 encoding of the UTF-8 bytes is strictly
    smaller than quoted-printable; otherwise returns ``"quoted-printable"``.

    This is a size-minimizing rule that approximates Gmail's per-part CTE
    selection: mostly-ASCII/Latin bodies favour QP (since base64 inflates
    ASCII by ~33 %), while heavily non-ASCII bodies (e.g. Hebrew, CJK) favour
    base64 because QP expands every non-ASCII byte to ``=XX`` (3 bytes each).
    QP wins all ties and the all-ASCII case.

    The computation is O(len(text)) with no materialisation of full output —
    just length arithmetic on the UTF-8 byte sequence.
    """
    raw = text.encode("utf-8")
    if not raw:
        return "quoted-printable"

    # --- QP size estimate ---
    # quopri encodes each byte that is not a printable ASCII safe-char as =XX
    # (3 bytes).  Safe bytes are kept as-is (1 byte), but long lines get a
    # soft-wrap ``=\r\n`` (3 bytes) every 75 content bytes.  We use a conservative
    # per-byte count: non-printable/non-safe → 3, otherwise 1; then add ~4 %
    # overhead for soft line-wraps (worst case one wrap per 75 bytes → 3 extra).
    # In practice the dominant factor is the =XX expansion, so this is accurate
    # enough to pick the same winner as computing `len(quopri.encodestring(raw))`.
    _QP_SAFE = frozenset(b" \t\r\n" + bytes(range(33, 127))) - {ord("=")}
    qp_bytes = sum(1 if b in _QP_SAFE else 3 for b in raw)
    # Add soft-wrap overhead: one `=\r\n` per 75 output bytes of content.
    qp_size = qp_bytes + (qp_bytes // 75) * 3

    # --- base64 size estimate ---
    # base64 expands 3 raw bytes → 4 chars; lines are wrapped at 76 cols with
    # CRLF (2 bytes).  Padding rounds up to the next multiple of 3.
    b64_chars = ((len(raw) + 2) // 3) * 4
    b64_lines = (b64_chars + 75) // 76  # number of CRLF line endings
    b64_size = b64_chars + b64_lines * 2

    return "base64" if b64_size < qp_size else "quoted-printable"


def _b64_text(text: str) -> str:
    """Base64-encode a text string (UTF-8) with CRLF-wrapped 76-col lines."""
    return _b64_crlf(text.encode("utf-8"))


def _alternative_parts(plain_text: str, html_text: str, boundary: str) -> str:
    """Return the body of a ``multipart/alternative`` subtree (no top-level headers).

    Emits the two body parts (text/plain + text/html, UTF-8) bounded by
    ``boundary``, suitable for use both as a standalone message body and as a
    child part within a ``multipart/mixed`` message.

    The Content-Transfer-Encoding for each part is chosen independently via
    :func:`choose_cte`: mostly-ASCII/Latin bodies use ``quoted-printable``
    (preserving existing behaviour byte-for-byte); heavily non-ASCII bodies
    (e.g. Hebrew, CJK) switch to ``base64`` where it is more compact.
    """
    crlf = "\r\n"

    def _part(content_type: str, body: str) -> str:
        cte = choose_cte(body)
        encoded = _qp_encode(body) if cte == "quoted-printable" else _b64_text(body)
        return crlf.join(
            [
                f"--{boundary}",
                f'Content-Type: {content_type}; charset="UTF-8"',
                f"Content-Transfer-Encoding: {cte}",
                "",
                encoded,
            ]
        )

    plain_part = _part("text/plain", plain_text)
    html_part = _part("text/html", html_text)
    closing = f"--{boundary}--"
    return crlf.join([plain_part, html_part, closing, ""])


def assemble_alternative(
    headers: List[Tuple[str, str]],
    plain_text: str,
    html_text: str,
    boundary: str,
) -> str:
    """Assemble a ``multipart/alternative`` message as a raw RFC5322 string.

    ``headers`` is an ordered list of (name, value) pairs authored exactly as the
    spec dictates (the caller controls order and which optional headers appear).
    The ``Content-Type`` header for the top-level multipart is appended here so
    its boundary always matches ``boundary``.

    Both body parts use ``charset="UTF-8"`` (uppercase, double-quoted).  The
    ``Content-Transfer-Encoding`` for each part is chosen independently via
    :func:`choose_cte` — ``quoted-printable`` for ASCII/Latin content,
    ``base64`` for heavily non-ASCII content (e.g. Hebrew, CJK).  Returns the
    message as a string with CRLF separators (ready for base64url encoding).
    """
    crlf = "\r\n"
    lines: List[str] = [f"{name}: {value}" for name, value in headers]
    lines.append(f'Content-Type: multipart/alternative; boundary="{boundary}"')
    head = crlf.join(lines)
    return crlf.join([head, "", _alternative_parts(plain_text, html_text, boundary)])


def _strip_header_controls(value: str) -> str:
    """Remove CR/LF/NUL so a value can't inject extra MIME header lines."""
    return value.replace("\r", "").replace("\n", "").replace("\x00", "")


def _escape_filename(filename: str) -> str:
    """RFC 2045 quoted-string escaping for an ASCII filename.

    Strips CR/LF/NUL first (header-injection defense, mirroring
    :func:`format_display_address`), then escapes backslash and double-quote.
    """
    safe = _strip_header_controls(filename)
    return safe.replace("\\", "\\\\").replace('"', '\\"')


def _mime_filename_params(filename: str) -> Tuple[str, str]:
    """Return ``(name_param, filename_param)`` for a MIME part's headers.

    ASCII filenames use the historical quoted-string form (``name="f.pdf"``),
    kept byte-identical to prior output. Non-ASCII filenames are RFC 2231
    encoded (``name*=UTF-8''caf%C3%A9.pdf``) so raw UTF-8 never lands in a
    header. CR/LF/NUL are stripped either way.
    """
    try:
        filename.encode("ascii")
    except UnicodeEncodeError:
        enc = encode_rfc2231(_strip_header_controls(filename), "UTF-8")
        return f"name*={enc}", f"filename*={enc}"
    esc = _escape_filename(filename)
    return f'name="{esc}"', f'filename="{esc}"'


def _b64_crlf(data: bytes) -> str:
    """Base64-encode *data* with CRLF line breaks and no trailing newline."""
    crlf = "\r\n"
    b64 = base64.encodebytes(data).decode("ascii")
    # encodebytes wraps at 76 cols with a trailing newline; strip it so there
    # is no empty line between the payload and the next boundary delimiter.
    return b64.rstrip("\n").replace("\n", crlf)


def _attachment_part(
    outer_boundary: str, filename: str, mime_type: str, data: bytes
) -> str:
    """Render one ``Content-Disposition: attachment`` MIME part."""
    crlf = "\r\n"
    name_param, filename_param = _mime_filename_params(filename)
    return crlf.join(
        [
            f"--{outer_boundary}",
            f"Content-Type: {mime_type}; {name_param}",
            "Content-Transfer-Encoding: base64",
            f"Content-Disposition: attachment; {filename_param}",
            "",
            _b64_crlf(data),
        ]
    )


def _inline_part(
    outer_boundary: str, filename: str, mime_type: str, data: bytes, content_id: str
) -> str:
    """Render one ``Content-Disposition: inline`` MIME part (cid image)."""
    crlf = "\r\n"
    name_param, filename_param = _mime_filename_params(filename)
    # Strip CR/LF/NUL from content_id (header-injection defense), then wrap in
    # angle brackets if not already wrapped.
    safe_cid = _strip_header_controls(content_id)
    cid = safe_cid if safe_cid.startswith("<") else f"<{safe_cid}>"
    return crlf.join(
        [
            f"--{outer_boundary}",
            f"Content-Type: {mime_type}; {name_param}",
            "Content-Transfer-Encoding: base64",
            f"Content-ID: {cid}",
            f"Content-Disposition: inline; {filename_param}",
            "",
            _b64_crlf(data),
        ]
    )


def _related_body(
    plain_text: str,
    html_text: str,
    inline_parts: List[dict],
    boundary_related: str,
    boundary_alt: str,
) -> str:
    """Return the body of a ``multipart/related`` subtree (no top-level headers).

    Structure: ``multipart/alternative`` child + one inline part per entry in
    *inline_parts*.
    """
    crlf = "\r\n"
    alt_header = f'Content-Type: multipart/alternative; boundary="{boundary_alt}"'
    alt_body = _alternative_parts(plain_text, html_text, boundary_alt)
    alt_part = crlf.join([f"--{boundary_related}", alt_header, "", alt_body])

    inline = [
        _inline_part(
            boundary_related,
            ip["filename"],
            ip["mime_type"],
            ip["data"],
            ip["content_id"],
        )
        for ip in inline_parts
    ]

    closing = f"--{boundary_related}--"
    return crlf.join([alt_part] + inline + [closing, ""])


def assemble_related(
    headers: List[Tuple[str, str]],
    plain_text: str,
    html_text: str,
    inline_parts: List[dict],
    boundary_related: str,
    boundary_alt: str,
) -> str:
    """Assemble a ``multipart/related`` message as a raw RFC5322 string.

    Structure: ``multipart/related`` → [``multipart/alternative``, inline...].

    Args:
        headers: Ordered (name, value) pairs (From, To, Subject, etc.).
        plain_text: Plain-text body.
        html_text: HTML body.
        inline_parts: List of ``{"filename": str, "mime_type": str, "data": bytes, "content_id": str}``.
        boundary_related: Boundary for the outer multipart/related.
        boundary_alt: Boundary for the inner multipart/alternative.

    Returns:
        Raw RFC5322 message string with CRLF separators.
    """
    crlf = "\r\n"
    lines: List[str] = [f"{name}: {value}" for name, value in headers]
    lines.append(f'Content-Type: multipart/related; boundary="{boundary_related}"')
    head = crlf.join(lines)
    return crlf.join(
        [
            head,
            "",
            _related_body(
                plain_text, html_text, inline_parts, boundary_related, boundary_alt
            ),
        ]
    )


def assemble_mixed(
    headers: List[Tuple[str, str]],
    plain_text: str,
    html_text: str,
    attachments: List[dict],
    boundary_mixed: str,
    boundary_alt: str,
) -> str:
    """Assemble a ``multipart/mixed`` message as a raw RFC5322 string.

    Structure matches Gmail-web's format for forwarded messages with attachments:
    ``multipart/mixed`` → [``multipart/alternative`` (plain + html)] + one part
    per attachment.

    Args:
        headers: Ordered (name, value) pairs (From, To, Subject, etc.).
        plain_text: Plain-text body.
        html_text: HTML body.
        attachments: List of ``{"filename": str, "mime_type": str, "data": bytes}``.
        boundary_mixed: Boundary string for the outer multipart/mixed.
        boundary_alt: Boundary string for the inner multipart/alternative child.

    Returns:
        Raw RFC5322 message string with CRLF separators.
    """
    return assemble_web_message(
        headers,
        plain_text,
        html_text,
        inline_parts=None,
        attachment_parts=attachments,
        boundary_alt=boundary_alt,
        boundary_related=None,
        boundary_mixed=boundary_mixed,
    )


def assemble_web_message(
    headers: List[Tuple[str, str]],
    plain_text: str,
    html_text: str,
    *,
    inline_parts: Optional[List[dict]] = None,
    attachment_parts: Optional[List[dict]] = None,
    boundary_alt: str,
    boundary_related: Optional[str] = None,
    boundary_mixed: Optional[str] = None,
) -> str:
    """Assemble a Gmail-web-faithful MIME message as a raw RFC5322 string.

    Selects the smallest sufficient MIME structure based on the presence of
    inline images and/or attachments:

    - No inline, no attachments → ``multipart/alternative``
    - Inline only → ``multipart/related`` → [alternative, inline...]
    - Attachments only → ``multipart/mixed`` → [alternative, attachments...]
    - Inline AND attachments → ``multipart/mixed`` → [``multipart/related`` → [alternative, inline...], attachments...]

    Args:
        headers: Ordered (name, value) pairs (From, To, Subject, etc.).
        plain_text: Plain-text body.
        html_text: HTML body.
        inline_parts: List of ``{"filename": str, "mime_type": str, "data": bytes, "content_id": str}``.
        attachment_parts: List of ``{"filename": str, "mime_type": str, "data": bytes}``.
        boundary_alt: Boundary for the innermost multipart/alternative.
        boundary_related: Boundary for multipart/related (required when inline_parts present).
        boundary_mixed: Boundary for the outermost multipart/mixed (required when
            attachment_parts present, or when both inline and attachments present).

    Returns:
        Raw RFC5322 message string with CRLF separators.
    """
    crlf = "\r\n"
    has_inline = bool(inline_parts)
    has_attach = bool(attachment_parts)

    if has_inline and boundary_related is None:
        raise ValueError("boundary_related is required when inline_parts is provided")
    if has_attach and boundary_mixed is None:
        raise ValueError("boundary_mixed is required when attachment_parts is provided")

    def _head(content_type: str) -> str:
        lines: List[str] = [f"{name}: {value}" for name, value in headers]
        lines.append(f"Content-Type: {content_type}")
        return crlf.join(lines)

    if not has_inline and not has_attach:
        # Pure alternative
        return assemble_alternative(headers, plain_text, html_text, boundary_alt)

    if has_inline and not has_attach:
        # multipart/related at top
        head = _head(f'multipart/related; boundary="{boundary_related}"')
        body = _related_body(
            plain_text, html_text, inline_parts, boundary_related, boundary_alt
        )
        return crlf.join([head, "", body])

    if not has_inline and has_attach:
        # multipart/mixed at top, alternative + attachments
        head = _head(f'multipart/mixed; boundary="{boundary_mixed}"')
        alt_header = f'Content-Type: multipart/alternative; boundary="{boundary_alt}"'
        alt_body = _alternative_parts(plain_text, html_text, boundary_alt)
        alt_part = crlf.join([f"--{boundary_mixed}", alt_header, "", alt_body])
        attach = [
            _attachment_part(
                boundary_mixed, ap["filename"], ap["mime_type"], ap["data"]
            )
            for ap in attachment_parts
        ]
        closing = f"--{boundary_mixed}--"
        return crlf.join([head, "", alt_part] + attach + [closing, ""])

    # Both inline AND attachments:
    # multipart/mixed → [multipart/related → [alternative, inline...], attachments...]
    head = _head(f'multipart/mixed; boundary="{boundary_mixed}"')
    related_header = f'Content-Type: multipart/related; boundary="{boundary_related}"'
    related_body = _related_body(
        plain_text, html_text, inline_parts, boundary_related, boundary_alt
    )
    related_part = crlf.join([f"--{boundary_mixed}", related_header, "", related_body])
    attach = [
        _attachment_part(boundary_mixed, ap["filename"], ap["mime_type"], ap["data"])
        for ap in attachment_parts
    ]
    closing = f"--{boundary_mixed}--"
    return crlf.join([head, "", related_part] + attach + [closing, ""])


def encode_raw(message: str) -> str:
    """Base64url-encode an assembled message for the Gmail send API."""
    return base64.urlsafe_b64encode(message.encode("utf-8")).decode("ascii")
