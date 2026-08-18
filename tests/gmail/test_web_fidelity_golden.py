import json
import pathlib
import re

from gmail.gmail_web_mime import (
    BLOCKQUOTE_STYLE,
    build_forwarded_container_html,
    build_forwarded_plain,
)
from tools.golden_skeleton import sanitize_html

FIX = pathlib.Path(__file__).parent / "fixtures"


def test_blockquote_style_matches_current_golden():
    golden = json.loads((FIX / "golden_reply.json").read_text())
    assert BLOCKQUOTE_STYLE == golden["html_probes"]["blockquote_style"]


# ---------------------------------------------------------------------------
# build_forwarded_container_html tests
# ---------------------------------------------------------------------------


def _tags(html: str) -> str:
    """Sanitize HTML and return all tags as a single string for substring checks."""
    return "".join(
        re.sub(r'href="[^"]*"', 'href="‹h›"', t)
        for t in re.findall(r"<[^>]+>", sanitize_html(html))
    )


def test_forward_container_matches_golden_skeleton():
    """Verify builder output yields probes matching golden fixture values."""
    golden = json.loads((FIX / "golden_forward.json").read_text())
    html = build_forwarded_container_html(
        "Jane Roe",
        "jane@example.com",
        "Mon, 2 Jun 2025 at 14:05",
        "Quarterly",
        "joe@example.com",
        "<div>orig</div>",
    )
    # Probe the builder's output directly and compare to golden fixture.
    has_gmail_quote_container = 'class="gmail_quote gmail_quote_container"' in html
    has_gmail_sendername = "gmail_sendername" in html
    has_blockquote_gmail_quote = "blockquote" in html
    has_forwarded_literal = "Forwarded message" in html

    assert (
        has_gmail_quote_container == golden["html_probes"]["has_gmail_quote_container"]
    )
    assert has_gmail_sendername == golden["html_probes"]["has_gmail_sendername"]
    assert (
        has_blockquote_gmail_quote
        == golden["html_probes"]["has_blockquote_gmail_quote"]
    )
    assert has_forwarded_literal == golden["html_probes"]["has_forwarded_literal"]
    # Keep the existing tag-skeleton assertions.
    tags = _tags(html)
    assert 'class="gmail_quote gmail_quote_container"' in tags
    assert 'class="gmail_sendername"' in tags
    assert "blockquote" not in tags


def test_forward_container_html_no_blockquote():
    """Output must never contain a blockquote element."""
    html = build_forwarded_container_html(
        "Alice",
        "alice@example.com",
        "Tue, 3 Jun 2025 at 09:00",
        "Test subject",
        "bob@example.com",
        "<div>body</div>",
    )
    assert "blockquote" not in html


def test_forward_container_html_required_classes():
    """All required Gmail structural classes must be present."""
    html = build_forwarded_container_html(
        "Alice",
        "alice@example.com",
        "Tue, 3 Jun 2025 at 09:00",
        "Test subject",
        "bob@example.com",
        "<div>body</div>",
    )
    assert 'class="gmail_quote gmail_quote_container"' in html
    assert 'class="gmail_attr"' in html
    assert 'class="gmail_sendername"' in html


def test_forward_container_html_forwarded_literal():
    """The forwarded separator literal must appear verbatim."""
    html = build_forwarded_container_html(
        "Alice",
        "alice@example.com",
        "Tue, 3 Jun 2025 at 09:00",
        "Test subject",
        "bob@example.com",
        "<div>body</div>",
    )
    assert "---------- Forwarded message ---------" in html


def test_forward_container_html_probes_match_golden():
    """All boolean probes must agree with golden_forward.json fixture values."""
    golden = json.loads((FIX / "golden_forward.json").read_text())
    html = build_forwarded_container_html(
        "Alice",
        "alice@example.com",
        "Tue, 3 Jun 2025 at 09:00",
        "Test subject",
        "bob@example.com",
        "<div>body</div>",
    )
    assert ('class="gmail_quote gmail_quote_container"' in html) == golden[
        "html_probes"
    ]["has_gmail_quote_container"]
    assert ('class="gmail_attr"' in html) == golden["html_probes"]["has_gmail_attr"]
    assert ("gmail_sendername" in html) == golden["html_probes"]["has_gmail_sendername"]
    assert ("blockquote" in html) == golden["html_probes"]["has_blockquote_gmail_quote"]
    assert ("Forwarded message" in html) == golden["html_probes"][
        "has_forwarded_literal"
    ]


def test_forward_container_html_from_fields():
    """From name, email, date, subject, to, and orig_html appear in output."""
    html = build_forwarded_container_html(
        "Jane Roe",
        "jane@example.com",
        "Mon, 2 Jun 2025 at 14:05",
        "Quarterly Report",
        "joe@example.com",
        "<div>original content</div>",
    )
    assert "Jane Roe" in html
    assert "jane@example.com" in html
    assert "Mon, 2 Jun 2025 at 14:05" in html
    assert "Quarterly Report" in html
    assert "joe@example.com" in html
    assert "<div>original content</div>" in html


def test_forward_container_html_empty_from_name():
    """When from_name is empty, omit the <strong> sendername element."""
    html = build_forwarded_container_html(
        "",
        "anon@example.com",
        "Wed, 4 Jun 2025 at 10:00",
        "No-name test",
        "recipient@example.com",
        "<div>content</div>",
    )
    assert "gmail_sendername" not in html
    assert "anon@example.com" in html


