"""Pure helpers for building Gmail-web faithful MIME content.

These functions are synchronous and side-effect free so they can be unit-tested
in isolation. Network-bound work (fetching the parent or forwarded message)
happens in the async Gmail tools, which feed the resulting strings into these
builders and into ``gmail_helpers._prepare_gmail_message_web``.
"""

from __future__ import annotations

import base64
import html as _html
import quopri
import re
import secrets
import unicodedata
from datetime import datetime
from email.errors import HeaderParseError, InvalidHeaderDefect
from email.header import Header
from email.headerregistry import HeaderRegistry
from email.policy import SMTP
from email.utils import encode_rfc2231, formataddr, getaddresses
from typing import Iterable, List, Literal, Optional, Tuple

TextDirection = Literal["auto", "ltr", "rtl"]
AddressGroup = Tuple[Optional[str], List[Tuple[str, str]]]

# Gmail's byte-identical blockquote style string for quoted replies.
BLOCKQUOTE_STYLE = (
    "margin:0px 0px 0px 0.8ex;border-left:1px solid rgb(204,204,204);padding-left:1ex"
)

# Three-letter day/month names matching Gmail's attribution line.
_DOW = "Mon Tue Wed Thu Fri Sat Sun".split()
_MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

_ADDRESS_HEADER = HeaderRegistry()
_PHRASE_SPECIALS = frozenset('()<>[]:;@\\,."')
_FORWARD_SEPARATOR = "---------- Forwarded message ---------"


