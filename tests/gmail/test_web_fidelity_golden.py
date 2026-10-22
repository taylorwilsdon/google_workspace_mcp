"""Gmail-web fidelity of the reply/forward builders and MIME assembly.

Builder output is checked against the sanitized golden skeletons captured from
real Gmail-web mail (see fixtures/README.md).
"""

import json
import pathlib
import re
from email import message_from_string

import pytest

from gmail.gmail_web_mime import (
    BLOCKQUOTE_STYLE,
    assemble_alternative,
    build_forwarded_container_html,
    build_forwarded_plain,
    choose_cte,
)
from tests.gmail.golden_skeleton import sanitize_html

FIX = pathlib.Path(__file__).parent / "fixtures"
GOLDEN_FORWARD = json.loads((FIX / "golden_forward.json").read_text(encoding="utf-8"))
GOLDEN_REPLY = json.loads((FIX / "golden_reply.json").read_text(encoding="utf-8"))

FORWARD_FIELDS = {
    "from_name": "Jane Roe",
    "from_email": "jane@example.com",
    "date": "Mon, 2 Jun 2025 at 14:05",
    "subject": "Quarterly Report",
    "to": "joe@example.com",
}


def _container(orig_html="<div>original content</div>", **overrides):
    return build_forwarded_container_html(
        **{**FORWARD_FIELDS, **overrides}, orig_html=orig_html
    )


def _plain(orig_plain="line one\nline two", **overrides):
    return build_forwarded_plain(
        **{**FORWARD_FIELDS, **overrides}, orig_plain=orig_plain
    )


def test_blockquote_style_matches_current_golden():
    assert BLOCKQUOTE_STYLE == GOLDEN_REPLY["html_probes"]["blockquote_style"]


def test_forward_container_matches_golden_probes():
    html = _container()
    probes = GOLDEN_FORWARD["html_probes"]
    assert ('class="gmail_quote gmail_quote_container"' in html) == probes[
        "has_gmail_quote_container"
    ]
    assert ('class="gmail_attr"' in html) == probes["has_gmail_attr"]
    assert ("gmail_sendername" in html) == probes["has_gmail_sendername"]
    assert ("blockquote" in html) == probes["has_blockquote_gmail_quote"]
    assert ("Forwarded message" in html) == probes["has_forwarded_literal"]
    tags = "".join(re.findall(r"<[^>]+>", sanitize_html(html)))
    assert 'class="gmail_quote gmail_quote_container"' in tags
    assert 'class="gmail_sendername"' in tags
    assert "blockquote" not in tags


def test_forward_container_html_layout():
    assert _container() == (
        '<div class="gmail_quote gmail_quote_container">'
        '<div dir="ltr" class="gmail_attr">'
        "---------- Forwarded message ---------<br>"
        'From: <strong class="gmail_sendername" dir="auto">Jane Roe</strong> '
        '<span dir="auto">&lt;<a href="mailto:jane@example.com">jane@example.com</a>'
        "&gt;</span><br>"
        "Date: Mon, 2 Jun 2025 at 14:05<br>"
        "Subject: Quarterly Report<br>"
        'To: <a href="mailto:joe@example.com">joe@example.com</a><br>'
        "</div><div>original content</div></div>"
    )


def test_forward_container_html_without_from_name():
    html = _container(from_name="")
    assert "gmail_sendername" not in html
    assert 'From: <span dir="auto">&lt;<a href="mailto:jane@example.com">' in html


@pytest.mark.parametrize(
    ("to", "expected"),
    [
        ("addr@example.com", '<a href="mailto:addr@example.com">addr@example.com</a>'),
        (
            '"Jane Roe" <jane@example.com>',
            'Jane Roe &lt;<a href="mailto:jane@example.com">jane@example.com</a>&gt;',
        ),
        (
            "a@example.com, b@example.com",
            '<a href="mailto:a@example.com">a@example.com</a>, '
            '<a href="mailto:b@example.com">b@example.com</a>',
        ),
        (
            '"<img src=x onerror=alert(1)>" <a@example.com>',
            "&lt;img src=x onerror=alert(1)&gt; "
            '&lt;<a href="mailto:a@example.com">a@example.com</a>&gt;',
        ),
        ("", ""),
    ],
)
def test_forward_container_renders_to_recipients(to, expected):
    assert f"To: {expected}<br></div>" in _container(to=to)


@pytest.mark.parametrize(
    ("field", "value", "escaped"),
    [
        ("from_name", "Ångström & Müller", "Ångström &amp; Müller"),
        ("from_email", "a&b@example.com", "a&amp;b@example.com"),
        ("subject", "Subject <with> &special", "Subject &lt;with&gt; &amp;special"),
        ("date", '<script>alert("x")</script>', "&lt;script&gt;alert(&quot;x&quot;)"),
    ],
)
def test_forward_container_escapes_header_values(field, value, escaped):
    html = _container(**{field: value})
    assert escaped in html
    assert value not in html


