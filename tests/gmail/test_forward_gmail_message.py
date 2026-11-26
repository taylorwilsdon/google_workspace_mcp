"""Unit tests for the forward path of send/draft_gmail_message."""

import base64
from email import message_from_bytes
from unittest.mock import Mock

import pytest
from fastmcp.exceptions import ToolError

from core.utils import UserInputError
from gmail.gmail_tools import _forward_gmail_message_impl


def get_sent_mime_message(mock_service):
    """Decode the raw MIME message passed to messages().send()."""
    raw = mock_service.users().messages().send.call_args.kwargs["body"]["raw"]
    return message_from_bytes(base64.urlsafe_b64decode(raw))


def get_body_and_attachments(mime_msg):
    """Return (body_part, [attachment_parts]) for plain or multipart messages."""
    if not mime_msg.is_multipart():
        return mime_msg, []

    body_part = None
    attachments = []
    for part in mime_msg.walk():
        if part.is_multipart():
            continue
        if part.get_content_disposition() == "attachment":
            attachments.append(part)
            continue
        if body_part is None and part.get_content_maintype() == "text":
            body_part = part

    return body_part, attachments


def get_body_part(mime_msg, subtype=None):
    """Return the first text body part, optionally matching a specific subtype."""
    if not mime_msg.is_multipart():
        if subtype is None or mime_msg.get_content_subtype() == subtype:
            return mime_msg
        return None

    for part in mime_msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        if part.get_content_maintype() != "text":
            continue
        if subtype is None or part.get_content_subtype() == subtype:
            return part

    return None


def get_body_text(mime_msg, subtype=None):
    """Decode the text/body part of a sent MIME message to a string."""
    body_part = get_body_part(mime_msg, subtype=subtype)
    assert body_part is not None
    return body_part.get_payload(decode=True).decode()


def create_mock_message(
    subject="Test Subject",
    from_addr="sender@example.com",
    to_addr="orig@example.com",
    date="Mon, 1 Jan 2024 10:00:00 -0000",
    text_body=None,
    html_body=None,
    attachments=None,
):
    """Create a mock Gmail message payload."""
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": from_addr},
        {"name": "To", "value": to_addr},
        {"name": "Date", "value": date},
    ]

    parts = []

    if text_body:
        encoded_text = base64.urlsafe_b64encode(text_body.encode()).decode()
        parts.append({"mimeType": "text/plain", "body": {"data": encoded_text}})

    if html_body:
        encoded_html = base64.urlsafe_b64encode(html_body.encode()).decode()
        parts.append({"mimeType": "text/html", "body": {"data": encoded_html}})

    if attachments:
        for att in attachments:
            parts.append(
                {
                    "filename": att["filename"],
                    "mimeType": att["mimeType"],
                    "body": {
                        "attachmentId": att["attachmentId"],
                        "size": att.get("size", 100),
                    },
                }
            )

    if parts:
        payload = {"mimeType": "multipart/mixed", "headers": headers, "parts": parts}
    else:
        # Simple message with no parts
        encoded_text = base64.urlsafe_b64encode(b"").decode()
        payload = {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {"data": encoded_text},
        }

    return {"payload": payload}


def create_mock_service(message, attachments_data=None, sent_message_id="sent123"):
    """Create a mock Gmail service with chained method calls."""
    mock = Mock()

    # Setup chain: service.users().messages().get()
    mock.users().messages().get().execute.return_value = message

    # Setup chain: service.users().messages().attachments().get()
    if attachments_data:
        mock.users().messages().attachments().get().execute.side_effect = (
            attachments_data
        )
    else:
        mock.users().messages().attachments().get().execute.return_value = {"data": ""}

    # Setup chain: service.users().messages().send()
    mock.users().messages().send().execute.return_value = {"id": sent_message_id}

    return mock


