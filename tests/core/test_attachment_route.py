"""Findings 24, 30, 39: /attachments/{file_id} must authenticate and check ownership.

The route used to serve any stored attachment to anyone who presented the UUID, with
no authentication at all. A URL that leaked -- into logs, a chat transcript, a
forwarded message -- was therefore a durable read primitive over other users' Gmail
and Drive content.
"""

import pytest
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from core.server import serve_attachment

OWNER = "owner@example.com"


def _build_request(file_id: str, headers=None) -> Request:
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/attachments/{file_id}",
        "raw_path": f"/attachments/{file_id}".encode(),
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 8000),
        "path_params": {"file_id": file_id},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


class _OwnedStorage:
    """Storage holding one attachment owned by OWNER."""

    def __init__(self, file_path, owner=OWNER):
        self.file_path = file_path
        self.owner = owner
        self.metadata_calls = []

    def get_attachment_metadata(self, file_id, *, owner):
        self.metadata_calls.append((file_id, owner))
        if owner != self.owner:
            return None
        return {"filename": "sample.pdf", "mime_type": "application/pdf"}

    def get_attachment_path(self, _file_id, *, owner):
        return self.file_path if owner == self.owner else None


def _patch_principal(monkeypatch, principal):
    async def fake_resolve(_headers):
        return principal

    monkeypatch.setattr("auth.http_principal.resolve_http_principal", fake_resolve)


@pytest.mark.asyncio
async def test_owner_receives_the_attachment(monkeypatch, tmp_path):
    file_path = tmp_path / "sample.pdf"
    file_path.write_bytes(b"%PDF-1.3\n")
    storage = _OwnedStorage(file_path)

    monkeypatch.setattr(
        "core.attachment_storage.get_attachment_storage", lambda: storage
    )
    _patch_principal(monkeypatch, OWNER)

    response = await serve_attachment(_build_request("abc123"))

    assert storage.metadata_calls == [("abc123", OWNER)]
    assert isinstance(response, FileResponse)
    assert response.status_code == 200
    # Stored content is untrusted; never let a browser sniff it into script.
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(monkeypatch, tmp_path):
    file_path = tmp_path / "sample.pdf"
    file_path.write_bytes(b"%PDF-1.3\n")
    storage = _OwnedStorage(file_path)

    monkeypatch.setattr(
        "core.attachment_storage.get_attachment_storage", lambda: storage
    )
    _patch_principal(monkeypatch, None)

    response = await serve_attachment(_build_request("abc123"))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    # The store must not even be consulted without a principal.
    assert storage.metadata_calls == []


@pytest.mark.asyncio
async def test_other_users_attachment_is_indistinguishable_from_missing(
    monkeypatch, tmp_path
):
    """A denial and a miss must both be 404 so the route is not an existence oracle."""
    file_path = tmp_path / "sample.pdf"
    file_path.write_bytes(b"%PDF-1.3\n")
    storage = _OwnedStorage(file_path)

    monkeypatch.setattr(
        "core.attachment_storage.get_attachment_storage", lambda: storage
    )
    _patch_principal(monkeypatch, "attacker@example.com")

    denied = await serve_attachment(_build_request("abc123"))

    _patch_principal(monkeypatch, OWNER)
    missing = await serve_attachment(_build_request("does-not-exist"))
    storage.owner = "nobody"  # force a miss for the owner too
    missing = await serve_attachment(_build_request("does-not-exist"))

    assert denied.status_code == 404
    assert missing.status_code == 404
    assert denied.body == missing.body


@pytest.mark.asyncio
async def test_404_when_metadata_missing(monkeypatch):
    class EmptyStorage:
        def get_attachment_metadata(self, _file_id, *, owner):
            return None

    monkeypatch.setattr(
        "core.attachment_storage.get_attachment_storage", lambda: EmptyStorage()
    )
    _patch_principal(monkeypatch, OWNER)

    response = await serve_attachment(_build_request("missing"))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 404
    assert b"Attachment not found or expired" in response.body