def test_forward_container_html_whitespace_only_from_name():
    """When from_name is whitespace only, treat as empty — omit sendername."""
    html = build_forwarded_container_html(
        "   ",
        "anon@example.com",
        "Wed, 4 Jun 2025 at 10:00",
        "Whitespace test",
        "recipient@example.com",
        "<div>content</div>",
    )
    assert "gmail_sendername" not in html
    assert "anon@example.com" in html


def test_forward_container_html_nonascii_from_name_escaped():
    """Non-ASCII from_name is HTML-escaped in output."""
    html = build_forwarded_container_html(
        "Ångström & Müller",
        "user@example.com",
        "Thu, 5 Jun 2025 at 08:00",
        "Unicode test",
        "other@example.com",
        "<div>x</div>",
    )
    # _escape_body must escape & to &amp;
    assert "&amp;" in html
    # Raw ampersand must NOT appear inside the sendername region
    # (it would indicate unescaped content).
    assert "Ångström & Müller" not in html
    # Non-ASCII characters themselves survive (it's only the & that gets escaped).
    assert "Ångström" in html


def test_forward_container_html_subject_escaped():
    """Subject containing < and & must be HTML-escaped."""
    html = build_forwarded_container_html(
        "Alice",
        "alice@example.com",
        "Fri, 6 Jun 2025 at 12:00",
        "Subject <with> &special",
        "bob@example.com",
        "<div>body</div>",
    )
    assert "&lt;with&gt;" in html
    assert "&amp;special" in html
    # Raw < must not appear in the subject field (orig_html may contain it).
    # Check the attr div specifically.
    assert "Subject: Subject &lt;with&gt; &amp;special" in html


def test_forward_container_html_to_rendered_passthrough():
    """to_rendered (pre-rendered HTML) is inserted verbatim, not escaped."""
    to_html = '<a href="mailto:bob@example.com">Bob</a>'
    html = build_forwarded_container_html(
        "Alice",
        "alice@example.com",
        "Fri, 6 Jun 2025 at 12:00",
        "Meeting",
        to_html,
        "<div>body</div>",
    )
    assert to_html in html


def test_forward_container_html_from_email_escaped():
    """from_email containing & must be HTML-escaped in output."""
    html = build_forwarded_container_html(
        "Test User",
        "a&b@example.com",
        "Fri, 6 Jun 2025 at 12:00",
        "Email escape test",
        "recipient@example.com",
        "<div>body</div>",
    )
    # The escaped form must appear (in mailto href and in display text)
    assert "a&amp;b@example.com" in html
    # The raw, unescaped form must NOT appear
    assert "a&b@example.com" not in html


# ---------------------------------------------------------------------------
# build_forwarded_plain tests
# ---------------------------------------------------------------------------


def test_forward_plain_separator():
    """Plain-text separator line must be exact."""
    text = build_forwarded_plain(
        "Jane Roe",
        "jane@example.com",
        "Mon, 2 Jun 2025 at 14:05",
        "Quarterly",
        "joe@example.com",
        "original body",
    )
    assert "---------- Forwarded message ---------" in text


def test_forward_plain_header_lines():
    """All four header lines must appear with correct labels."""
    text = build_forwarded_plain(
        "Jane Roe",
        "jane@example.com",
        "Mon, 2 Jun 2025 at 14:05",
        "Quarterly Report",
        "joe@example.com",
        "original body",
    )
    assert "From: Jane Roe <jane@example.com>" in text
    assert "Date: Mon, 2 Jun 2025 at 14:05" in text
    assert "Subject: Quarterly Report" in text
    assert "To: joe@example.com" in text


def test_forward_plain_orig_not_quoted():
    """Original body must appear as-is (no '> ' quoting)."""
    orig = "line one\nline two"
    text = build_forwarded_plain(
        "Alice",
        "alice@example.com",
        "Sat, 7 Jun 2025 at 11:00",
        "Test",
        "bob@example.com",
        orig,
    )
    assert "> line one" not in text
    assert "line one\nline two" in text


def test_forward_plain_empty_from_name():
    """When from_name is empty, From line uses bare email."""
    text = build_forwarded_plain(
        "",
        "anon@example.com",
        "Sun, 8 Jun 2025 at 07:00",
        "No name",
        "r@example.com",
        "body",
    )
    assert "From: anon@example.com" in text
    assert "<anon@example.com>" not in text


def test_forward_plain_whitespace_only_from_name():
    """Whitespace-only from_name is treated as empty — bare email."""
    text = build_forwarded_plain(
        "  ",
        "anon@example.com",
        "Sun, 8 Jun 2025 at 07:00",
        "No name ws",
        "r@example.com",
        "body",
    )
    assert "From: anon@example.com" in text


