import pytest
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from core.server import serve_attachment


def _build_request(file_id: str) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/attachments/{file_id}",
        "raw_path": f"/attachments/{file_id}".encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 8000),
        "path_params": {"file_id": file_id},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


@pytest.mark.asyncio
async def test_serve_attachment_uses_path_param_file_id(monkeypatch, tmp_path):
    file_path = tmp_path / "sample.pdf"
    file_path.write_bytes(b"%PDF-1.3\n")
    captured = {}

    class DummyStorage:
        def get_attachment_metadata(self, file_id):
            captured["file_id"] = file_id
            return {"filename": "sample.pdf", "mime_type": "application/pdf"}

        def get_attachment_path(self, _file_id):
            return file_path

    monkeypatch.setattr(
        "core.attachment_storage.get_attachment_storage", lambda: DummyStorage()
    )

    response = await serve_attachment(_build_request("abc123"))

    assert captured["file_id"] == "abc123"
    assert isinstance(response, FileResponse)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_serve_attachment_404_when_metadata_missing(monkeypatch):
    class DummyStorage:
        def get_attachment_metadata(self, _file_id):
            return None

    monkeypatch.setattr(
        "core.attachment_storage.get_attachment_storage", lambda: DummyStorage()
    )

    response = await serve_attachment(_build_request("missing"))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 404
    assert b"Attachment not found or expired" in response.body
