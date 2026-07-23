"""
Tests for the ``save_body_to_file`` option on the Gmail content tools:
``get_gmail_message_content``, ``get_gmail_messages_content_batch``,
``get_gmail_thread_content``, and ``get_gmail_threads_content_batch``.
"""

import base64
from unittest.mock import Mock

import pytest

import gmail.gmail_tools as gmail_tools
from core.server import server
from core.tool_registry import get_tool_components
from core.utils import UserInputError
from gmail.gmail_tools import (
    get_gmail_message_content,
    get_gmail_messages_content_batch,
    get_gmail_thread_content,
    get_gmail_threads_content_batch,
)


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _headers(**overrides):
    header_map = {
        "Subject": "Example subject",
        "From": "sender@example.com",
        "To": "recipient@example.com",
        "Cc": "cc@example.com",
        "Message-ID": "<message@example.com>",
        "Date": "Fri, 28 Mar 2026 10:00:00 -0400",
    }
    header_map.update(overrides)
    return [{"name": name, "value": value} for name, value in header_map.items()]


def _payload(headers=None, text=None, html=None):
    payload = {"headers": headers or _headers()}
    parts = []
    if text is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": _encode(text)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": _encode(html)}})
    if parts:
        payload["mimeType"] = "multipart/alternative"
        payload["parts"] = parts
    return payload


def _message_response(message_id: str, text="", html="", headers=None):
    return {
        "id": message_id,
        "payload": _payload(headers=headers, text=text, html=html),
    }


def _metadata_response(message_id: str, headers=None):
    return {
        "id": message_id,
        "payload": {"headers": headers or _headers()},
    }


def _thread_response(thread_id: str, messages):
    return {"id": thread_id, "messages": messages}


class _FakeBatch:
    def __init__(self, callback):
        self._callback = callback
        self._requests = []

    def add(self, request, request_id):
        self._requests.append((request_id, request))

    def execute(self):
        for request_id, request in self._requests:
            try:
                response = request.execute()
                self._callback(request_id, response, None)
            except Exception as exc:
                self._callback(request_id, None, exc)


def _build_service(*, message_responses=None, thread_responses=None):
    message_responses = message_responses or {}
    thread_responses = thread_responses or {}

    service = Mock()

    def message_get(**kwargs):
        request = Mock()
        response = message_responses[(kwargs["id"], kwargs["format"])]
        if isinstance(response, Exception):
            request.execute.side_effect = response
        else:
            request.execute.return_value = response
        return request

    def thread_get(**kwargs):
        request = Mock()
        response = thread_responses[(kwargs["id"], kwargs["format"])]
        if isinstance(response, Exception):
            request.execute.side_effect = response
        else:
            request.execute.return_value = response
        return request

    service.users().messages().get.side_effect = message_get
    service.users().threads().get.side_effect = thread_get
    service.new_batch_http_request.side_effect = lambda callback: _FakeBatch(callback)
    return service