def test_forward_plain_scaffold_matches_golden():
    """Plain scaffold structure must agree with golden_forward.json."""
    golden = json.loads((FIX / "golden_forward.json").read_text())
    text = build_forwarded_plain(
        "Jane Roe",
        "jane@example.com",
        "Mon, 2 Jun 2025 at 14:05",
        "Quarterly",
        "joe@example.com",
        "original line one\noriginal line two",
    )
    # The separator line must match the FWD_SEP pattern from the golden scaffold.
    fwd_sep = next(
        (line for line in golden["plain_scaffold"] if line.startswith("FWD_SEP:")), None
    )
    assert fwd_sep is not None
    sep_value = fwd_sep.split(": ", 1)[1]
    assert sep_value in text
    # Header labels must appear in order.
    fwd_hdrs = [
        line for line in golden["plain_scaffold"] if line.startswith("FWD_HDR:")
    ]
    labels = [h.split(": ", 1)[1].split(":")[0] for h in fwd_hdrs]
    pos = [text.index(f"{lbl}:") for lbl in labels]
    assert pos == sorted(pos), "Forward header labels must appear in golden order"


# ---------------------------------------------------------------------------
# assemble_mixed tests
# ---------------------------------------------------------------------------

_PDF_DATA = b"%PDF-1.4 fake"
_PNG_DATA = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


