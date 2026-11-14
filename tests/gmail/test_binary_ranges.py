"""Synthetic-only byte range coverage for attachments and raw MIME exports."""

import base64
import hashlib
import inspect
import json
from unittest.mock import Mock

import pytest
from pydantic import TypeAdapter, ValidationError

import gmail.gmail_tools as tools
from core.utils import UserInputError
from gmail.gmail_helpers import DEFAULT_BINARY_CHUNK_BYTES, MAX_BINARY_CHUNK_BYTES


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    return inspect.unwrap(fn)


@pytest.fixture(autouse=True)
def _no_storage(monkeypatch):
    monkeypatch.delenv("WORKSPACE_MCP_MAX_FILE_BYTES", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_FILES_GCS_BUCKET", raising=False)
    monkeypatch.setattr(tools, "is_stateless_mode", lambda: False)
    storage = Mock(side_effect=AssertionError("Ranged reads must not write files"))
    monkeypatch.setattr("core.attachment_storage.get_attachment_storage", storage)


def _service(content):
    service = Mock()
    encoded = base64.urlsafe_b64encode(content).decode().rstrip("=")
    service.users().messages().attachments().get().execute.return_value = {
        "data": encoded,
        "size": len(content),
    }
    metadata = {
        "sizeEstimate": len(content),
        "payload": {
            "headers": [{"name": "Subject", "value": "synthetic"}],
            "parts": [{"body": {"attachmentId": "att-1", "size": len(content)}}],
        },
    }
    service.users().messages().get.side_effect = lambda **kwargs: Mock(
        execute=Mock(
            return_value={"raw": encoded} if kwargs["format"] == "raw" else metadata
        )
    )
    return service


async def _read(kind, service, **kwargs):
    options = dict(
        service=service, message_id="msg-1", user_google_email="user@example.com"
    )
    options.update(kwargs)
    if kind == "attachment":
        return await _unwrap(tools.get_gmail_attachment_content)(
            attachment_id="att-1", **options
        )
    return await _unwrap(tools.get_gmail_message_content)(
        full=True, body_format="raw", **options
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["attachment", "raw"])
async def test_binary_range_reassembly_and_boundaries(kind):
    payload = b"From: synthetic@example.com\r\n\r\n" + bytes(range(256)) * 3
    service = _service(payload)
    pieces = []
    offset = 0
    calls = 0
    while True:
        page = json.loads(
            await _read(kind, service, return_base64=True, offset=offset, length=97)
        )
        chunk = base64.b64decode(page["content_base64"], validate=True)
        assert page["encoding"] == "base64"
        assert chunk == payload[offset : offset + 97]
        assert page["offset"] == offset
        assert page["length"] == len(chunk)
        assert page["total_size"] == len(payload)
        assert page["sha256"] == hashlib.sha256(payload).hexdigest()
        pieces.append(chunk)
        calls += 1
        if page["complete"]:
            assert page["next_offset"] is None
            break
        assert page["next_offset"] == offset + len(chunk)
        offset = page["next_offset"]
    assert b"".join(pieces) == payload
    if kind == "attachment":
        assert (
            service.users().messages().attachments().get().execute.call_count == calls
        )
    else:
        assert (
            sum(
                c.kwargs["format"] == "raw"
                for c in service.users().messages().get.call_args_list
            )
            == calls
        )
    eof = json.loads(
        await _read(kind, service, return_base64=True, offset=len(payload))
    )
    assert eof["length"] == 0 and eof["complete"] and eof["content_base64"] == ""
    with pytest.raises(UserInputError, match="exceeds total_size"):
        await _read(kind, service, return_base64=True, offset=len(payload) + 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["attachment", "raw"])
async def test_default_and_maximum_chunk_sizes(kind):
    payload = b"x" * (MAX_BINARY_CHUNK_BYTES + 1)
    service = _service(payload)
    for options, expected in [
        ({"offset": 0}, DEFAULT_BINARY_CHUNK_BYTES),
        ({"length": MAX_BINARY_CHUNK_BYTES}, MAX_BINARY_CHUNK_BYTES),
    ]:
        page = json.loads(await _read(kind, service, return_base64=True, **options))
        assert page["offset"] == 0
        assert page["length"] == expected
        assert len(base64.b64decode(page["content_base64"])) == expected
        assert page["next_offset"] == expected
        assert not page["complete"]
    if kind == "raw":
        page = json.loads(await _read(kind, service, return_base64=True))
        assert page["length"] == DEFAULT_BINARY_CHUNK_BYTES


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["attachment", "raw"])
@pytest.mark.parametrize(
    "options",
    [
        {"offset": -1},
        {"offset": 1.5},
        {"offset": True},
        {"offset": "1"},
        {"length": 0},
        {"length": -1},
        {"length": 1.5},
        {"length": True},
        {"length": "1"},
        {"length": MAX_BINARY_CHUNK_BYTES + 1},
    ],
)
async def test_invalid_ranges_rejected_before_fetch(kind, options):
    service = Mock()
    with pytest.raises(UserInputError):
        await _read(kind, service, return_base64=True, **options)
    service.users.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["attachment", "raw"])