def _new_attachment_id() -> str:
    """Return a random attachment id in the shape Gmail web uses (``f_...``)."""
    return f"f_{secrets.token_hex(5)}"


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
    - Non-ASCII or long names are RFC2047 encoded-word wrapped.
    - Names with commas/specials are RFC5322 quoted.

    ``formataddr`` handles quoting; ``Header`` supplies encoded words.
    """
    # Strip CR/LF/NUL from the address too: formataddr does not sanitize it, so
    # an unvalidated CRLF-bearing address would be RFC5322 header injection.
    safe_email = _strip_header_controls(email)
    if not name or not name.strip():
        return safe_email
    safe_name = _strip_header_controls(name)
    if safe_name.isascii() and len(safe_name) <= 75:
        # ASCII name: formataddr applies RFC5322 quoting when needed.
        return formataddr((safe_name, safe_email))
    # Encoded words also allow long, unbroken display names to fold safely.
    encoded_name = Header(safe_name, "utf-8").encode(maxlinelen=75, linesep="\r\n")
    return f"{encoded_name} <{safe_email}>"


def format_address_list(value: str) -> str:
    """RFC 2047 encode non-ASCII display names in a To/Cc/Bcc address list.

    The list is always validated (a malformed entry raises ``ValueError``). A
    Valid ASCII values within the line limit are returned unchanged; otherwise
    each address is reformatted with ``format_display_address``.
    """
    groups = parse_address_list(value)
    if value.isascii() and len(value) <= 998:
        return value
    return join_address_groups(
        (group_name, [format_display_address(n, a) for n, a in members])
        for group_name, members in groups
    )


def join_address_groups(groups: Iterable[Tuple[Optional[str], List[str]]]) -> str:
    """Serialize ``(group_name, [formatted mailbox, ...])`` as an address list.

    Ungrouped mailboxes (``group_name`` None) are joined as-is; a named group is
    emitted as ``Name: a, b;`` with its label quoted or RFC 2047 encoded.
    """
    out: List[str] = []
    for group_name, members in groups:
        formatted = ", ".join(members)
        if group_name is None:
            out.append(formatted)
        else:
            sep = ": " if formatted else ":"
            out.append(f"{_format_group_name(group_name)}{sep}{formatted};")
    return ", ".join(out)


def parse_address_list(value: str) -> List[AddressGroup]:
    """Parse an RFC 5322 address list into ``(group_name, [(name, addr), ...])``.

    Ungrouped mailboxes come back as ``(None, [(name, addr)])``. Empty list
    elements (``a@x.com,,`` or a trailing comma) are skipped; any malformed
    entry (a name with no address, an unclosed ``<``) raises ``ValueError``
    instead of being silently dropped, so a recipient is never lost.
    """
    try:
        header = _ADDRESS_HEADER("To", value)
    except (IndexError, HeaderParseError) as exc:
        # The stdlib parser raises instead of recording a defect on some
        # truncated input (e.g. "José <" on Python 3.11).
        raise ValueError(f"Invalid address list: could not parse {value!r}.") from exc
    if any(isinstance(d, InvalidHeaderDefect) for d in header.defects):
        raise ValueError(f"Invalid address list: could not parse {value!r}.")
    groups: List[AddressGroup] = []
    for group in header.groups:
        members = [(a.display_name, a.addr_spec) for a in group.addresses]
        if any(not addr for _name, addr in members):
            raise ValueError(f"Invalid address list: empty address in {value!r}.")
        groups.append((group.display_name, members))
    if not groups:
        raise ValueError(f"Invalid address list: no address in {value!r}.")
    return groups


def _format_group_name(name: str) -> str:
    """Render an RFC 5322 group display name (quoted or RFC 2047 encoded)."""
    safe = _strip_header_controls(name)
    if not safe.isascii() or len(safe) > 75:
        return Header(safe, "utf-8").encode(maxlinelen=75, linesep="\r\n")
    if any(c in _PHRASE_SPECIALS for c in safe):
        return '"' + safe.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return safe


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
    lines = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs().split("\n")
    parts = []
    for line in lines:
        if line == "":
            parts.append("<div><br></div>")
        else:
            # HTML collapses ASCII spaces; preserve indentation and spacing.
            escaped = re.sub(
                r"^ +| +$| {2,}",
                lambda match: "&nbsp;" * len(match.group()),
                _escape_body(line),
            )
            parts.append(f"<div>{escaped}</div>")
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


def format_gmail_date(dt: datetime) -> str:
    """Format ``<Dow>, <D> <Mon> <YYYY> at <H>:<MM>`` as Gmail web renders dates.

    Day and hour are NOT zero-padded; minute IS zero-padded.
    """
    dow = _DOW[dt.weekday()]
    mon = _MON[dt.month - 1]
    return f"{dow}, {dt.day} {mon} {dt.year} at {dt.hour}:{dt.minute:02d}"


def _format_attribution_when(dt: datetime) -> str:
    """Format the ``On <Dow>, <D> <Mon> <YYYY> at <H>:<MM>`` clause."""
    return f"On {format_gmail_date(dt)}"


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


def _render_recipients_html(raw_header: str) -> str:
    """Render a raw To/Cc header the way Gmail's forwarded block shows it.

    Each recipient becomes ``Name &lt;<a href="mailto:addr">addr</a>&gt;``, or
    just the link when it has no display name.
    """
    parts = []
    for name, email in getaddresses([raw_header]):
        if not email:
            continue
        safe_email = _html.escape(email)
        link = f'<a href="mailto:{safe_email}">{safe_email}</a>'
        parts.append(f"{_escape_body(name)} &lt;{link}&gt;" if name.strip() else link)
    return ", ".join(parts)


def build_forwarded_container_html(
    from_name: str, from_email: str, date: str, subject: str, to: str, orig_html: str
) -> str:
    """Assemble Gmail's ``gmail_quote_container`` for a forwarded message.

    The ``gmail_attr`` header block (separator, From, Date, Subject, To) is
    followed directly by the original HTML; unlike a reply there is no
    blockquote. ``from_name`` or ``from_email`` may be empty; ``to`` is the raw
    header value and ``orig_html`` is inserted verbatim.
    """
    from_fields = []
    if from_name:
        from_fields.append(
            f'<strong class="gmail_sendername" dir="auto">{_escape_body(from_name)}</strong>'
        )
    if from_email:
        safe_email = _html.escape(from_email)
        from_fields.append(
            f'<span dir="auto">&lt;<a href="mailto:{safe_email}">{safe_email}</a>&gt;</span>'
        )
    attr_div = (
        '<div dir="ltr" class="gmail_attr">'
        f"{_FORWARD_SEPARATOR}<br>"
        f"From: {' '.join(from_fields)}<br>"
        f"Date: {_escape_body(date)}<br>"
        f"Subject: {_escape_body(subject)}<br>"
        f"To: {_render_recipients_html(to)}<br>"
        "</div>"
    )
    return f'<div class="gmail_quote gmail_quote_container">{attr_div}{orig_html}</div>'


def build_forwarded_plain(
    from_name: str, from_email: str, date: str, subject: str, to: str, orig_plain: str
) -> str:
    """Assemble Gmail's plain-text forwarded block.

    The header lines are followed by a blank line and the original body
    verbatim (not ``> ``-quoted).
    """
    if from_name and from_email:
        from_field = f"{from_name} <{from_email}>"
    else:
        from_field = from_name or from_email
    return (
        f"{_FORWARD_SEPARATOR}\n"
        f"From: {from_field}\n"
        f"Date: {date}\n"
        f"Subject: {subject}\n"
        f"To: {to}\n"
        "\n"
        f"{orig_plain}"
    )


def _qp_encode(text: str) -> str:
    """Quoted-printable encode text with CRLF line endings (76-col soft wrap)."""
    # Normalize to CRLF so quopri's soft-wrapping operates on canonical lines.
    # quopri passes a bare CR through unencoded, so it becomes a line break too
    # (as EmailMessage does) rather than an RFC 5322-invalid lone CR on the wire.
    lines = text.replace("\r\n", "\n").replace("\r", "\n")
    encoded = quopri.encodestring(lines.replace("\n", "\r\n").encode("utf-8"))
    # binascii.b2a_qp emits bare-LF soft breaks ("=\n") when the input has no
    # CRLF to imitate (a single-line HTML part); canonicalize those to CRLF.
    return encoded.decode("ascii").replace("\r\n", "\n").replace("\n", "\r\n")


def choose_cte(text: str) -> str:
    """Choose the smaller Content-Transfer-Encoding for a text body part.

    Mirrors Gmail's per-part choice: mostly-ASCII/Latin bodies come out
    smaller as ``quoted-printable`` (which wins ties), heavily non-ASCII bodies
    (Hebrew, CJK) as ``base64``.
    """
    if len(_b64_text(text)) < len(_qp_encode(text)):
        return "base64"
    return "quoted-printable"


def _b64_text(text: str) -> str:
    """Base64-encode a text string (UTF-8) with CRLF-wrapped 76-col lines."""
    return _b64_crlf(text.encode("utf-8"))


def _alternative_parts(plain_text: str, html_text: str, boundary: str) -> str:
    """Return the text/plain and text/html parts bounded by ``boundary``.

    Each part's Content-Transfer-Encoding is chosen by :func:`choose_cte`.
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


