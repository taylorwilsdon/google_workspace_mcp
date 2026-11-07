"""Read complete message bodies through bounded calls to the existing tool."""

import base64
import json
import sys
from email.message import EmailMessage
from unittest.mock import Mock

import pytest
from fastmcp.exceptions import ToolError, ValidationError

import gmail.gmail_helpers as helpers
from core.file_limits import FileTooLargeError
from core.server import server
from core.utils import UserInputError
from gmail.gmail_tools import _format_body_content, get_gmail_message_content


@pytest.fixture(autouse=True)
def clear_page_cache():
    helpers._PAGE_CACHE.clear()
    yield
    helpers._PAGE_CACHE.clear()


def unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


read_message = unwrap(get_gmail_message_content)


def gmail_service(message, headers=()):
    raw = message.as_bytes()
    service = Mock()

    def get(**kwargs):
        response = (
            {"sizeEstimate": len(raw), "payload": {"headers": list(headers)}}
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
    formats = [
        call.kwargs["format"]
        for call in service.users.return_value.messages.return_value.get.call_args_list
    ]
    assert formats.count("raw") == 1
    assert formats.count("metadata") == len(chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"full": True},
        {"format": "metadata"},
        {"body_format": "raw"},
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
@pytest.mark.parametrize(
    "options", [{"page_offset": -1}, {"page_size": 0}, {"page_size": 20001}]
)
async def test_page_bounds_are_schema_validated(options):
    tool = await server.get_tool("get_gmail_message_content")
    with pytest.raises(ValidationError):
        await tool.run(
            {
                "message_id": "message-1",
                "user_google_email": "reader@example.com",
                **{"page_offset": 0, **options},
            }
        )


@pytest.mark.asyncio
async def test_headers_are_returned_complete():
    message = EmailMessage()
    message.set_content("Body")
    to = ", ".join(f"person{i}@example.com" for i in range(80))
    page = json.loads(
        await read_message(
            gmail_service(message, [{"name": "To", "value": to}]),
            "message-1",
            "reader@example.com",
            page_offset=0,
        )
    )
    assert page["headers"]["To"] == to


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
    helpers._PAGE_CACHE.clear()
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


@pytest.mark.asyncio
async def test_cache_expiration_refetches_and_checks_digest(monkeypatch):
    now = [0]
    monkeypatch.setattr(helpers, "monotonic", lambda: now[0])
    message = EmailMessage()
    message.set_content("Original")
    original = json.loads(
        await read_message(
            gmail_service(message), "id", "user@example.com", page_offset=0
        )
    )
    now[0] = helpers._PAGE_CACHE_TTL + 1
    message.set_content("Changed")
    service = gmail_service(message)
    with pytest.raises(UserInputError, match="representation changed"):
        await read_message(
            service,
            "id",
            "user@example.com",
            page_offset=0,
            expected_sha256=original["body_sha256"],
        )
    assert service.users.return_value.messages.return_value.get.call_count == 2


@pytest.mark.asyncio
async def test_cache_separates_rendering_options():
    message = EmailMessage()
    message.set_content("Body")
    message.add_alternative(
        '<p>Body <a href="https://example.com">link</a></p>', subtype="html"
    )
    service = gmail_service(message)
    plain = json.loads(
        await read_message(service, "id", "user@example.com", page_offset=0)
    )
    linked = json.loads(
        await read_message(
            service, "id", "user@example.com", page_offset=0, include_links=True
        )
    )
    html = json.loads(
        await read_message(
            service, "id", "user@example.com", page_offset=0, body_format="html"
        )
    )
    again = json.loads(
        await read_message(service, "id", "user@example.com", page_offset=0)
    )
    assert "https://example.com" not in plain["body"]
    assert "https://example.com" in linked["body"]
    assert "<p>" in html["body"]
    assert plain == again
    formats = [
        call.kwargs["format"]
        for call in service.users.return_value.messages.return_value.get.call_args_list
    ]
    assert formats.count("raw") == 3
    assert formats.count("metadata") == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", ["bytes", "entries"])
async def test_cache_evicts_oldest_within_both_bounds(monkeypatch, bound):
    message = EmailMessage()
    message.set_content("x" * 100)
    service = gmail_service(message)
    monkeypatch.setattr(
        helpers, "_PAGE_CACHE_BYTES", 1000 if bound == "bytes" else 10000
    )
    monkeypatch.setattr(helpers, "_PAGE_CACHE_ENTRIES", 128 if bound == "bytes" else 2)
    for message_id in ["first", "second", "third", "first"]:
        await read_message(service, message_id, "user@example.com", page_offset=0)
        assert len(helpers._PAGE_CACHE) <= 2
        assert (
            sum(
                sys.getsizeof(entry[1][0]) + 256
                for entry in helpers._PAGE_CACHE.values()
            )
            <= helpers._PAGE_CACHE_BYTES
        )
    assert service.users.return_value.messages.return_value.get.call_count == 8


@pytest.mark.asyncio
async def test_smaller_file_limit_applies_to_cached_body(monkeypatch):
    message = EmailMessage()
    message.set_content("x" * 2000)
    await read_message(gmail_service(message), "id", "user@example.com", page_offset=0)
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "1000")
    service = Mock()
    service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "sizeEstimate": 1
    }
    with pytest.raises(FileTooLargeError):
        await read_message(service, "id", "user@example.com", page_offset=100)
    assert service.users.return_value.messages.return_value.get.call_count == 1


@pytest.mark.asyncio
async def test_body_larger_than_cache_budget_is_not_retained(monkeypatch):
    monkeypatch.setattr(helpers, "_PAGE_CACHE_BYTES", 300)
    message = EmailMessage()
    message.set_content("x" * 100)
    service = gmail_service(message)
    for offset in [0, 50]:
        result = json.loads(
            await read_message(service, "id", "user@example.com", page_offset=offset)
        )
        assert result["body"].startswith("x")
        assert not helpers._PAGE_CACHE
    assert service.users.return_value.messages.return_value.get.call_count == 4
