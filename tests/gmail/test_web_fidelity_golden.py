import re


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
