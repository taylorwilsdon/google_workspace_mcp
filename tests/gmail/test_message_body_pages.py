"""Read complete message bodies through bounded calls to the existing tool."""

import base64
import json
from email.message import EmailMessage
from unittest.mock import Mock

import pytest

from core.utils import UserInputError
from core.file_limits import FileTooLargeError
from fastmcp.exceptions import ToolError
from gmail.gmail_tools import _format_body_content, get_gmail_message_content


def unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


read_message = unwrap(get_gmail_message_content)


def gmail_service(message):
    raw = message.as_bytes()
    service = Mock()

    def get(**kwargs):
        response = (
            {"sizeEstimate": len(raw), "payload": {"headers": []}}
            if kwargs["format"] == "metadata"
            else {"raw": base64.urlsafe_b64encode(raw).decode()}
        )
        return Mock(execute=Mock(return_value=response))

    service.users.return_value.messages.return_value.get.side_effect = get
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("body_format", ["text", "html"])
async def test_complete_unicode_body_in_bounded_pages(body_format):
    html = "<p>" + "漢😀 &amp; text " * 3000 + "LAST ENTRY</p>"
    message = EmailMessage()
    message.set_content("Your client does not support HTML")
    message.add_alternative(html, subtype="html")
    service = gmail_service(message)
    assert "LAST ENTRY" not in _format_body_content("", html, body_format)

    offset, digest, chunks = 0, None, []
    while True:
        page = json.loads(
            await read_message(
                service,
                "message-1",
                "reader@example.com",
                body_format=body_format,
                page_offset=offset,
                page_size=7001,
                expected_sha256=digest,
            )
        )
        assert len(page["body"]) <= 7001
        assert "[Content truncated...]" not in page["body"]
        assert page["page_offset"] == offset
        chunks.append(page["body"])
        digest = page["body_sha256"]
        if page["complete"]:
            assert page["next_offset"] is None
            break
        offset = page["next_offset"]
    complete = "".join(chunks)
    assert "LAST ENTRY" in complete
    assert page["total_chars"] == len(complete)
    assert complete == _format_body_content(
        "Your client does not support HTML", html, body_format, truncate_limit=None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"full": True},
        {"format": "metadata"},
        {"body_format": "raw"},
        {"page_offset": -1},
        {"page_size": 0},
        {"page_size": 20001},
    ],
)
async def test_invalid_page_options_fail_before_fetch(options):
    service = Mock()
    with pytest.raises(UserInputError):
        await read_message(
            service, "message-1", "reader@example.com", **{"page_offset": 0, **options}
        )
    service.users.assert_not_called()


@pytest.mark.asyncio
async def test_plain_body_preserves_charset_and_excludes_attachments():
    message = EmailMessage()
    message.set_content("Résumé and café", charset="iso-8859-1")
    attached = EmailMessage()
    attached.set_content("ATTACHED MESSAGE")
    message.add_attachment(attached)
    message.add_attachment("TEXT ATTACHMENT", filename="note.txt")
    page = json.loads(
        await read_message(
            gmail_service(message),
            "message-1",
            "reader@example.com",
            page_offset=0,
            include_headers=False,
        )
    )
    assert page["body"] == "Résumé and café\n"
    assert page["complete"]
    assert "headers" not in page


@pytest.mark.asyncio
async def test_related_body_uses_start_part_and_pages_links():
    message = EmailMessage()
    message.make_related()
    message.set_param("start", "<body>")
    image = EmailMessage()
    image.set_content(b"image", maintype="image", subtype="png")
    image["Content-ID"] = "<image>"
    body = EmailMessage()
    body.set_content(
        '<p>Hello <a href="https://example.com/tail">link</a></p>', subtype="html"
    )
    body["Content-ID"] = "<body>"
    message.attach(image)
    message.attach(body)
    service = gmail_service(message)
    complete = json.loads(
        await read_message(
            service,
            "message-1",
            "reader@example.com",
            page_offset=0,
            include_links=True,
        )
    )
    assert "https://example.com/tail" in complete["body"]
    tail = json.loads(
        await read_message(
            service,
            "message-1",
            "reader@example.com",
            page_offset=complete["total_chars"] - 4,
            page_size=4,
            include_links=True,
            expected_sha256=complete["body_sha256"],
        )
    )
    assert tail["body"] == complete["body"][-4:]
    assert tail["complete"]


