"""Tests for the golden-skeleton extractor and HTML sanitizer.

All fixtures use synthetic data only (example.com addresses, no personal data).
"""

import email.mime.multipart
import email.mime.text

from tools.golden_skeleton import (
    extract_skeleton,
    sanitize_html,
    _plain_line_structure,
    _Sanitizer,
)


def _make_raw(html: str, plain: str = "body") -> bytes:
    """Build a minimal multipart/alternative raw MIME message."""
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg.attach(email.mime.text.MIMEText(plain, "plain", "utf-8"))
    msg.attach(email.mime.text.MIMEText(html, "html", "utf-8"))
    return msg.as_bytes()


RAW = (
    b"MIME-Version: 1.0\r\nFrom: a@example.com\r\nSubject: Re: hi\r\n"
    b'Content-Type: multipart/alternative; boundary="000000000000abcdef0123456789"\r\n\r\n'
    b'--000000000000abcdef0123456789\r\nContent-Type: text/plain; charset="UTF-8"\r\n'
    b"Content-Transfer-Encoding: quoted-printable\r\n\r\nhi\r\n"
    b'--000000000000abcdef0123456789\r\nContent-Type: text/html; charset="UTF-8"\r\n'
    b'Content-Transfer-Encoding: quoted-printable\r\n\r\n<div dir="ltr">hi</div>\r\n'
    b"--000000000000abcdef0123456789--\r\n"
)


def test_extract_skeleton_mime_tree_and_headers():
    sk = extract_skeleton(RAW)
    assert sk["headers_order"][0] == "MIME-Version"
    assert sk["mime_tree"][0]["content_type"] == "multipart/alternative"
    cts = [p["content_type"] for p in sk["mime_tree"][0]["parts"]]
    assert cts == ["text/plain", "text/html"]


def test_sanitize_html_drops_text_and_redacts_addr():
    out = sanitize_html(
        '<div class="gmail_attr">On X, Joe &lt;'
        '<a href="mailto:j@example.com">j@example.com</a>&gt; wrote:</div>'
    )
    assert "Joe" not in out and "j@example.com" not in out
    assert 'class="gmail_attr"' in out


def test_sanitize_html_redacts_gmail_sendername_text():
    """Inner text of gmail_sendername elements must be dropped."""
    out = sanitize_html('<strong class="gmail_sendername">Alice Smith</strong>')
    assert "Alice" not in out
    assert "Smith" not in out
    assert 'class="gmail_sendername"' in out


def test_extract_skeleton_html_probes():
    raw = (
        b"MIME-Version: 1.0\r\nFrom: b@example.com\r\n"
        b'Content-Type: text/html; charset="UTF-8"\r\n\r\n'
        b'<div class="gmail_quote gmail_quote_container">'
        b'<blockquote class="gmail_quote" style="margin:0px 0px 0px 0.8ex">'
        b"quoted</blockquote></div>"
    )
    sk = extract_skeleton(raw)
    assert sk["html_probes"]["has_gmail_quote_container"] is True
    assert sk["html_probes"]["has_blockquote_gmail_quote"] is True
    assert "0.8ex" in sk["html_probes"].get("blockquote_style", "")


def test_extract_skeleton_plain_structure():
    raw = (
        b"MIME-Version: 1.0\r\nFrom: c@example.com\r\n"
        b'Content-Type: text/plain; charset="UTF-8"\r\n\r\n'
        b"hello\r\n> quoted line\r\n\r\n"
    )
    sk = extract_skeleton(raw)
    assert any(s.startswith("QUOTE") for s in sk["plain_structure"])
    assert any(s == "BLANK" for s in sk["plain_structure"])


def test_plain_line_structure_crlf_normalization():
    """CRLF line endings should be normalized before classification.

    Without normalization, trailing \r prevents regex patterns (like
    "On ... wrote:") from matching, and empty lines don't classify as BLANK.
    """
    # LF-only input with attribution line
    lf_text = "On Mon, Jan 1 at 1:00 PM User <u@example.com> wrote:\n\n> quoted\n"
    lf_result = _plain_line_structure(lf_text)

    # CRLF input — should produce identical classification
    crlf_text = (
        "On Mon, Jan 1 at 1:00 PM User <u@example.com> wrote:\r\n\r\n> quoted\r\n"
    )
    crlf_result = _plain_line_structure(crlf_text)

    # Attribution line should be detected in both
    assert any("ATTR_LINE" in item for item in lf_result), (
        f"LF missing ATTR_LINE: {lf_result}"
    )
    assert any("ATTR_LINE" in item for item in crlf_result), (
        f"CRLF missing ATTR_LINE: {crlf_result}"
    )
    # Both should have the same structure
    assert lf_result == crlf_result, f"LF: {lf_result}\nCRLF: {crlf_result}"
    # No stray \r characters should appear in output
    assert not any("\r" in item for item in crlf_result)