def test_assemble_mixed_tree():
    from gmail.gmail_web_mime import assemble_mixed
    from tools.golden_skeleton import extract_skeleton

    raw = assemble_mixed(
        [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Fwd: x")],
        "plain",
        '<div dir="ltr">html</div>',
        [{"filename": "a.pdf", "mime_type": "application/pdf", "data": b"%PDF-1.4"}],
        "000000000000aaaaaaaaaaaaaaaa",
        "000000000000bbbbbbbbbbbbbbbb",
    )
    sk = extract_skeleton(raw.encode("utf-8"))
    top = sk["mime_tree"][0]
    assert top["content_type"] == "multipart/mixed"
    assert top["parts"][0]["content_type"] == "multipart/alternative"
    assert top["parts"][1]["content_type"] == "application/pdf"
    assert top["parts"][1]["disposition"] == "attachment"


def test_assemble_mixed_skeleton_matches_golden():
    """mime_shape from golden_forward_attach.json must match assembled output."""
    from gmail.gmail_web_mime import assemble_mixed
    from tools.golden_skeleton import extract_skeleton

    golden = json.loads((FIX / "golden_forward_attach.json").read_text())
    shape = golden["mime_shape"]

    raw = assemble_mixed(
        [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Fwd: y")],
        "plain body",
        '<div dir="ltr">html body</div>',
        [{"filename": "report.pdf", "mime_type": "application/pdf", "data": _PDF_DATA}],
        "000000000000aaaaaaaaaaaaaaaa",
        "000000000000bbbbbbbbbbbbbbbb",
    )
    sk = extract_skeleton(raw.encode("utf-8"))
    top = sk["mime_tree"][0]

    # Top-level content_type
    assert top["content_type"] == shape["content_type"]
    # First child: multipart/alternative
    alt = top["parts"][0]
    assert alt["content_type"] == shape["parts"][0]["content_type"]
    assert (
        alt["parts"][0]["content_type"] == shape["parts"][0]["parts"][0]["content_type"]
    )
    assert (
        alt["parts"][1]["content_type"] == shape["parts"][0]["parts"][1]["content_type"]
    )
    # Second child: attachment
    attach = top["parts"][1]
    assert attach["content_type"] == shape["parts"][1]["content_type"]
    assert attach["disposition"] == shape["parts"][1]["disposition"]
    assert attach["cte"] == shape["parts"][1]["cte"]


def test_assemble_mixed_multiple_attachments_in_order():
    """Two attachments produce two child parts in the order supplied."""
    from gmail.gmail_web_mime import assemble_mixed
    from tools.golden_skeleton import extract_skeleton

    raw = assemble_mixed(
        [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Multi")],
        "plain",
        "<div>html</div>",
        [
            {
                "filename": "first.pdf",
                "mime_type": "application/pdf",
                "data": _PDF_DATA,
            },
            {"filename": "second.png", "mime_type": "image/png", "data": _PNG_DATA},
        ],
        "000000000000aaaaaaaaaaaaaaaa",
        "000000000000bbbbbbbbbbbbbbbb",
    )
    sk = extract_skeleton(raw.encode("utf-8"))
    parts = sk["mime_tree"][0]["parts"]
    assert len(parts) == 3  # alt + 2 attachments
    assert parts[1]["content_type"] == "application/pdf"
    assert parts[2]["content_type"] == "image/png"
    assert parts[1]["disposition"] == "attachment"
    assert parts[2]["disposition"] == "attachment"


def test_assemble_mixed_filename_quote_escaping_discriminating():
    """Filenames with double-quotes are RFC 2045 escaped in BOTH name= and filename= params.

    This test FAILS if escaping is absent (old behaviour) and PASSES only after the fix.
    """
    from gmail.gmail_web_mime import assemble_mixed

    raw = assemble_mixed(
        [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Filename")],
        "plain",
        "<div>html</div>",
        [
            {
                "filename": 'my "final".pdf',
                "mime_type": "application/pdf",
                "data": b"x",
            }
        ],
        "000000000000aaaaaaaaaaaaaaaa",
        "000000000000bbbbbbbbbbbbbbbb",
    )
    # Both Content-Type name= and Content-Disposition filename= must use escaped quotes.
    assert 'name="my \\"final\\".pdf"' in raw, (
        "Content-Type name= param must escape inner double-quotes"
    )
    assert 'filename="my \\"final\\".pdf"' in raw, (
        "Content-Disposition filename= param must escape inner double-quotes"
    )
    # The malformed (unescaped) form must NOT appear.
    assert 'name="my "final".pdf"' not in raw, (
        "Unescaped double-quote in name= would be malformed RFC 2045"
    )


def test_assemble_mixed_filename_backslash_escaping():
    """Filenames with backslashes are RFC 2045 escaped (backslash doubled) in BOTH params."""
    from gmail.gmail_web_mime import assemble_mixed

    raw = assemble_mixed(
        [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Filename")],
        "plain",
        "<div>html</div>",
        [
            {
                "filename": "a\\b.pdf",
                "mime_type": "application/pdf",
                "data": b"x",
            }
        ],
        "000000000000aaaaaaaaaaaaaaaa",
        "000000000000bbbbbbbbbbbbbbbb",
    )
    # Backslash must be doubled in both params.
    assert 'name="a\\\\b.pdf"' in raw, (
        "Content-Type name= param must double-escape backslashes"
    )
    assert 'filename="a\\\\b.pdf"' in raw, (
        "Content-Disposition filename= param must double-escape backslashes"
    )


def test_assemble_mixed_inner_alternative_matches_assemble_alternative():
    """Inner alternative subtree body must equal what assemble_alternative produces.

    Asserts BOTH the text/plain AND text/html part bodies appear verbatim in the
    mixed message, matching the entire multipart/alternative child block.
    """
    from gmail.gmail_web_mime import assemble_alternative, assemble_mixed

    plain_text = "hello world"
    html_text = "<div>hello world</div>"
    boundary_alt = "000000000000bbbbbbbbbbbbbbbb"
    boundary_mixed = "000000000000aaaaaaaaaaaaaaaa"
    headers = [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Test")]

    # Build standalone alternative
    alt_standalone = assemble_alternative(headers, plain_text, html_text, boundary_alt)

    # Build mixed and extract the alternative child part body
    mixed = assemble_mixed(
        headers,
        plain_text,
        html_text,
        [{"filename": "x.pdf", "mime_type": "application/pdf", "data": b"x"}],
        boundary_mixed,
        boundary_alt,
    )

    crlf = "\r\n"
    plain_marker = f"--{boundary_alt}{crlf}Content-Type: text/plain"
    html_marker = f"--{boundary_alt}{crlf}Content-Type: text/html"
    closing_alt = f"--{boundary_alt}--"

    # Extract plain part block (from plain_marker up to html_marker)
    alt_plain_start = alt_standalone.index(plain_marker)
    alt_html_start = alt_standalone.index(html_marker)
    alt_plain_body = alt_standalone[alt_plain_start:alt_html_start]
    assert alt_plain_body in mixed, (
        "Plain part body from alt must appear verbatim in mixed"
    )

    # Extract html part block (from html_marker up to closing --)
    alt_closing_start = alt_standalone.index(closing_alt)
    alt_html_body = alt_standalone[alt_html_start:alt_closing_start]
    assert alt_html_body in mixed, (
        "HTML part body from alt must appear verbatim in mixed"
    )

    # The closing alt boundary must also appear in mixed
    assert closing_alt in mixed, "Closing alternative boundary must appear in mixed"


# ---------------------------------------------------------------------------
# assemble_web_message tests
# ---------------------------------------------------------------------------

_JPEG_DATA = b"\xff\xd8\xff\xe0" + b"\xab" * 40  # fake JPEG header + padding
_PNG_INLINE = b"\x89PNG\r\n\x1a\n" + b"\xcd" * 30


def test_web_message_no_inline_no_attach_matches_alternative():
    """alternative-only path must produce byte-identical output to assemble_alternative."""
    from gmail.gmail_web_mime import assemble_alternative, assemble_web_message

    headers = [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Alt")]
    plain = "hello"
    html = "<div>hello</div>"
    boundary = "000000000000aabbccddeeff0011"

    via_alt = assemble_alternative(headers, plain, html, boundary)
    via_web = assemble_web_message(
        headers,
        plain,
        html,
        inline_parts=None,
        attachment_parts=None,
        boundary_alt=boundary,
    )
    assert via_web == via_alt


def test_web_message_inline_only_top_is_related():
    """Inline-only: top container must be multipart/related."""
    from gmail.gmail_web_mime import assemble_web_message
    from tools.golden_skeleton import extract_skeleton

    headers = [
        ("From", "a@example.com"),
        ("To", "b@example.com"),
        ("Subject", "Inline"),
    ]
    raw = assemble_web_message(
        headers,
        "plain",
        "<div>html</div>",
        inline_parts=[
            {
                "filename": "img.jpg",
                "mime_type": "image/jpeg",
                "data": _JPEG_DATA,
                "content_id": "img001@example.com",
            }
        ],
        attachment_parts=None,
        boundary_alt="000000000000aaaaaaaaaaaaaaaa",
        boundary_related="000000000000bbbbbbbbbbbbbbbb",
    )
    sk = extract_skeleton(raw.encode("utf-8"))
    top = sk["mime_tree"][0]
    assert top["content_type"] == "multipart/related"
    alt = top["parts"][0]
    assert alt["content_type"] == "multipart/alternative"
    assert alt["parts"][0]["content_type"] == "text/plain"
    assert alt["parts"][1]["content_type"] == "text/html"
    inline = top["parts"][1]
    assert inline["content_type"] == "image/jpeg"
    assert inline["disposition"] == "inline"
    assert inline["cte"] == "base64"


def test_web_message_inline_only_cid_headers():
    """Each inline part must have Content-ID with angle brackets and Content-Disposition: inline."""
    from gmail.gmail_web_mime import assemble_web_message

    cid_bare = "myimage@example.com"
    cid_wrapped = "<otherid@example.com>"
    headers = [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "CID")]
    raw = assemble_web_message(
        headers,
        "plain",
        "<div>html</div>",
        inline_parts=[
            {
                "filename": "a.jpg",
                "mime_type": "image/jpeg",
                "data": _JPEG_DATA,
                "content_id": cid_bare,
            },
            {
                "filename": "b.png",
                "mime_type": "image/png",
                "data": _PNG_INLINE,
                "content_id": cid_wrapped,
            },
        ],
        attachment_parts=None,
        boundary_alt="000000000000aaaaaaaaaaaaaaaa",
        boundary_related="000000000000bbbbbbbbbbbbbbbb",
    )
    assert f"Content-ID: <{cid_bare}>" in raw
    assert f"Content-ID: {cid_wrapped}" in raw
    assert "Content-Disposition: inline" in raw


def test_web_message_inline_only_base64_roundtrip():
    """Round-trip decode of an inline part's base64 block returns original bytes."""
    import base64 as _base64

    from gmail.gmail_web_mime import assemble_web_message

    data = bytes(range(80))  # >57 bytes so multi-line
    headers = [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "RT")]
    boundary_related = "000000000000bbbbbbbbbbbbbbbb"
    raw = assemble_web_message(
        headers,
        "plain",
        "<div>html</div>",
        inline_parts=[
            {
                "filename": "img.png",
                "mime_type": "image/png",
                "data": data,
                "content_id": "img@example.com",
            }
        ],
        attachment_parts=None,
        boundary_alt="000000000000aaaaaaaaaaaaaaaa",
        boundary_related=boundary_related,
    )
    crlf = "\r\n"
    disp = 'Content-Disposition: inline; filename="img.png"'
    disp_pos = raw.index(disp)
    body_start = raw.index(crlf + crlf, disp_pos) + len(crlf + crlf)
    next_boundary = f"{crlf}--{boundary_related}"
    body_end = raw.index(next_boundary, body_start)
    b64_block = raw[body_start:body_end]
    decoded = _base64.b64decode(b64_block.replace(crlf, ""))
    assert decoded == data


def test_web_message_attach_only_matches_assemble_mixed():
    """Attachments-only path must produce byte-identical output to assemble_mixed."""
    from gmail.gmail_web_mime import assemble_mixed, assemble_web_message

    headers = [
        ("From", "a@example.com"),
        ("To", "b@example.com"),
        ("Subject", "Attach"),
    ]
    plain = "plain body"
    html = "<div>html body</div>"
    attachments = [
        {"filename": "doc.pdf", "mime_type": "application/pdf", "data": _PDF_DATA}
    ]
    b_mixed = "000000000000aaaaaaaaaaaaaaaa"
    b_alt = "000000000000bbbbbbbbbbbbbbbb"

    via_mixed = assemble_mixed(headers, plain, html, attachments, b_mixed, b_alt)
    via_web = assemble_web_message(
        headers,
        plain,
        html,
        inline_parts=None,
        attachment_parts=attachments,
        boundary_alt=b_alt,
        boundary_mixed=b_mixed,
    )
    assert via_web == via_mixed


def test_web_message_inline_and_attach_matches_golden():
    """Inline + attachments must produce a mime_shape matching golden_inline.json."""
    import json
    import pathlib

    from gmail.gmail_web_mime import assemble_web_message
    from tools.golden_skeleton import extract_skeleton

    golden = json.loads(
        (pathlib.Path(__file__).parent / "fixtures" / "golden_inline.json").read_text()
    )
    shape = golden["mime_shape"]

    headers = [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Both")]
    raw = assemble_web_message(
        headers,
        "plain",
        "<div>html</div>",
        inline_parts=[
            {
                "filename": "img.jpg",
                "mime_type": "image/jpeg",
                "data": _JPEG_DATA,
                "content_id": "img1@example.com",
            },
        ],
        attachment_parts=[
            {
                "filename": "report.pdf",
                "mime_type": "application/pdf",
                "data": _PDF_DATA,
            },
        ],
        boundary_alt="000000000000cccccccccccccccc",
        boundary_related="000000000000bbbbbbbbbbbbbbbb",
        boundary_mixed="000000000000aaaaaaaaaaaaaaaa",
    )
    sk = extract_skeleton(raw.encode("utf-8"))
    top = sk["mime_tree"][0]

    # Top-level: multipart/mixed
    assert top["content_type"] == shape["content_type"]  # multipart/mixed

    # First child: multipart/related
    related = top["parts"][0]
    assert (
        related["content_type"] == shape["parts"][0]["content_type"]
    )  # multipart/related

    # First child of related: multipart/alternative
    alt = related["parts"][0]
    assert alt["content_type"] == shape["parts"][0]["parts"][0]["content_type"]
    assert alt["parts"][0]["content_type"] == "text/plain"
    assert alt["parts"][1]["content_type"] == "text/html"

    # Second child of related: inline image
    img = related["parts"][1]
    assert img["content_type"] == "image/jpeg"
    assert img["disposition"] == "inline"
    assert img["cte"] == "base64"

    # Second child of mixed: attachment (content_type depends on what we pass, not the golden fixture)
    att = top["parts"][1]
    assert att["disposition"] == shape["parts"][1]["disposition"]  # "attachment"
    assert att["cte"] == shape["parts"][1]["cte"]  # "base64"


def test_web_message_inline_filename_escaping():
    """Filenames with double-quotes and spaces in inline parts are RFC 2045 escaped."""
    from gmail.gmail_web_mime import assemble_web_message

    headers = [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Esc")]
    raw = assemble_web_message(
        headers,
        "plain",
        "<div>html</div>",
        inline_parts=[
            {
                "filename": 'my "photo" 1.jpg',
                "mime_type": "image/jpeg",
                "data": _JPEG_DATA,
                "content_id": "x@example.com",
            }
        ],
        attachment_parts=None,
        boundary_alt="000000000000aaaaaaaaaaaaaaaa",
        boundary_related="000000000000bbbbbbbbbbbbbbbb",
    )
    assert 'name="my \\"photo\\" 1.jpg"' in raw
    assert 'filename="my \\"photo\\" 1.jpg"' in raw
    # Unescaped form must not appear
    assert 'name="my "photo" 1.jpg"' not in raw


def test_web_message_inline_base64_line_length():
    """Every base64 line in an inline part must be ≤76 chars; no blank line before next boundary."""
    from gmail.gmail_web_mime import assemble_web_message

    data = bytes(range(200))
    boundary_related = "000000000000bbbbbbbbbbbbbbbb"
    headers = [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Lines")]
    raw = assemble_web_message(
        headers,
        "plain",
        "<div>html</div>",
        inline_parts=[
            {
                "filename": "big.png",
                "mime_type": "image/png",
                "data": data,
                "content_id": "big@example.com",
            }
        ],
        attachment_parts=None,
        boundary_alt="000000000000aaaaaaaaaaaaaaaa",
        boundary_related=boundary_related,
    )
    crlf = "\r\n"
    disp = 'Content-Disposition: inline; filename="big.png"'
    disp_pos = raw.index(disp)
    body_start = raw.index(crlf + crlf, disp_pos) + len(crlf + crlf)
    next_boundary = f"{crlf}--{boundary_related}"
    body_end = raw.index(next_boundary, body_start)
    b64_block = raw[body_start:body_end]

    lines = b64_block.split(crlf)
    for line in lines:
        assert len(line) <= 76, f"base64 line exceeds 76 chars: {line!r}"
    assert not b64_block.endswith(crlf), (
        "base64 block must not end with blank line before boundary"
    )
    assert lines[-1] != "", "Last base64 line before boundary must not be empty"


def test_assemble_mixed_base64_multiline():
    """200-byte attachment: base64 spans multiple lines; no empty line before next boundary."""
    import base64 as _base64

    from gmail.gmail_web_mime import assemble_mixed

    data = bytes(range(200))
    boundary_mixed = "000000000000aaaaaaaaaaaaaaaa"
    boundary_alt = "000000000000bbbbbbbbbbbbbbbb"

    raw = assemble_mixed(
        [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Big")],
        "plain",
        "<div>html</div>",
        [
            {
                "filename": "big.bin",
                "mime_type": "application/octet-stream",
                "data": data,
            }
        ],
        boundary_mixed,
        boundary_alt,
    )

    crlf = "\r\n"
    # Locate attachment part body (after the blank line following Content-Disposition)
    disp_header = 'Content-Disposition: attachment; filename="big.bin"'
    disp_pos = raw.index(disp_header)
    # Body starts after the header block's blank line (CRLFCRLF)
    body_start = raw.index(crlf + crlf, disp_pos) + len(crlf + crlf)
    # Body ends just before the next boundary line
    next_boundary = f"{crlf}--{boundary_mixed}"
    body_end = raw.index(next_boundary, body_start)
    b64_block = raw[body_start:body_end]

    # (a) round-trip decodes to original bytes
    decoded = _base64.b64decode(b64_block.replace(crlf, ""))
    assert decoded == data, "base64 block must decode back to original bytes"

    # (b) every line is ≤ 76 chars (excluding CRLF)
    lines = b64_block.split(crlf)
    for line in lines:
        assert len(line) <= 76, f"base64 line exceeds 76 chars: {line!r}"

    # (c) no empty line between last base64 line and the following boundary
    assert not b64_block.endswith(crlf), (
        "base64 block must not end with a blank line before the next boundary"
    )
    assert lines[-1] != "", "Last base64 line before boundary must not be empty"


# ---------------------------------------------------------------------------
# Guard tests for assemble_web_message boundary validation
# ---------------------------------------------------------------------------


def test_web_message_missing_boundary_related_raises():
    """assemble_web_message with inline_parts but no boundary_related raises ValueError."""
    import pytest

    from gmail.gmail_web_mime import assemble_web_message

    with pytest.raises(ValueError, match="boundary_related is required"):
        assemble_web_message(
            [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Test")],
            "plain",
            "<div>html</div>",
            inline_parts=[
                {
                    "filename": "img.jpg",
                    "mime_type": "image/jpeg",
                    "data": _JPEG_DATA,
                    "content_id": "img@example.com",
                }
            ],
            attachment_parts=None,
            boundary_alt="000000000000aaaaaaaaaaaaaaaa",
            boundary_related=None,
        )


def test_web_message_missing_boundary_mixed_raises():
    """assemble_web_message with attachment_parts but no boundary_mixed raises ValueError."""
    import pytest

    from gmail.gmail_web_mime import assemble_web_message

    with pytest.raises(ValueError, match="boundary_mixed is required"):
        assemble_web_message(
            [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Test")],
            "plain",
            "<div>html</div>",
            inline_parts=None,
            attachment_parts=[
                {
                    "filename": "doc.pdf",
                    "mime_type": "application/pdf",
                    "data": _PDF_DATA,
                }
            ],
            boundary_alt="000000000000aaaaaaaaaaaaaaaa",
            boundary_mixed=None,
        )


def test_web_message_multiple_inline_parts():
    """Multiple inline parts produce a multipart/related with all inlines preserved."""
    from gmail.gmail_web_mime import assemble_web_message
    from tools.golden_skeleton import extract_skeleton

    headers = [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Multi")]
    raw = assemble_web_message(
        headers,
        "plain",
        "<div>html</div>",
        inline_parts=[
            {
                "filename": "img1.jpg",
                "mime_type": "image/jpeg",
                "data": _JPEG_DATA,
                "content_id": "img1@example.com",
            },
            {
                "filename": "img2.png",
                "mime_type": "image/png",
                "data": _PNG_INLINE,
                "content_id": "img2@example.com",
            },
        ],
        attachment_parts=None,
        boundary_alt="000000000000aaaaaaaaaaaaaaaa",
        boundary_related="000000000000bbbbbbbbbbbbbbbb",
    )
    sk = extract_skeleton(raw.encode("utf-8"))
    top = sk["mime_tree"][0]
    assert top["content_type"] == "multipart/related"
    # Should have 3 parts: alternative + 2 inlines
    assert len(top["parts"]) == 3
    assert top["parts"][0]["content_type"] == "multipart/alternative"
    assert top["parts"][1]["content_type"] == "image/jpeg"
    assert top["parts"][1]["disposition"] == "inline"
    assert top["parts"][2]["content_type"] == "image/png"
    assert top["parts"][2]["disposition"] == "inline"
    # Verify content-id presence
    assert "Content-ID: <img1@example.com>" in raw
    assert "Content-ID: <img2@example.com>" in raw


def test_assemble_related_direct():
    """Direct call to assemble_related produces well-formed multipart/related."""
    from gmail.gmail_web_mime import assemble_related
    from tools.golden_skeleton import extract_skeleton

    headers = [
        ("From", "a@example.com"),
        ("To", "b@example.com"),
        ("Subject", "Direct"),
    ]
    raw = assemble_related(
        headers,
        "plain text",
        "<div>html text</div>",
        inline_parts=[
            {
                "filename": "inline.jpg",
                "mime_type": "image/jpeg",
                "data": _JPEG_DATA,
                "content_id": "inline@example.com",
            }
        ],
        boundary_related="000000000000bbbbbbbbbbbbbbbb",
        boundary_alt="000000000000aaaaaaaaaaaaaaaa",
    )
    sk = extract_skeleton(raw.encode("utf-8"))
    top = sk["mime_tree"][0]
    assert top["content_type"] == "multipart/related"
    # Should have alternative + inline
    assert len(top["parts"]) == 2
    assert top["parts"][0]["content_type"] == "multipart/alternative"
    assert top["parts"][0]["parts"][0]["content_type"] == "text/plain"
    assert top["parts"][0]["parts"][1]["content_type"] == "text/html"
    assert top["parts"][1]["content_type"] == "image/jpeg"
    assert top["parts"][1]["disposition"] == "inline"


# ---------------------------------------------------------------------------
# choose_cte tests
# ---------------------------------------------------------------------------


def test_choose_cte_plain_ascii():
    from gmail.gmail_web_mime import choose_cte

    assert choose_cte("plain ascii text") == "quoted-printable"


def test_choose_cte_empty_string():
    from gmail.gmail_web_mime import choose_cte

    assert choose_cte("") == "quoted-printable"


def test_choose_cte_mostly_ascii_body():
    """A body that is overwhelmingly ASCII favours quoted-printable (base64 inflates it)."""
    from gmail.gmail_web_mime import choose_cte

    # Long ASCII prefix with a single accented char: QP adds =C3=A9 (6 bytes) but
    # base64 inflates every ASCII byte by ~33 %, so QP wins overall.
    body = "a" * 200 + "é"
    assert choose_cte(body) == "quoted-printable"


def test_choose_cte_hebrew_body():
    """A long Hebrew string should yield base64 (QP would triple each byte)."""
    from gmail.gmail_web_mime import choose_cte

    # 80 Hebrew characters: each is 2 UTF-8 bytes; QP → =XX=XX (6 bytes each)
    hebrew = "שלום" * 20  # 80 chars → 160 UTF-8 bytes
    assert choose_cte(hebrew) == "base64"


def test_choose_cte_cjk_body():
    """A long CJK string should yield base64."""
    from gmail.gmail_web_mime import choose_cte

    cjk = "你好世界" * 20  # 80 chars → 240 UTF-8 bytes (3 bytes each)
    assert choose_cte(cjk) == "base64"


# ---------------------------------------------------------------------------
# assemble_alternative CTE tests
# ---------------------------------------------------------------------------


def test_assemble_alternative_ascii_body_uses_qp():
    """ASCII body → both parts use Content-Transfer-Encoding: quoted-printable."""
    import re

    from gmail.gmail_web_mime import assemble_alternative

    headers = [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "QP")]
    raw = assemble_alternative(
        headers,
        "Hello, world!",
        "<div>Hello, world!</div>",
        "000000000000aabbccddeeff0011",
    )
    # Both parts must declare QP
    ctes = re.findall(r"Content-Transfer-Encoding: (\S+)", raw)
    assert ctes == ["quoted-printable", "quoted-printable"]


def test_assemble_alternative_ascii_body_qp_roundtrip():
    """ASCII body QP-encoded: decoding the body block returns the original text."""
    import quopri

    from gmail.gmail_web_mime import assemble_alternative

    plain = "Hello from the test suite.\nSecond line."
    html = "<div>Hello from the test suite.</div><div>Second line.</div>"
    boundary = "000000000000aabbccddeeff0011"
    raw = assemble_alternative(
        [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "RT")],
        plain,
        html,
        boundary,
    )
    crlf = "\r\n"
    # Extract plain part body
    plain_marker = f"--{boundary}{crlf}Content-Type: text/plain"
    html_marker = f"--{boundary}{crlf}Content-Type: text/html"
    plain_start = raw.index(plain_marker)
    html_start = raw.index(html_marker)
    plain_block = raw[plain_start:html_start]
    # Body is after the double CRLF; strip the trailing CRLF (part separator)
    body_start = plain_block.index(crlf + crlf) + len(crlf + crlf)
    body = plain_block[body_start:].rstrip(crlf)
    decoded = quopri.decodestring(body.encode("ascii")).decode("utf-8")
    # Normalise line endings for comparison
    assert decoded.replace("\r\n", "\n") == plain


def test_assemble_alternative_hebrew_plain_uses_base64():
    """Heavily-non-ASCII plain body → plain part uses base64; decodes correctly."""
    import base64 as _b64

    from gmail.gmail_web_mime import assemble_alternative

    hebrew_plain = "שלום" * 30  # 120 Hebrew chars
    html = "<div>hello</div>"  # ASCII → may differ
    boundary = "000000000000aabbccddeeff0011"
    crlf = "\r\n"

    raw = assemble_alternative(
        [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "He")],
        hebrew_plain,
        html,
        boundary,
    )

    # Plain part must declare base64
    plain_marker = f"--{boundary}{crlf}Content-Type: text/plain"
    html_marker = f"--{boundary}{crlf}Content-Type: text/html"
    plain_start = raw.index(plain_marker)
    html_start = raw.index(html_marker)
    plain_block = raw[plain_start:html_start]

    assert "Content-Transfer-Encoding: base64" in plain_block

    # Decode body and verify round-trip
    body_offset = plain_block.index(crlf + crlf) + len(crlf + crlf)
    b64_block = plain_block[body_offset:]
    decoded = _b64.b64decode(b64_block.replace(crlf, "")).decode("utf-8")
    assert decoded == hebrew_plain


def test_assemble_alternative_parts_can_differ_in_cte():
    """Plain and HTML parts may independently choose different CTEs."""
    import base64 as _b64
    import quopri

    from gmail.gmail_web_mime import assemble_alternative

    # Plain body: heavily non-ASCII → should be base64
    hebrew_plain = "שלום עולם" * 20
    # HTML body: mostly ASCII (the non-ASCII is a tiny fraction) → should be QP
    # Use a long ASCII HTML body to ensure QP wins for the html part.
    ascii_html = "<div>" + "hello world " * 50 + "</div>"

    boundary = "000000000000aabbccddeeff0011"
    crlf = "\r\n"

    raw = assemble_alternative(
        [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Mixed")],
        hebrew_plain,
        ascii_html,
        boundary,
    )

    plain_marker = f"--{boundary}{crlf}Content-Type: text/plain"
    html_marker = f"--{boundary}{crlf}Content-Type: text/html"
    closing = f"--{boundary}--"

    plain_start = raw.index(plain_marker)
    html_start = raw.index(html_marker)
    closing_start = raw.index(closing)

    plain_block = raw[plain_start:html_start]
    html_block = raw[html_start:closing_start]

    # Plain part: base64
    assert "Content-Transfer-Encoding: base64" in plain_block, (
        "Hebrew plain part should use base64"
    )
    # HTML part: quoted-printable (ASCII body)
    assert "Content-Transfer-Encoding: quoted-printable" in html_block, (
        "ASCII html part should use quoted-printable"
    )

    # Verify plain round-trip
    plain_body_offset = plain_block.index(crlf + crlf) + len(crlf + crlf)
    plain_decoded = _b64.b64decode(
        plain_block[plain_body_offset:].replace(crlf, "")
    ).decode("utf-8")
    assert plain_decoded == hebrew_plain

    # Verify html round-trip (strip trailing CRLF part separator before decoding)
    html_body_offset = html_block.index(crlf + crlf) + len(crlf + crlf)
    html_body = html_block[html_body_offset:].rstrip(crlf)
    html_decoded = quopri.decodestring(html_body.encode("ascii")).decode("utf-8")
    assert html_decoded.replace("\r\n", "\n") == ascii_html
