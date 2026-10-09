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
import secrets
import unicodedata
from datetime import datetime
from email.header import Header
from email.errors import HeaderParseError, InvalidHeaderDefect
from email.headerregistry import HeaderRegistry
from email.utils import formataddr
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
        encoded_name = Header(safe_name, "utf-8").encode(maxlinelen=998, linesep="\r\n")
        return f"{encoded_name} <{safe_email}>"


def format_address_list(value: str) -> str:
    """RFC 2047 encode non-ASCII display names in a To/Cc/Bcc address list.

    The list is always validated (a malformed entry raises ``ValueError``). A
    valid all-ASCII value is then returned unchanged (byte-identical to the
    input); otherwise each address is reformatted with ``format_display_address``.
    """
    groups = parse_address_list(value)
    if value.isascii():
        return value
    out: List[str] = []
    for group_name, members in groups:
        formatted = ", ".join(format_display_address(n, a) for n, a in members)
        if group_name is None:
            out.append(formatted)
        else:
            sep = ": " if formatted else ":"
            out.append(f"{_format_group_name(group_name)}{sep}{formatted};")
    return ", ".join(out)


AddressGroup = Tuple[Optional[str], List[Tuple[str, str]]]


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
    if not safe.isascii():
        return Header(safe, "utf-8").encode(maxlinelen=998, linesep="\r\n")
    if any(c in _PHRASE_SPECIALS for c in safe):
        return '"' + safe.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return safe


_ADDRESS_HEADER = HeaderRegistry()
_PHRASE_SPECIALS = frozenset('()<>[]:;@\\,."')


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


def _qp_encode(text: str) -> str:
    """Quoted-printable encode text with CRLF line endings (76-col soft wrap)."""
    # Normalize to CRLF so quopri's soft-wrapping operates on canonical lines.
    raw = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    encoded = quopri.encodestring(raw).decode("ascii")
    # binascii.b2a_qp emits bare-LF soft breaks ("=\n") when the input has no
    # CRLF to imitate (a single-line HTML part). A data CR is always encoded as
    # =0D, so every raw LF is a line ending and can be canonicalized to CRLF.
    return encoded.replace("\r\n", "\n").replace("\n", "\r\n")


def choose_cte(text: str) -> str:
    """Choose Content-Transfer-Encoding for a text body part.

    Returns ``"base64"`` if base64 encoding of the UTF-8 bytes is strictly
    smaller than quoted-printable; otherwise returns ``"quoted-printable"``.

    This is a size-minimizing rule that approximates Gmail's per-part CTE
    selection: mostly-ASCII/Latin bodies favour QP (since base64 inflates
    ASCII by ~33 %), while heavily non-ASCII bodies (e.g. Hebrew, CJK) favour
    base64 because QP expands every non-ASCII byte to ``=XX`` (3 bytes each).
    QP wins all ties and the all-ASCII case.

    The computation is O(len(text)) with no materialisation of full output -
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
    :func:`choose_cte` - ``quoted-printable`` for ASCII/Latin content,
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


def _b64_crlf(data: bytes) -> str:
    """Base64-encode *data* with CRLF line breaks and no trailing newline."""
    crlf = "\r\n"
    b64 = base64.encodebytes(data).decode("ascii")
    # encodebytes wraps at 76 cols with a trailing newline; strip it so there
    # is no empty line between the payload and the next boundary delimiter.
    return b64.rstrip("\n").replace("\n", crlf)


def encode_raw(message: str) -> str:
    """Base64url-encode an assembled message for the Gmail send API."""
    return base64.urlsafe_b64encode(message.encode("utf-8")).decode("ascii")