def test_forward_plain_layout():
    # The original body follows verbatim, not "> "-quoted.
    assert _plain() == (
        "---------- Forwarded message ---------\n"
        "From: Jane Roe <jane@example.com>\n"
        "Date: Mon, 2 Jun 2025 at 14:05\n"
        "Subject: Quarterly Report\n"
        "To: joe@example.com\n"
        "\n"
        "line one\nline two"
    )


@pytest.mark.parametrize(
    ("from_name", "from_email", "expected"),
    [
        ("", "anon@example.com", "From: anon@example.com\n"),
        ("Undisclosed sender", "", "From: Undisclosed sender\n"),
    ],
)
def test_forward_plain_from_line_with_missing_part(from_name, from_email, expected):
    assert expected in _plain(from_name=from_name, from_email=from_email)


def test_forward_plain_scaffold_matches_golden():
    text = _plain()
    scaffold = GOLDEN_FORWARD["plain_scaffold"]
    separator = next(line for line in scaffold if line.startswith("FWD_SEP:"))
    assert separator.split(": ", 1)[1] in text
    labels = [
        line.split(": ", 2)[1] for line in scaffold if line.startswith("FWD_HDR:")
    ]
    positions = [text.index(f"{label}: ") for label in labels]
    assert positions == sorted(positions)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "quoted-printable"),
        ("plain ascii text", "quoted-printable"),
        # One accented char: QP adds a few bytes, base64 inflates everything.
        ("a" * 200 + "é", "quoted-printable"),
        ("שלום" * 20, "base64"),
        ("你好世界" * 20, "base64"),
    ],
)
def test_choose_cte(text, expected):
    assert choose_cte(text) == expected


@pytest.mark.parametrize(
    ("plain", "html", "ctes"),
    [
        (
            "Hello from the test suite.\nSecond line.",
            "<div>Hello from the test suite.</div><div>Second line.</div>",
            ["quoted-printable", "quoted-printable"],
        ),
        # Each part picks its own encoding.
        (
            "שלום עולם" * 20,
            "<div>" + "hello world " * 50 + "</div>",
            ["base64", "quoted-printable"],
        ),
    ],
)
def test_assemble_alternative_encodes_each_part(plain, html, ctes):
    raw = assemble_alternative(
        [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "Hi")],
        plain,
        html,
        "000000000000aabbccddeeff0011",
    )
    parts = message_from_string(raw).get_payload()
    assert [p["Content-Transfer-Encoding"] for p in parts] == ctes
    decoded = [
        p.get_payload(decode=True).decode("utf-8").replace("\r\n", "\n") for p in parts
    ]
    assert decoded == [plain, html]


_PDF_DATA = b"%PDF-1.4 fake"


_PNG_DATA = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


def test_assemble_mixed_tree():
    from gmail.gmail_web_mime import assemble_mixed
    from tests.gmail.golden_skeleton import extract_skeleton

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
    from tests.gmail.golden_skeleton import extract_skeleton

    golden = json.loads(
        (FIX / "golden_forward_attach.json").read_text(encoding="utf-8")
    )
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
    from tests.gmail.golden_skeleton import extract_skeleton

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
    from tests.gmail.golden_skeleton import extract_skeleton

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


def test_web_message_attach_only_matches_assemble_mixed(monkeypatch):
    """Attachments-only path must produce byte-identical output to assemble_mixed."""
    from gmail import gmail_web_mime
    from gmail.gmail_web_mime import assemble_mixed, assemble_web_message

    # Attachment ids are random per part; pin them so the outputs compare.
    monkeypatch.setattr(gmail_web_mime, "_new_attachment_id", lambda: "f_0000000000")

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
    from tests.gmail.golden_skeleton import extract_skeleton

    golden = json.loads(
        (pathlib.Path(__file__).parent / "fixtures" / "golden_inline.json").read_text(
            encoding="utf-8"
        )
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
    from tests.gmail.golden_skeleton import extract_skeleton

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
    from tests.gmail.golden_skeleton import extract_skeleton

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


@pytest.mark.parametrize("with_inline", [False, True])
def test_web_message_folds_long_recipient_headers(with_inline):
    """Attachment and inline assembly fold headers like the alternative path."""
    from gmail.gmail_web_mime import assemble_web_message

    to = ", ".join(f"Person Number {i} <person{i}@example.com>" for i in range(40))
    inline = [
        {
            "filename": "i.png",
            "mime_type": "image/png",
            "data": _PNG_DATA,
            "content_id": "ii_1",
        }
    ]
    raw = assemble_web_message(
        headers=[("From", "a@example.com"), ("To", to), ("Subject", "S " * 60)],
        plain_text="p",
        html_text="<div>p</div>",
        inline_parts=inline if with_inline else None,
        attachment_parts=[
            {"filename": "a.pdf", "mime_type": "application/pdf", "data": _PDF_DATA}
        ],
        boundary_alt="000000000000aaaaaaaaaaaaaaaa",
        boundary_related="000000000000cccccccccccccccc" if with_inline else None,
        boundary_mixed="000000000000bbbbbbbbbbbbbbbb",
    )
    head = raw.split("\r\n\r\n", 1)[0]
    assert max(len(line) for line in head.split("\r\n")) <= 78
    assert "person39@example.com" in head