@pytest.mark.asyncio
async def test_forward_simple_text_email():
    """Forward plain text email, no attachments"""
    message = create_mock_message(
        subject="Hello",
        from_addr="alice@example.com",
        to_addr="bob@example.com",
        text_body="This is the body.",
    )
    mock_service = create_mock_service(message, sent_message_id="fwd001")

    result = await _forward_gmail_message_impl(
        service=mock_service,
        message_id="msg123",
        to="recipient@example.com",
        user_google_email="me@example.com",
    )

    assert "Email forwarded" in result
    assert "fwd001" in result
    mock_service.users().messages().send.assert_called()
    mock_service.users().messages().send.return_value.execute.assert_called_once_with(
        num_retries=0
    )

    sent = get_sent_mime_message(mock_service)
    assert sent["To"] == "recipient@example.com"
    body_part, attachments = get_body_and_attachments(sent)
    assert body_part.get_content_subtype() == "plain"
    assert not attachments
    body = get_body_text(sent)
    assert "This is the body." in body
    assert "Forwarded message" in body
    assert "alice@example.com" in body


@pytest.mark.asyncio
async def test_forward_reports_unknown_delivery_on_ambiguous_failure():
    message = create_mock_message(
        subject="Hello",
        from_addr="alice@example.com",
        to_addr="bob@example.com",
        text_body="This is the body.",
    )
    mock_service = create_mock_service(message)
    mock_service.users().messages().send().execute.side_effect = ConnectionResetError(
        "reset by peer"
    )

    with pytest.raises(ToolError, match="Check the Sent folder before sending again"):
        await _forward_gmail_message_impl(
            service=mock_service,
            message_id="msg123",
            to="recipient@example.com",
            user_google_email="me@example.com",
        )


@pytest.mark.asyncio
async def test_forward_html_only_original_fills_plain_part():
    """An HTML-only original still yields its body in the text/plain part."""
    message = create_mock_message(
        subject="HTML only",
        from_addr="alice@example.com",
        to_addr="bob@example.com",
        html_body="<p>First paragraph.</p><p>Second paragraph.</p>",
    )
    mock_service = create_mock_service(message, sent_message_id="fwd003")

    await _forward_gmail_message_impl(
        service=mock_service,
        message_id="msg789",
        to="recipient@example.com",
        user_google_email="me@example.com",
    )

    plain = get_body_text(get_sent_mime_message(mock_service), subtype="plain")
    assert "Forwarded message" in plain
    assert "First paragraph." in plain
    assert "Second paragraph." in plain
    assert "First paragraph.Second paragraph." not in plain
    assert "<p>" not in plain


