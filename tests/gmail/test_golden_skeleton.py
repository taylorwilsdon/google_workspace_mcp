"""Tests for the golden-skeleton extractor and HTML sanitizer.

All fixtures use synthetic data only (example.com addresses, no personal data).
"""

import email.mime.multipart
import email.mime.text

import pytest

from tests.gmail.golden_skeleton import (
    _plain_line_structure,
    _redact,
    extract_skeleton,
    sanitize_html,
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


@pytest.mark.parametrize(
    ("html", "class_attr"),
    [
        (
            '<div class="gmail_attr">On X, Jane Roe &lt;'
            '<a href="mailto:j@example.com">j@example.com</a>&gt; wrote:</div>',
            'class="gmail_attr"',
        ),
        (
            '<div class="gmail_attr extra">Jane Roe &lt;j@example.com&gt;</div>',
            'class="gmail_attr extra"',
        ),
        (
            '<strong class="gmail_sendername">Jane Roe</strong>',
            'class="gmail_sendername"',
        ),
        (
            '<strong class="gmail_sendername bold-name">Jane Roe</strong>',
            'class="gmail_sendername bold-name"',
        ),
    ],
)
def test_sanitize_html_redacts_attribution_text(html, class_attr):
    """Names and addresses inside gmail_attr/gmail_sendername are dropped, even
    alongside other classes, while the structural tag is kept."""
    out = sanitize_html(html)
    assert "Jane" not in out and "j@example.com" not in out, out
    assert class_attr in out, out


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


def test_plain_line_structure_normalizes_crlf():
    """A trailing CR must not stop the attribution or blank lines matching."""
    text = "On Mon, Jan 1 at 1:00 PM User <u@example.com> wrote:\n\n> quoted\n"
    lf_result = _plain_line_structure(text)
    assert any("ATTR_LINE" in item for item in lf_result), lf_result
    assert _plain_line_structure(text.replace("\n", "\r\n")) == lf_result


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


def test_redact_apostrophe_local_part():
    """Addresses with apostrophes in the local part must be fully redacted."""

    result = _redact("Send to o'hara@example.com please")
    assert "o'hara" not in result, f"local part leaked: {result!r}"
    assert "‹email›" in result, f"placeholder missing: {result!r}"


def test_html_tags_redact_free_text_attributes():
    raw = _make_raw(
        '<div title="Jane Roe" data-owner="Jane Roe" class="gmail_quote">'
        "<a href='https://example.com/jane-roe'>x</a>"
        '<img alt="Jane Roe" src="https://example.com/jane.png"></div>'
    )
    tags = "".join(extract_skeleton(raw)["html_tags"])
    assert "Jane" not in tags and "jane" not in tags, tags
    assert 'class="gmail_quote"' in tags, tags


def test_sanitize_html_keeps_only_structural_attribute_values():
    out = sanitize_html(
        '<div dir="rtl" class="gmail_attr" title="Jane Roe" data-x="Jane Roe" '
        "style=\"font-family:'Jane Roe Sans';background:url(https://example.com/jane)\">"
        "</div>"
    )
    assert "Jane" not in out and "jane" not in out, out
    assert 'dir="rtl"' in out and 'class="gmail_attr"' in out, out
    assert "font-family:" in out, out


def test_blockquote_style_redacts_free_text_but_keeps_structure():
    raw = _make_raw(
        '<blockquote class="gmail_quote" style="margin:0px 0px 0px 0.8ex;'
        "font-family:'Jane Roe'\">q</blockquote>"
    )
    style = extract_skeleton(raw)["html_probes"]["blockquote_style"]
    assert "Jane" not in style, style
    assert style.startswith("margin:0px 0px 0px 0.8ex;"), style


def test_blockquote_style_of_gmail_quote_is_unchanged():
    style = "margin:0px 0px 0px 0.8ex;border-left:1px solid rgb(204,204,204);padding-left:1ex"
    raw = _make_raw(f'<blockquote class="gmail_quote" style="{style}">q</blockquote>')
    assert extract_skeleton(raw)["html_probes"]["blockquote_style"] == style


@pytest.mark.parametrize(
    "line",
    [
        "On Jane Roe <jane@example.com> wrote:",
        "On Mon, 2 Jun 2025, Jane Roe <jane@example.com> wrote:",
    ],
)
def test_attr_line_never_leaks_date_or_name(line):
    out = _plain_line_structure(line)
    assert len(out) == 1 and out[0].startswith("ATTR_LINE"), out
    assert not any(word in out[0] for word in ("Mon", "Jane", "jane")), out


def test_boundary_pattern_masks_non_hex_characters():
    msg = email.mime.multipart.MIMEMultipart("alternative", boundary="Molly_Smith=0a")
    msg.attach(email.mime.text.MIMEText("x", "plain", "utf-8"))
    pattern = extract_skeleton(msg.as_bytes())["mime_tree"][0]["boundary_pattern"]
    assert "Molly" not in pattern and "Smith" not in pattern, pattern
    assert pattern.endswith("=xx"), pattern


def test_gmail_boundary_shape_is_preserved():
    sk = extract_skeleton(RAW)
    assert sk["mime_tree"][0]["boundary_pattern"] == "x" * 28