@pytest.fixture
def isolated_storage_env(tmp_path, monkeypatch):
    """Route attachment storage to a temp dir and force HTTP (not stateless) mode."""
    import core.attachment_storage as storage_module
    import auth.oauth_config as oauth_config_module
    import core.config as core_config_module

    monkeypatch.setattr(storage_module, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(oauth_config_module, "is_stateless_mode", lambda: False)
    monkeypatch.setattr(core_config_module, "get_transport_mode", lambda: "http")

    # Reset the cached module-level storage singleton so our patched
    # STORAGE_DIR actually takes effect.
    monkeypatch.setattr(storage_module, "_attachment_storage", None, raising=False)

    return tmp_path


def _saved_files(storage_dir):
    return sorted(p for p in storage_dir.iterdir() if p.is_file())


def test_schema_includes_save_body_to_file():
    """Published MCP schemas of all four content tools expose save_body_to_file."""
    components = get_tool_components(server)
    for tool in (
        get_gmail_message_content,
        get_gmail_messages_content_batch,
        get_gmail_thread_content,
        get_gmail_threads_content_batch,
    ):
        schema = components[tool.__name__].parameters["properties"]
        assert "save_body_to_file" in schema, tool.__name__
        assert schema["save_body_to_file"]["type"] == "boolean"
        assert schema["save_body_to_file"]["default"] is False


class TestSingleMessage:
    @pytest.mark.asyncio
    async def test_text_body_saved_to_txt_file(self, isolated_storage_env):
        service = _build_service(
            message_responses={
                ("msg-1", "metadata"): _metadata_response("msg-1"),
                ("msg-1", "full"): _message_response(
                    "msg-1", text="Plain body", html="<p>Plain body</p>"
                ),
            }
        )

        result = await _unwrap(get_gmail_message_content)(
            service=service,
            message_id="msg-1",
            user_google_email="user@example.com",
            save_body_to_file=True,
        )

        assert "--- BODY SAVED TO FILE ---" in result
        assert "Download URL" in result
        assert "Plain body" not in result

        files = _saved_files(isolated_storage_env)
        assert len(files) == 1
        assert files[0].suffix == ".txt"
        assert "msg-1" in files[0].name
        assert files[0].read_text() == "Plain body"

    @pytest.mark.asyncio
    async def test_html_body_saved_untruncated(self, isolated_storage_env):
        long_html = "<div>" + "x" * 25000 + "</div>"
        service = _build_service(
            message_responses={
                ("msg-1", "metadata"): _metadata_response("msg-1"),
                ("msg-1", "full"): _message_response(
                    "msg-1", text="fallback", html=long_html
                ),
            }
        )

        result = await _unwrap(get_gmail_message_content)(
            service=service,
            message_id="msg-1",
            user_google_email="user@example.com",
            body_format="html",
            save_body_to_file=True,
        )

        assert "--- BODY SAVED TO FILE ---" in result
        assert "[Content truncated...]" not in result

        files = _saved_files(isolated_storage_env)
        assert len(files) == 1
        assert files[0].suffix == ".html"
        # Full untruncated body (inline display would cap at 20000 chars).
        assert files[0].read_text() == long_html

    @pytest.mark.asyncio
    async def test_raw_body_saved_as_original_mime_eml(self, isolated_storage_env):
        mime_bytes = (
            b"From: sender@example.com\r\nSubject: Example subject\r\n\r\nRaw body\r\n"
        )
        raw_b64 = base64.urlsafe_b64encode(mime_bytes).decode()
        service = _build_service(
            message_responses={
                ("msg-1", "metadata"): _metadata_response("msg-1"),
                ("msg-1", "raw"): {"id": "msg-1", "raw": raw_b64},
            }
        )

        result = await _unwrap(get_gmail_message_content)(
            service=service,
            message_id="msg-1",
            user_google_email="user@example.com",
            body_format="raw",
            save_body_to_file=True,
        )

        assert "--- BODY SAVED TO FILE ---" in result
        assert "--- RAW MIME ---" not in result

        files = _saved_files(isolated_storage_env)
        assert len(files) == 1
        assert files[0].suffix == ".eml"
        assert files[0].read_bytes() == mime_bytes

    @pytest.mark.asyncio
    async def test_raw_body_with_unpadded_base64url_saved(self, isolated_storage_env):
        """Gmail's raw field is base64url without '=' padding; save must not fail."""
        # Length not a multiple of 3 so the encoded form requires padding.
        mime_bytes = b"Subject: Example subject\r\n\r\nUnpadded raw body\r\n.."
        raw_b64_unpadded = base64.urlsafe_b64encode(mime_bytes).decode().rstrip("=")
        assert len(raw_b64_unpadded) % 4 != 0, "fixture must actually be unpadded"

        service = _build_service(
            message_responses={
                ("msg-1", "metadata"): _metadata_response("msg-1"),
                ("msg-1", "raw"): {"id": "msg-1", "raw": raw_b64_unpadded},
            }
        )

        result = await _unwrap(get_gmail_message_content)(
            service=service,
            message_id="msg-1",
            user_google_email="user@example.com",
            body_format="raw",
            save_body_to_file=True,
        )

        assert "--- BODY SAVED TO FILE ---" in result
        assert "Failed to save body to file" not in result

        files = _saved_files(isolated_storage_env)
        assert len(files) == 1
        assert files[0].suffix == ".eml"
        assert files[0].read_bytes() == mime_bytes

    @pytest.mark.asyncio
    async def test_filename_derived_from_subject(self, isolated_storage_env):
        service = _build_service(
            message_responses={
                ("msg-1", "metadata"): _metadata_response(
                    "msg-1", headers=_headers(Subject="RE: Q3 / Report?")
                ),
                ("msg-1", "full"): _message_response(
                    "msg-1",
                    text="Body",
                    headers=_headers(Subject="RE: Q3 / Report?"),
                ),
            }
        )

        await _unwrap(get_gmail_message_content)(
            service=service,
            message_id="msg-1",
            user_google_email="user@example.com",
            save_body_to_file=True,
        )

        files = _saved_files(isolated_storage_env)
        assert len(files) == 1
        # Windows-reserved characters sanitized, message ID included.
        assert "/" not in files[0].name
        assert "?" not in files[0].name
        assert files[0].name.startswith("RE_ Q3 _ Report")
        assert "msg-1" in files[0].name

    @pytest.mark.asyncio
    async def test_stdio_mode_returns_file_path(
        self, isolated_storage_env, monkeypatch
    ):
        import core.config as core_config_module

        monkeypatch.setattr(core_config_module, "get_transport_mode", lambda: "stdio")

        service = _build_service(
            message_responses={
                ("msg-1", "metadata"): _metadata_response("msg-1"),
                ("msg-1", "full"): _message_response("msg-1", text="Body"),
            }
        )

        result = await _unwrap(get_gmail_message_content)(
            service=service,
            message_id="msg-1",
            user_google_email="user@example.com",
            save_body_to_file=True,
        )

        assert "Saved to:" in result
        assert str(isolated_storage_env) in result
        assert "Download URL" not in result

    @pytest.mark.asyncio
    async def test_default_keeps_body_inline(self, isolated_storage_env):
        service = _build_service(
            message_responses={
                ("msg-1", "metadata"): _metadata_response("msg-1"),
                ("msg-1", "full"): _message_response("msg-1", text="Inline body"),
            }
        )

        result = await _unwrap(get_gmail_message_content)(
            service=service,
            message_id="msg-1",
            user_google_email="user@example.com",
        )

        assert "--- BODY ---" in result
        assert "Inline body" in result
        assert _saved_files(isolated_storage_env) == []

    @pytest.mark.asyncio
    async def test_stateless_mode_falls_back_to_inline(
        self, isolated_storage_env, monkeypatch
    ):
        import auth.oauth_config as oauth_config_module

        monkeypatch.setattr(oauth_config_module, "is_stateless_mode", lambda: True)

        service = _build_service(
            message_responses={
                ("msg-1", "metadata"): _metadata_response("msg-1"),
                ("msg-1", "full"): _message_response("msg-1", text="Inline body"),
            }
        )

        result = await _unwrap(get_gmail_message_content)(
            service=service,
            message_id="msg-1",
            user_google_email="user@example.com",
            save_body_to_file=True,
        )

        assert "Stateless mode" in result
        assert "Inline body" in result
        assert _saved_files(isolated_storage_env) == []

    @pytest.mark.asyncio
    async def test_save_failure_falls_back_to_inline(
        self, isolated_storage_env, monkeypatch
    ):
        from core.attachment_storage import AttachmentStorage

        def _boom(self, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(AttachmentStorage, "save_attachment", _boom)

        service = _build_service(
            message_responses={
                ("msg-1", "metadata"): _metadata_response("msg-1"),
                ("msg-1", "full"): _message_response("msg-1", text="Inline body"),
            }
        )

        result = await _unwrap(get_gmail_message_content)(
            service=service,
            message_id="msg-1",
            user_google_email="user@example.com",
            save_body_to_file=True,
        )

        assert "Failed to save body to file" in result
        assert "Inline body" in result
        assert _saved_files(isolated_storage_env) == []


class TestBatchMessages:
    @pytest.mark.asyncio
    async def test_one_file_per_message(self, isolated_storage_env):
        service = _build_service(
            message_responses={
                ("msg-1", "full"): _message_response("msg-1", text="First body"),
                ("msg-2", "full"): _message_response("msg-2", text="Second body"),
            }
        )

        result = await _unwrap(get_gmail_messages_content_batch)(
            service=service,
            message_ids=["msg-1", "msg-2"],
            user_google_email="user@example.com",
            save_body_to_file=True,
        )

        assert result.count("--- BODY SAVED TO FILE ---") == 2
        assert "First body" not in result
        assert "Second body" not in result

        files = _saved_files(isolated_storage_env)
        assert len(files) == 2
        contents = {f.read_text() for f in files}
        assert contents == {"First body", "Second body"}

    @pytest.mark.asyncio
    async def test_raw_batch_saves_untruncated_eml(self, isolated_storage_env):
        mime_bytes = b"Subject: Example subject\r\n\r\n" + b"y" * 30000
        raw_b64 = base64.urlsafe_b64encode(mime_bytes).decode()
        service = _build_service(
            message_responses={
                ("msg-1", "metadata"): _metadata_response("msg-1"),
                ("msg-1", "raw"): {"id": "msg-1", "raw": raw_b64},
            }
        )

        result = await _unwrap(get_gmail_messages_content_batch)(
            service=service,
            message_ids=["msg-1"],
            user_google_email="user@example.com",
            body_format="raw",
            save_body_to_file=True,
        )

        assert "--- BODY SAVED TO FILE ---" in result

        files = _saved_files(isolated_storage_env)
        assert len(files) == 1
        assert files[0].suffix == ".eml"
        # Full MIME, beyond the inline raw truncation limit.
        assert files[0].read_bytes() == mime_bytes

    @pytest.mark.asyncio
    async def test_metadata_format_with_save_rejected(self, isolated_storage_env):
        service = _build_service()

        with pytest.raises(UserInputError, match="save_body_to_file"):
            await _unwrap(get_gmail_messages_content_batch)(
                service=service,
                message_ids=["msg-1"],
                user_google_email="user@example.com",
                format="metadata",
                save_body_to_file=True,
            )


class TestThreads:
    def _thread_service(self):
        return _build_service(
            thread_responses={
                ("thread-1", "full"): _thread_response(
                    "thread-1",
                    [
                        _message_response("msg-1", text="First message body"),
                        _message_response(
                            "msg-2",
                            text="Second message body",
                            headers=_headers(From="other@example.com"),
                        ),
                    ],
                )
            }
        )

    @pytest.mark.asyncio
    async def test_thread_saves_one_file_per_message(self, isolated_storage_env):
        result = await _unwrap(get_gmail_thread_content)(
            service=self._thread_service(),
            thread_id="thread-1",
            user_google_email="user@example.com",
            save_body_to_file=True,
        )

        assert "Thread ID: thread-1" in result
        assert result.count("--- BODY SAVED TO FILE ---") == 2
        assert "First message body" not in result
        assert "Second message body" not in result

        files = _saved_files(isolated_storage_env)
        assert len(files) == 2
        contents = {f.read_text() for f in files}
        assert contents == {"First message body", "Second message body"}
        names = {f.name for f in files}
        assert any("msg-1" in name for name in names)
        assert any("msg-2" in name for name in names)

    @pytest.mark.asyncio
    async def test_thread_default_keeps_bodies_inline(self, isolated_storage_env):
        result = await _unwrap(get_gmail_thread_content)(
            service=self._thread_service(),
            thread_id="thread-1",
            user_google_email="user@example.com",
        )

        assert "First message body" in result
        assert "Second message body" in result
        assert _saved_files(isolated_storage_env) == []

    @pytest.mark.asyncio
    async def test_thread_include_analysis_still_returns_dict(
        self, isolated_storage_env
    ):
        result = await _unwrap(get_gmail_thread_content)(
            service=self._thread_service(),
            thread_id="thread-1",
            user_google_email="user@example.com",
            save_body_to_file=True,
            include_analysis=True,
        )

        assert isinstance(result, dict)
        assert "analysis" in result
        assert "--- BODY SAVED TO FILE ---" in result["content"]
        assert len(_saved_files(isolated_storage_env)) == 2

    @pytest.mark.asyncio
    async def test_threads_batch_saves_files(self, isolated_storage_env):
        service = _build_service(
            thread_responses={
                ("thread-1", "full"): _thread_response(
                    "thread-1", [_message_response("msg-1", text="Body one")]
                ),
                ("thread-2", "full"): _thread_response(
                    "thread-2", [_message_response("msg-2", text="Body two")]
                ),
            }
        )

        result = await _unwrap(get_gmail_threads_content_batch)(
            service=service,
            thread_ids=["thread-1", "thread-2"],
            user_google_email="user@example.com",
            save_body_to_file=True,
        )

        assert result.count("--- BODY SAVED TO FILE ---") == 2

        files = _saved_files(isolated_storage_env)
        assert len(files) == 2
        contents = {f.read_text() for f in files}
        assert contents == {"Body one", "Body two"}

    @pytest.mark.asyncio
    async def test_thread_raw_saves_eml_per_message(self, isolated_storage_env):
        mime_bytes = b"Subject: Example subject\r\n\r\nThread raw body\r\n"
        raw_b64 = base64.urlsafe_b64encode(mime_bytes).decode()
        service = _build_service(
            message_responses={
                ("msg-1", "raw"): {"id": "msg-1", "raw": raw_b64},
            },
            thread_responses={
                ("thread-1", "full"): _thread_response(
                    "thread-1", [_message_response("msg-1", text="ignored")]
                )
            },
        )

        result = await _unwrap(get_gmail_thread_content)(
            service=service,
            thread_id="thread-1",
            user_google_email="user@example.com",
            body_format="raw",
            save_body_to_file=True,
        )

        assert "--- BODY SAVED TO FILE ---" in result

        files = _saved_files(isolated_storage_env)
        assert len(files) == 1
        assert files[0].suffix == ".eml"
        assert files[0].read_bytes() == mime_bytes


class TestSaveBodyFileHelper:
    def test_unknown_empty_content_returns_warning(self, isolated_storage_env):
        lines, saved = gmail_tools._save_body_file("", "msg-1", "Subject", "raw")
        assert saved is False
        assert any("No body content" in line for line in lines)