@pytest.mark.asyncio
async def test_forward_with_message_plain():
    """Forward with plain text user message prepended"""
    message = create_mock_message(
        subject="FYI",
        from_addr="alice@example.com",
        to_addr="bob@example.com",
        text_body="Original message body.",
    )
    mock_service = create_mock_service(message, sent_message_id="fwd003")

    result = await _forward_gmail_message_impl(
        service=mock_service,
        message_id="msg789",
        to="recipient@example.com",
        forward_message="Please see below.",
        forward_message_format="plain",
        user_google_email="me@example.com",
    )

    assert "Email forwarded" in result
    assert "fwd003" in result
    sent = get_sent_mime_message(mock_service)
    assert get_body_text(sent, subtype="plain").startswith(
        "Please see below.\r\n\r\n---------- Forwarded message"
    )
    assert get_body_text(sent, subtype="html").startswith(
        '<div dir="ltr"><div>Please see below.</div><br><br>'
        '<div class="gmail_quote gmail_quote_container">'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("note", "note_format", "direction", "expected_prefix"),
    [
        # No note: nothing user-authored to orient, so "auto" stays ltr.
        (None, "plain", "auto", '<div dir="ltr"><br><div class="gmail_quote'),
        (None, "plain", "rtl", '<div dir="rtl"><br><div class="gmail_quote'),
        ("שלום, ראה למטה", "plain", "auto", '<div dir="rtl"><div>שלום'),
        # An explicit direction wins over the HTML note's own dir.
        (
            '<div dir="ltr">See below.</div>',
            "html",
            "rtl",
            '<div dir="rtl"><div dir="rtl">See below.</div>',
        ),
    ],
)
async def test_forward_html_base_direction(
    note, note_format, direction, expected_prefix
):
    """The note sets the forward's base direction; the original keeps its own."""
    mock_service = create_mock_service(
        create_mock_message(text_body="Original message body.")
    )

    await _forward_gmail_message_impl(
        service=mock_service,
        message_id="msg789",
        to="recipient@example.com",
        forward_message=note,
        forward_message_format=note_format,
        user_google_email="me@example.com",
        direction=direction,
    )

    body = get_body_text(get_sent_mime_message(mock_service), subtype="html")
    assert body.startswith(expected_prefix)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("date", "expected"),
    [
        ("Sat, 28 Mar 2026 09:05:00 -0400", "Sat, 28 Mar 2026 at 9:05"),
        ("sometime last week", "sometime last week"),
    ],
)
async def test_forward_header_formats_date_like_gmail(date, expected):
    """The forwarded Date line uses Gmail's format in the sender's own offset;
    an unparseable Date passes through verbatim."""
    message = create_mock_message(date=date, text_body="Original message body.")
    mock_service = create_mock_service(message)

    await _forward_gmail_message_impl(
        service=mock_service,
        message_id="msg789",
        to="recipient@example.com",
        user_google_email="me@example.com",
    )

    sent = get_sent_mime_message(mock_service)
    assert f"Date: {expected}\r\n" in get_body_text(sent, subtype="plain")
    assert f"Date: {expected}<br>" in get_body_text(sent, subtype="html")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("from_header", "expected_from"),
    [(None, "(unknown sender)"), ("Undisclosed sender", "Undisclosed sender")],
)
async def test_forward_header_without_sender_address(from_header, expected_from):
    """A From with no usable address renders as text, never a dead mailto link."""
    message = create_mock_message(text_body="Original message body.")
    headers = message["payload"]["headers"]
    headers[:] = [h for h in headers if h["name"] not in ("From", "Date")]
    if from_header:
        headers.append({"name": "From", "value": from_header})
    mock_service = create_mock_service(message)

    await _forward_gmail_message_impl(
        service=mock_service,
        message_id="msg789",
        to="recipient@example.com",
        user_google_email="me@example.com",
    )

    sent = get_sent_mime_message(mock_service)
    plain = get_body_text(sent, subtype="plain")
    html_body = get_body_text(sent, subtype="html")
    assert f"From: {expected_from}\r\nDate: (unknown date)\r\n" in plain
    assert (
        f'From: <strong class="gmail_sendername" dir="auto">{expected_from}'
        in html_body
    )
    assert "mailto:" not in html_body.split("To:")[0]


@pytest.mark.asyncio
async def test_forward_header_blank_sender_name_renders_bare_address():
    """A whitespace-only display name is dropped rather than shown as a name."""
    message = create_mock_message(
        from_addr='"  " <anon@example.com>', text_body="Original message body."
    )
    mock_service = create_mock_service(message)

    await _forward_gmail_message_impl(
        service=mock_service,
        message_id="msg789",
        to="recipient@example.com",
        user_google_email="me@example.com",
    )

    sent = get_sent_mime_message(mock_service)
    assert "From: anon@example.com\r\n" in get_body_text(sent, subtype="plain")
    html_body = get_body_text(sent, subtype="html")
    assert "gmail_sendername" not in html_body
    assert 'From: <span dir="auto">&lt;<a href="mailto:anon@example.com">' in html_body


@pytest.mark.asyncio
async def test_forward_with_message_html():
    """Forward with HTML user message prepended"""
    message = create_mock_message(
        subject="Important",
        from_addr="alice@example.com",
        to_addr="bob@example.com",
        html_body="<p>Original HTML content.</p>",
    )
    mock_service = create_mock_service(message, sent_message_id="fwd004")

    result = await _forward_gmail_message_impl(
        service=mock_service,
        message_id="msgabc",
        to="recipient@example.com",
        forward_message="<b>Note:</b> See below.",
        forward_message_format="html",
        user_google_email="me@example.com",
    )

    assert "Email forwarded" in result
    assert "fwd004" in result

    sent = get_sent_mime_message(mock_service)
    body_part = get_body_part(sent, subtype="html")
    assert body_part is not None
    assert body_part.get_content_subtype() == "html"
    body = get_body_text(sent, subtype="html")
    assert "<b>Note:</b> See below." in body
    assert "Original HTML content." in body


