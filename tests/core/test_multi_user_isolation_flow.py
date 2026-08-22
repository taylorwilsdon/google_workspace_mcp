"""End-to-end cross-user isolation for the HTTP multi-user configuration.

The individual fixes are unit-tested where they live. This exercises the seam they
have to hold *together*, because that is where the audit findings actually lived: an
attachment produced by one user's tool call must not be reachable by another user's
HTTP request, and the identity used for storage must be the same one the route
authorises against.

Google API calls are stubbed; nothing here needs real credentials. What is real is the
principal resolution (`auth.http_principal`), the owner-scoped store
(`core.attachment_storage`) and the route (`core.server.serve_attachment`).
"""

import base64
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

import auth.http_principal as http_principal
import core.attachment_storage as attachment_storage
from core.attachment_storage import AttachmentStorage
from core.server import serve_attachment

ALICE = "alice@example.com"
BOB = "bob@example.com"


class _VerifiedToken:
    """What an OAuth 2.1 provider returns for a valid bearer token."""

    def __init__(self, email):
        self.email = email
        self.claims = {"email": email}


class _Provider:
    """Auth provider that maps opaque tokens to accounts."""

    def __init__(self, tokens):
        self._tokens = dict(tokens)

    async def verify_token(self, token):
        email = self._tokens.get(token)
        return _VerifiedToken(email) if email else None


@pytest.fixture
def http_multi_user(monkeypatch, tmp_path):
    """OAuth 2.1 HTTP mode with two authenticated accounts and a clean store."""
    monkeypatch.setattr(http_principal, "is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(http_principal, "is_oauth21_enabled", lambda: True)

    provider = _Provider({"alice-token": ALICE, "bob-token": BOB})
    monkeypatch.setattr(
        "auth.oauth21_session_store.get_auth_provider", lambda: provider
    )

    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", tmp_path)
    store = AttachmentStorage()
    monkeypatch.setattr(attachment_storage, "_attachment_storage", store)
    monkeypatch.setattr("core.attachment_storage.get_attachment_storage", lambda: store)
    return store


def _request(file_id, token=None):
    headers = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": f"/attachments/{file_id}",
        "raw_path": f"/attachments/{file_id}".encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("203.0.113.7", 44444),
        "server": ("mcp.example.com", 443),
        "path_params": {"file_id": file_id},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def _store_for(store, owner, payload, filename):
    """Stand in for a tool call: the tool stores under its resolved principal."""
    return store.save_attachment(
        base64.urlsafe_b64encode(payload).decode(),
        filename=filename,
        mime_type="text/plain",
        owner=owner,
    )


@pytest.mark.asyncio
async def test_owner_can_fetch_their_own_attachment(http_multi_user):
    saved = _store_for(http_multi_user, ALICE, b"alice secret", "a.txt")

    response = await serve_attachment(_request(saved.file_id, "alice-token"))

    assert isinstance(response, FileResponse)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_another_authenticated_user_cannot_fetch_it(http_multi_user):
    """The core of findings 24/30/39: the id is not a capability."""
    saved = _store_for(http_multi_user, ALICE, b"alice secret", "a.txt")

    response = await serve_attachment(_request(saved.file_id, "bob-token"))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(http_multi_user):
    saved = _store_for(http_multi_user, ALICE, b"alice secret", "a.txt")

    response = await serve_attachment(_request(saved.file_id))

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token_is_rejected(http_multi_user):
    saved = _store_for(http_multi_user, ALICE, b"alice secret", "a.txt")

    response = await serve_attachment(_request(saved.file_id, "forged-token"))

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_denial_and_miss_are_indistinguishable(http_multi_user):
    """Otherwise the route is an oracle for which file ids exist."""
    saved = _store_for(http_multi_user, ALICE, b"alice secret", "a.txt")

    denied = await serve_attachment(_request(saved.file_id, "bob-token"))
    missing = await serve_attachment(
        _request("11111111-2222-3333-4444-555555555555", "bob-token")
    )

    assert denied.status_code == missing.status_code == 404
    assert denied.body == missing.body


@pytest.mark.asyncio
async def test_each_user_sees_only_their_own_of_two_attachments(http_multi_user):
    alice_file = _store_for(http_multi_user, ALICE, b"alice", "a.txt")
    bob_file = _store_for(http_multi_user, BOB, b"bob", "b.txt")

    results = {
        ("alice", "own"): await serve_attachment(
            _request(alice_file.file_id, "alice-token")
        ),
        ("alice", "other"): await serve_attachment(
            _request(bob_file.file_id, "alice-token")
        ),
        ("bob", "own"): await serve_attachment(_request(bob_file.file_id, "bob-token")),
        ("bob", "other"): await serve_attachment(
            _request(alice_file.file_id, "bob-token")
        ),
    }

    assert results[("alice", "own")].status_code == 200
    assert results[("bob", "own")].status_code == 200
    assert results[("alice", "other")].status_code == 404
    assert results[("bob", "other")].status_code == 404


@pytest.mark.asyncio
async def test_gateway_mode_uses_the_asserted_principal(monkeypatch, tmp_path):
    """The other supported multi-user mode must scope attachments the same way."""
    monkeypatch.setattr(http_principal, "is_trust_gateway_identity", lambda: True)
    monkeypatch.setattr(
        http_principal,
        "get_oauth_config",
        lambda: SimpleNamespace(gateway_identity_header="x-gateway-assertion"),
    )
    monkeypatch.setattr(
        http_principal,
        "extract_email_from_assertion",
        lambda assertion: {"alice.jwt": ALICE, "bob.jwt": BOB}.get(assertion),
    )

    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", tmp_path)
    store = AttachmentStorage()
    monkeypatch.setattr("core.attachment_storage.get_attachment_storage", lambda: store)
    saved = _store_for(store, ALICE, b"alice secret", "a.txt")

    def gateway_request(assertion):
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": f"/attachments/{saved.file_id}",
            "raw_path": f"/attachments/{saved.file_id}".encode(),
            "query_string": b"",
            "headers": [(b"x-gateway-assertion", assertion.encode())],
            "client": ("203.0.113.7", 44444),
            "server": ("mcp.example.com", 443),
            "path_params": {"file_id": saved.file_id},
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        return Request(scope, receive)

    assert (await serve_attachment(gateway_request("alice.jwt"))).status_code == 200
    assert (await serve_attachment(gateway_request("bob.jwt"))).status_code == 404