async def test_range_requires_base64(kind):
    service = Mock()
    for options in ({"offset": 0}, {"length": 10}):
        with pytest.raises(UserInputError, match="require return_base64"):
            await _read(kind, service, **options)
    service.users.assert_not_called()


@pytest.mark.asyncio
async def test_raw_inline_combinations_rejected_before_fetch():
    service = Mock()
    base = dict(
        service=service,
        message_id="msg-1",
        user_google_email="user@example.com",
        return_base64=True,
        full=True,
        body_format="raw",
    )
    for options in (
        {"full": False},
        {"body_format": "text"},
        {"body_format": "html"},
        {"format": "metadata"},
        {"include_headers": False},
        {"page_offset": 0},
    ):
        with pytest.raises(UserInputError):
            await _unwrap(tools.get_gmail_message_content)(**(base | options))
    service.users.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["attachment", "raw"])
async def test_request_uses_current_service_without_cross_account_cache(kind):
    for email, payload in [
        ("a@example.com", b"account-a"),
        ("b@example.com", b"account-b"),
    ]:
        service = _service(payload)
        page = json.loads(
            await _read(
                kind, service, return_base64=True, offset=0, user_google_email=email
            )
        )
        assert base64.b64decode(page["content_base64"]) == payload
        assert page["sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["attachment", "raw"])
async def test_whole_file_limit_still_applies(kind, monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "8")
    service = _service(b"x" * 9)
    result = await _read(kind, service, return_base64=True, offset=0, length=1)
    assert "WORKSPACE_MCP_MAX_FILE_BYTES" in result
    if kind == "attachment":
        service.users().messages().attachments().get().execute.assert_not_called()
    else:
        assert all(
            c.kwargs["format"] == "metadata"
            for c in service.users().messages().get.call_args_list
        )


@pytest.mark.asyncio
async def test_attachment_empty_invalid_and_inaccurate_size(monkeypatch):
    page = json.loads(
        await _read("attachment", _service(b""), return_base64=True, offset=0)
    )
    assert page["total_size"] == page["length"] == 0
    assert page["sha256"] == hashlib.sha256(b"").hexdigest()
    service = _service(b"x")
    service.users().messages().attachments().get().execute.return_value["data"] = "%%%"
    with pytest.raises(UserInputError, match="Invalid base64"):
        await _read("attachment", service, return_base64=True, offset=0)
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "8")
    service = _service(b"x")
    service.users().messages().attachments().get().execute.return_value["data"] = (
        base64.urlsafe_b64encode(b"x" * 9).decode()
    )
    assert "WORKSPACE_MCP_MAX_FILE_BYTES" in await _read(
        "attachment", service, return_base64=True, offset=0
    )


@pytest.mark.parametrize(
    "tool", [tools.get_gmail_attachment_content, tools.get_gmail_message_content]
)
def test_binary_range_schema_rejects_coerced_values(tool):
    annotations = inspect.get_annotations(_unwrap(tool))
    for name, invalids in [
        ("offset", [-1, True, 1.5, "1"]),
        ("length", [0, True, 1.5, "1", MAX_BINARY_CHUNK_BYTES + 1]),
    ]:
        adapter = TypeAdapter(annotations[name])
        for value in invalids:
            with pytest.raises(ValidationError):
                adapter.validate_python(value)
