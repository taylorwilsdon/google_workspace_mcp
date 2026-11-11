"""Content preservation and wire-format regressions for web compose."""

import base64
import html
import re
from email import message_from_bytes
from email.header import decode_header, make_header
from email.policy import default

import pytest

from gmail.gmail_helpers import _derive_web_bodies, _prepare_gmail_message_web
from gmail.gmail_tools import _forward_gmail_message_impl
from tests.gmail.test_forward_gmail_message import (
    create_mock_message,
    create_mock_service,
    get_body_text,
    get_sent_mime_message,
)


@pytest.mark.parametrize(
    "field,value",
    [
        ("subject", "word " * 250),
        ("subject", "山" * 120),
        ("from_name", "山" * 120),
        ("to", ", ".join(f"user{i}@example.com" for i in range(75))),
        ("cc", "山" * 120 + " <person@example.com>"),
        ("bcc", "山" * 120 + ": person@example.com;"),
    ],
    ids=[
        "ascii-subject",
        "unicode-subject",
        "unicode-from",
        "recipient-list",
        "unicode-cc",
        "unicode-group",
    ],
)
def test_web_headers_obey_line_and_encoded_word_limits(field, value):
    fields = dict(
        subject="Hello",
        plain_body="Body",
        html_body="<div>Body</div>",
        from_email="me@example.com",
        to="you@example.com",
    )
    fields[field] = value
    raw = base64.urlsafe_b64decode(_prepare_gmail_message_web(**fields))
    headers = raw.split(b"\r\n\r\n", 1)[0]
    assert max(map(len, headers.split(b"\r\n"))) <= 998
    words = re.findall(rb"=\?[^?]+\?[bBqQ]\?[^?]*\?=", headers)
    assert all(len(word) <= 75 for word in words), list(map(len, words))
    parsed = message_from_bytes(raw, policy=default)
    if field == "subject":
        assert str(parsed["Subject"]) == value
    elif field == "from_name":
        assert (
            str(make_header(decode_header(message_from_bytes(raw)["From"])))
            == value + " <me@example.com>"
        )
    elif field == "to":
        assert [a.addr_spec for a in parsed["To"].addresses] == value.split(", ")
    elif field == "cc":
        assert str(make_header(decode_header(message_from_bytes(raw)["Cc"]))) == value
    else:
        group_name = message_from_bytes(raw)["Bcc"].partition(":")[0]
        assert str(make_header(decode_header(group_name))) == "山" * 120
        assert parsed["Bcc"].addresses[0].addr_spec == "person@example.com"


@pytest.mark.parametrize(
    "body",
    ["if ok:\n    run()\nA    B", "  leading and trailing  ", "one\rtwo", "a\tvalue"],
)
def test_plain_compose_preserves_visible_whitespace(body):
    _, markup = _derive_web_bodies(body, "plain", "auto")
    # Each generated line is a div. Apply HTML's normal ASCII whitespace
    # collapsing to its text, leaving nonbreaking spaces intact.
    lines = re.findall(r"<div>(.*?)</div>", markup, re.S)
    rendered = "\n".join(
        re.sub(r"[ \t\r\n\f]+", " ", html.unescape(line))
        .strip(" ")
        .replace("\xa0", " ")
        for line in lines
    )
    assert rendered == body.replace("\r\n", "\n").replace("\r", "\n").expandtabs()


@pytest.mark.asyncio
@pytest.mark.parametrize("as_draft", [False, True])
@pytest.mark.parametrize(
    "original_html", [None, '<a href="https://example.com">View details</a>']
)
async def test_forward_attachment_preserves_original_plain_part(
    as_draft, original_html
):
    original = "if ok:\n    run()\n\n\nA    B\nhttps://example.com"
    service = create_mock_service(
        create_mock_message(text_body=original, html_body=original_html)
    )
    await _forward_gmail_message_impl(
        service=service,
        message_id="original",
        to="you@example.com",
        user_google_email="me@example.com",
        as_draft=as_draft,
        attachments=[{"filename": "file.txt", "content": "eA=="}],
    )
    if as_draft:
        raw = service.users().drafts().create.call_args.kwargs["body"]["message"]["raw"]
        message = message_from_bytes(base64.urlsafe_b64decode(raw))
    else:
        message = get_sent_mime_message(service)
    assert original in get_body_text(message, "plain").replace("\r\n", "\n")
    assert sum(p.get_content_disposition() == "attachment" for p in message.walk()) == 1
