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
    golden = json.loads((FIX / "golden_reply.json").read_text(encoding="utf-8"))
    assert BLOCKQUOTE_STYLE == golden["html_probes"]["blockquote_style"]


def _tags(html: str) -> str:
    """Sanitize HTML and return all tags as a single string for substring checks."""
    return "".join(
        re.sub(r'href="[^"]*"', 'href="‹h›"', t)
        for t in re.findall(r"<[^>]+>", sanitize_html(html))
    )


def test_forward_container_matches_golden_skeleton():
    """Verify builder output yields probes matching golden fixture values."""
    golden = json.loads((FIX / "golden_forward.json").read_text(encoding="utf-8"))
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
    golden = json.loads((FIX / "golden_forward.json").read_text(encoding="utf-8"))
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
    """When from_name is whitespace only, treat as empty - omit sendername."""
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
    """Whitespace-only from_name is treated as empty - bare email."""
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
    golden = json.loads((FIX / "golden_forward.json").read_text(encoding="utf-8"))
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