# ---------------------------------------------------------------------------
# PII-correctness: forwarded-header block and attr_template
# ---------------------------------------------------------------------------

_FWD_HTML = (
    "<div>"
    "---------- Forwarded message ---------<br>"
    'From: <strong class="gmail_sendername" dir="auto">Jane Roe</strong>'
    ' <span dir="auto">&lt;<a href="mailto:jane@example.com">jane@example.com</a>&gt;</span><br>'
    "Date: Mon, 2 Jun 2025 at 14:05<br>"
    "Subject: Hello<br>"
    'To: <a href="mailto:bob@example.com">bob@example.com</a><br>'
    "</div>"
)

_ATTR_HTML = (
    '<div dir="ltr" class="gmail_attr">'
    'On Mon, 2 Jun 2025 at 14:05, X &lt;<a href="mailto:x@example.com">x@example.com</a>&gt; wrote:'
    "<br></div>"
)


def test_forward_header_block_redacts_sender_name():
    """forward_header_block must not contain any display name or subject text."""
    raw = _make_raw(_FWD_HTML)
    sk = extract_skeleton(raw)
    probes = sk["html_probes"]

    assert "forward_header_block" in probes, "probe key missing"
    fhb = probes["forward_header_block"]

    # Display name and subject must be gone
    assert "Jane Roe" not in fhb, f"sender name leaked: {fhb!r}"
    assert "Hello" not in fhb, f"subject leaked: {fhb!r}"

    # Structure must be preserved
    assert 'class="gmail_sendername"' in fhb, f"sendername tag missing: {fhb!r}"

    # Email addresses must be redacted
    assert "jane@example.com" not in fhb, f"email leaked: {fhb!r}"


def test_attr_template_redacts_date():
    """attr_template must not contain the real date string."""
    raw = _make_raw(_ATTR_HTML)
    sk = extract_skeleton(raw)
    probes = sk["html_probes"]

    assert "attr_template" in probes, "attr_template probe key missing"
    tmpl = probes["attr_template"]

    # Real date must not appear
    assert "2 Jun 2025" not in tmpl, f"date leaked: {tmpl!r}"
    assert "Mon" not in tmpl, f"date leaked: {tmpl!r}"

    # Template must still end with wrote:
    assert tmpl.endswith("wrote:"), f"template malformed: {tmpl!r}"


# ---------------------------------------------------------------------------
# Finding #6 — EMAIL_RE must redact apostrophe-containing local parts
# ---------------------------------------------------------------------------


def test_redact_apostrophe_local_part():
    """Addresses with apostrophes in the local part must be fully redacted."""
    from tools.golden_skeleton import _redact

    result = _redact("Send to o'hara@example.com please")
    assert "o'hara" not in result, f"local part leaked: {result!r}"
    assert "‹email›" in result, f"placeholder missing: {result!r}"


# ---------------------------------------------------------------------------
# Finding #7 — multi-class gmail_attr suppresses inner text
# ---------------------------------------------------------------------------


def test_sanitize_html_multi_class_gmail_attr_suppresses_text():
    """Elements with 'gmail_attr' in a multi-class value must still redact inner text."""
    out = sanitize_html(
        '<div class="gmail_attr extra">Real Name &lt;x@example.com&gt;</div>'
    )
    # Inner attribution text must not appear
    assert "Real Name" not in out, f"attribution name leaked: {out!r}"
    assert "x@example.com" not in out, f"email leaked: {out!r}"
    # The structural tag itself must be preserved
    assert 'class="gmail_attr extra"' in out, f"tag class missing: {out!r}"


def test_sanitize_html_multi_class_gmail_sendername_suppresses_text():
    """Elements with 'gmail_sendername' alongside other classes must redact inner text."""
    out = sanitize_html('<strong class="gmail_sendername bold-name">Jane Doe</strong>')
    assert "Jane" not in out, f"sender name leaked: {out!r}"
    assert "Doe" not in out, f"sender name leaked: {out!r}"
    assert 'class="gmail_sendername bold-name"' in out, f"tag class missing: {out!r}"


def test_bare_void_element_does_not_desync_class_stack():
    """A bare void element (<br>, no trailing slash) inside a classed element must
    not leave a dangling entry on the class stack. Gmail HTML emits bare <br>
    (e.g. in gmail_attr blocks); html.parser fires handle_starttag but never a
    matching handle_endtag for it, so pushing it would desync redact tracking."""
    s = _Sanitizer()
    s.feed('<div class="gmail_attr"><br></div>')
    assert s._class_stack == [], f"stack desynced: {s._class_stack!r}"
    assert s._in_redact_element() is False


def test_bare_void_element_keeps_redaction_scoped():
    """After a redact element closes, later text must NOT be treated as redacted
    even when a bare <br> appeared inside it."""
    out = sanitize_html('<div class="gmail_attr">secret<br></div><span>KEEPME</span>')
    # The gmail_attr inner text is redacted; the trailing span structure remains.
    assert "<span>" in out
    assert "secret" not in out