@pytest.mark.asyncio
async def test_forward_plain_original_with_html_note():
    """Plain-text original + HTML note should produce an HTML forward body."""
    message = create_mock_message(
        subject="Plain Original",
        from_addr="alice@example.com",
        to_addr="bob@example.com",
        text_body="Line one\nLine two",
    )
    mock_service = create_mock_service(message, sent_message_id="fwd009")

    result = await _forward_gmail_message_impl(
        service=mock_service,
        message_id="msgpqr",
        to="recipient@example.com",
        forward_message="<b>Heads up</b>",
        forward_message_format="html",
        user_google_email="me@example.com",
    )

    assert "Email forwarded" in result
    assert "fwd009" in result

    sent = get_sent_mime_message(mock_service)
    body_part = get_body_part(sent, subtype="html")
    assert body_part is not None
    # HTML format must be honored even though the original was plain text.
    assert body_part.get_content_subtype() == "html"
    body = get_body_text(sent, subtype="html")
    assert "<b>Heads up</b>" in body
    # The plain-text original is escaped and newline-converted into the HTML body.
    assert "<div>Line one</div><div>Line two</div>" in body


@pytest.mark.asyncio
async def test_forward_without_attachments():
    """Forward with include_attachments=False"""
    message = create_mock_message(
        subject="With Attachment",
        from_addr="alice@example.com",
        to_addr="bob@example.com",
        text_body="Message body.",
        attachments=[
            {
                "filename": "doc.pdf",
                "mimeType": "application/pdf",
                "attachmentId": "att1",
            }
        ],
    )
    mock_service = create_mock_service(message, sent_message_id="fwd005")

    result = await _forward_gmail_message_impl(
        service=mock_service,
        message_id="msgdef",
        to="recipient@example.com",
        include_attachments=False,
        user_google_email="me@example.com",
    )

    assert "Email forwarded" in result
    assert "fwd005" in result
    # Should not include attachments in result
    assert "attachment(s)" not in result


@pytest.mark.asyncio
async def test_forward_with_attachments():
    """Forward with attachments, mock attachment download"""
    message = create_mock_message(
        subject="With Attachment",
        from_addr="alice@example.com",
        to_addr="bob@example.com",
        text_body="See attached.",
        attachments=[
            {
                "filename": "report.pdf",
                "mimeType": "application/pdf",
                "attachmentId": "att1",
            },
            {"filename": "image.png", "mimeType": "image/png", "attachmentId": "att2"},
        ],
    )

    # Mock attachment data. Use raw bytes that yield URL-safe chars (-/_) and an
    # unpadded encoding to exercise the base64 normalization path.
    att1_raw = b"PDF content \xfb\xff"
    att2_raw = b"PNG content \xfb\xef"
    att1_data = base64.urlsafe_b64encode(att1_raw).decode().rstrip("=")
    att2_data = base64.urlsafe_b64encode(att2_raw).decode().rstrip("=")
    attachments_data = [{"data": att1_data}, {"data": att2_data}]

    mock_service = create_mock_service(
        message, attachments_data=attachments_data, sent_message_id="fwd006"
    )

    result = await _forward_gmail_message_impl(
        service=mock_service,
        message_id="msgghi",
        to="recipient@example.com",
        include_attachments=True,
        user_google_email="me@example.com",
    )

    assert "Email forwarded" in result
    assert "2 attachment(s)" in result
    assert "fwd006" in result

    sent = get_sent_mime_message(mock_service)
    _, attachments = get_body_and_attachments(sent)
    assert len(attachments) == 2
    filenames = [a.get_filename() for a in attachments]
    assert filenames == ["report.pdf", "image.png"]
    # Attachment payloads round-trip back to the original bytes despite the
    # unpadded URL-safe input.
    assert attachments[0].get_payload(decode=True) == att1_raw
    assert attachments[1].get_payload(decode=True) == att2_raw


@pytest.mark.asyncio
async def test_forward_attachment_download_failure_raises():
    """A failed attachment download must abort rather than send a partial forward."""
    message = create_mock_message(
        subject="Has attachment",
        from_addr="alice@example.com",
        to_addr="bob@example.com",
        text_body="See attached.",
        attachments=[
            {
                "filename": "report.pdf",
                "mimeType": "application/pdf",
                "attachmentId": "att1",
            }
        ],
    )
    mock_service = create_mock_service(
        message, attachments_data=[Exception("boom")], sent_message_id="fwd011"
    )

    with pytest.raises(Exception, match="Failed to include requested attachment"):
        await _forward_gmail_message_impl(
            service=mock_service,
            message_id="msgvwx",
            to="recipient@example.com",
            include_attachments=True,
            user_google_email="me@example.com",
        )


