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