@pytest.mark.asyncio
async def test_empty_body_end_offset_and_changed_representation():
    message = EmailMessage()
    message.set_content("")
    service = gmail_service(message)
    first = json.loads(
        await read_message(service, "id", "reader@example.com", page_offset=0)
    )
    end = json.loads(
        await read_message(
            service,
            "id",
            "reader@example.com",
            page_offset=first["total_chars"],
            expected_sha256=first["body_sha256"],
        )
    )
    assert end["body"] == ""
    assert end["complete"] and end["next_offset"] is None
    with pytest.raises(UserInputError, match="exceeds total_chars"):
        await read_message(
            service, "id", "reader@example.com", page_offset=first["total_chars"] + 1
        )
    changed = EmailMessage()
    changed.set_content("Changed")
    with pytest.raises(UserInputError, match="representation changed"):
        await read_message(
            gmail_service(changed),
            "id",
            "reader@example.com",
            page_offset=0,
            expected_sha256=first["body_sha256"],
        )


@pytest.mark.asyncio
async def test_each_page_rechecks_current_authorized_service():
    message = EmailMessage()
    message.set_content("First account body")
    first = json.loads(
        await read_message(
            gmail_service(message), "id", "one@example.com", page_offset=0
        )
    )
    message.set_content("Second account body")
    second = json.loads(
        await read_message(
            gmail_service(message), "id", "two@example.com", page_offset=0
        )
    )
    assert first["body"] != second["body"]
    denied = Mock()
    denied.users.return_value.messages.return_value.get.return_value.execute.side_effect = PermissionError(
        "denied"
    )
    with pytest.raises(PermissionError):
        await read_message(denied, "id", "one@example.com", page_offset=3)


@pytest.mark.asyncio
async def test_size_limit_rejects_metadata_before_raw_fetch(monkeypatch):
    message = EmailMessage()
    message.set_content("x" * 2000)
    service = gmail_service(message)
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "1000")
    with pytest.raises(FileTooLargeError):
        await read_message(service, "id", "reader@example.com", page_offset=0)
    assert service.users.return_value.messages.return_value.get.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["encoded", "decoded"])
async def test_actual_size_limit_when_metadata_underestimates(monkeypatch, boundary):
    message = EmailMessage()
    message.set_content("x" * 2000)
    raw = message.as_bytes()
    while len(raw) % 3 != 2:
        raw += b"\n"
    limit = len(raw) // 2 if boundary == "encoded" else len(raw) - 1
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", str(limit))
    service = Mock()
    service.users.return_value.messages.return_value.get.return_value.execute.side_effect = [
        {"sizeEstimate": 1},
        {"raw": base64.urlsafe_b64encode(raw).decode()},
    ]
    error = ToolError if boundary == "encoded" else FileTooLargeError
    with pytest.raises(error):
        await read_message(service, "id", "reader@example.com", page_offset=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, "not valid base64!"])
async def test_missing_or_invalid_raw_does_not_claim_complete(raw):
    service = Mock()
    service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "raw": raw
    }
    with pytest.raises(ToolError):
        await read_message(service, "id", "reader@example.com", page_offset=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{"expected_sha256": "hash"}, {"page_size": 10}])
async def test_continuation_options_require_offset(options):
    service = Mock()
    with pytest.raises(UserInputError, match="require page_offset"):
        await read_message(service, "id", "reader@example.com", **options)
    service.users.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing-boundary", "deep-nesting"])
async def test_malformed_or_deep_mime_does_not_claim_complete(case):
    raw = b'Content-Type: multipart/mixed; boundary="missing"\n\nUNPARSED BODY'
    expected = "Malformed multipart"
    if case == "deep-nesting":
        raw = b"Content-Type: text/plain\n\nDeep body"
        for level in range(350):
            raw = (
                f'Content-Type: multipart/mixed; boundary="b{level}"\n\n--b{level}\n'.encode()
                + raw
                + f"\n--b{level}--\n".encode()
            )
        expected = "nesting is too deep"
    service = Mock()
    service.users.return_value.messages.return_value.get.return_value.execute.side_effect = [
        {"sizeEstimate": len(raw)},
        {"raw": base64.urlsafe_b64encode(raw).decode()},
    ]
    with pytest.raises(ToolError, match=expected):
        await read_message(service, "id", "reader@example.com", page_offset=0)


@pytest.mark.asyncio
async def test_attached_international_message_is_excluded():
    message = EmailMessage()
    message.set_content("Visible outer body")
    message.make_mixed()
    nested = EmailMessage()
    nested.set_content("Attached international message")
    attachment = EmailMessage()
    attachment["Content-Type"] = "message/global"
    attachment.set_payload([nested])
    message.attach(attachment)
    page = json.loads(
        await read_message(
            gmail_service(message),
            "id",
            "reader@example.com",
            page_offset=0,
        )
    )
    assert page["body"] == "Visible outer body\n"
    assert page["complete"]