@pytest.mark.asyncio
async def test_forward_rejects_oversized_attachment_before_download(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "100")
    message = create_mock_message(
        subject="Oversized attachment",
        text_body="See attached.",
        attachments=[
            {
                "filename": "huge.zip",
                "mimeType": "application/zip",
                "attachmentId": "att-large",
                "size": 500,
            }
        ],
    )
    mock_service = create_mock_service(message)
    attachment_get = mock_service.users().messages().attachments().get
    attachment_get.reset_mock()
    send_execute = mock_service.users().messages().send().execute
    send_execute.reset_mock()

    with pytest.raises(UserInputError, match="huge.zip.*too large"):
        await _forward_gmail_message_impl(
            service=mock_service,
            message_id="msg-large",
            to="recipient@example.com",
            include_attachments=True,
            user_google_email="me@example.com",
        )

    attachment_get.assert_not_called()
    send_execute.assert_not_called()


@pytest.mark.asyncio
async def test_forward_with_cc_bcc():
    """Forward with CC and BCC recipients"""
    message = create_mock_message(
        subject="Team Update",
        from_addr="alice@example.com",
        to_addr="bob@example.com",
        text_body="Team message.",
    )
    mock_service = create_mock_service(message, sent_message_id="fwd008")

    result = await _forward_gmail_message_impl(
        service=mock_service,
        message_id="msgmno",
        to="recipient@example.com",
        cc="cc@example.com",
        bcc="bcc@example.com",
        user_google_email="me@example.com",
    )

    assert "Email forwarded" in result
    assert "fwd008" in result

    sent = get_sent_mime_message(mock_service)
    assert sent["To"] == "recipient@example.com"
    assert sent["Cc"] == "cc@example.com"
    assert sent["Bcc"] == "bcc@example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("original", "override", "expected"),
    [
        ("Meeting Notes", None, "Fwd: Meeting Notes"),
        ("Fwd: Already forwarded", None, "Fwd: Already forwarded"),
        ("FW: Quarterly numbers", None, "FW: Quarterly numbers"),
        ("Original", "Custom Subject", "Custom Subject"),
    ],
)
async def test_forward_subject(original, override, expected):
    """'Fwd: ' is prefixed once, an existing Fwd:/FW: is kept, an override wins."""
    mock_service = create_mock_service(
        create_mock_message(subject=original, text_body="Body.")
    )

    await _forward_gmail_message_impl(
        service=mock_service,
        message_id="msgyz",
        to="recipient@example.com",
        subject=override,
        user_google_email="me@example.com",
    )

    assert get_sent_mime_message(mock_service)["Subject"] == expected


@pytest.mark.asyncio
async def test_forward_as_draft_keeps_attachments_and_does_not_send():
    """as_draft saves the forward, attachments included, without sending it."""
    message = create_mock_message(
        subject="Quarterly",
        from_addr="alice@example.com",
        to_addr="bob@example.com",
        text_body="See attached.",
        attachments=[
            {
                "filename": "report.pdf",
                "mimeType": "application/pdf",
                "attachmentId": "att1",
            }
        ],
    )
    mock_service = create_mock_service(
        message,
        attachments_data=[
            {"data": base64.urlsafe_b64encode(b"%PDF-1.4").decode().rstrip("=")}
        ],
    )
    mock_service.users().drafts().create().execute.return_value = {"id": "draft001"}

    result = await _forward_gmail_message_impl(
        service=mock_service,
        message_id="msgdraft",
        to=None,
        user_google_email="me@example.com",
        as_draft=True,
    )

    assert "Forward draft created" in result
    assert "draft001" in result
    mock_service.users().messages().send().execute.assert_not_called()

    draft_body = mock_service.users().drafts().create.call_args.kwargs["body"]
    drafted = message_from_bytes(base64.urlsafe_b64decode(draft_body["message"]["raw"]))
    assert drafted["Subject"] == "Fwd: Quarterly"
    assert drafted["To"] is None
    _, attachments = get_body_and_attachments(drafted)
    assert [a.get_filename() for a in attachments] == ["report.pdf"]
    assert attachments[0].get_payload(decode=True) == b"%PDF-1.4"