def _render_headers(headers: List[Tuple[str, str]]) -> List[str]:
    """Fold top-level headers to 76 columns, rejecting any line over 998 bytes."""
    crlf = "\r\n"
    policy = SMTP.clone(max_line_length=76)
    lines: List[str] = []
    for name, value in headers:
        if name == "Subject":
            lines.append(policy.fold(name, value).removesuffix(crlf))
        else:
            # Refolding address headers through HeaderRegistry can insert
            # literal spaces between adjacent encoded words on Python 3.12.
            folded = Header(value, header_name=name).encode(maxlinelen=76, linesep=crlf)
            lines.append(f"{name}: {folded}")
    if any(
        len(line.encode("utf-8")) > 998
        for header in lines
        for line in header.split(crlf)
    ):
        raise ValueError("Header contains a token too long to fit an RFC 5322 line.")
    return lines


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

    Both body parts use ``charset="UTF-8"`` (uppercase, double-quoted). Returns
    the message with CRLF separators, ready for :func:`encode_raw`.
    """
    crlf = "\r\n"
    lines = _render_headers(headers)
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


_MIME_TYPE_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*"
)


def _safe_mime_type(mime_type: Optional[str]) -> str:
    """Return *mime_type* trimmed if it is a bare ``type/subtype`` token.

    Anything else (empty, parameters, CR/LF header injection) becomes
    ``application/octet-stream`` so a caller value cannot add header lines.
    """
    value = (mime_type or "").strip()
    return value if _MIME_TYPE_RE.fullmatch(value) else "application/octet-stream"


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
    """Render one ``Content-Disposition: attachment`` MIME part.

    Header set and order match Gmail web: a random ``X-Attachment-Id`` with a
    matching ``Content-ID`` on every regular attachment.
    """
    crlf = "\r\n"
    name_param, filename_param = _mime_filename_params(filename)
    attachment_id = _new_attachment_id()
    return crlf.join(
        [
            f"--{outer_boundary}",
            f"Content-Type: {_safe_mime_type(mime_type)}; {name_param}",
            f"Content-Disposition: attachment; {filename_param}",
            "Content-Transfer-Encoding: base64",
            f"Content-ID: <{attachment_id}>",
            f"X-Attachment-Id: {attachment_id}",
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
            f"Content-Type: {_safe_mime_type(mime_type)}; {name_param}",
            "Content-Transfer-Encoding: base64",
            f"Content-ID: {cid}",
            f"X-Attachment-Id: {cid[1:-1]}",
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
    lines = _render_headers(headers)
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
        lines = _render_headers(headers)
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
