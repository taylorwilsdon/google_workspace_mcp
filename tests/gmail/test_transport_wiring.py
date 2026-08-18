"""Tests confirming send/forward are routed through dispatch_transmit.

Verifies:
- env=smtp + scope  → send_via_smtp called, messages().send NOT called
- Bcc header absent from SMTP raw bytes; bcc address present in SMTP envelope
- env=smtp + missing scope → API path, result has fallback note, Bcc header kept
- env=api (default) → API path, result byte-identical to pre-change
- ISOLATION: draft_gmail_message and read tools never touch the transport

Synthetic data only (example.com).
"""

import base64
from email import message_from_bytes
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

import gmail.gmail_send_transport as transport_mod
import gmail.gmail_tools as tools_mod
from auth.scopes import MAIL_GOOGLE_COM_SCOPE
from gmail.gmail_tools import _forward_gmail_message_impl, send_gmail_message


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_USER = "alice@example.com"


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _gmail_service(sent_id: str = "msg-api-001"):
    svc = Mock()
    svc.users.return_value.messages.return_value.send.return_value.execute.return_value = {
        "id": sent_id
    }
    # messages().list for best-effort SMTP lookup
    svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "smtp-best-001"}]
    }
    # No signature
    svc.users.return_value.settings.return_value.sendAs.return_value.list.return_value.execute.return_value = {
        "sendAs": []
    }
    return svc


def _people_service_empty():
    """People service that resolves no names, so no scope-fallback note is added."""
    svc = Mock()
    svc.people.return_value.searchContacts.return_value.execute.return_value = {
        "results": []
    }
    return svc


def _patch_transport(monkeypatch, effective, creds, note):
    """Patch resolve_effective_transport in both the source module and gmail_tools' namespace."""
    fn = lambda user: (effective, creds, note)  # noqa: E731
    monkeypatch.setattr(transport_mod, "resolve_effective_transport", fn)
    monkeypatch.setattr(tools_mod, "resolve_effective_transport", fn)


def _make_creds(scopes=()):
    creds = MagicMock()
    creds.scopes = set(scopes)
    creds.token = "tok-abc"
    creds.expired = False
    return creds


def _smtp_creds():
    return _make_creds(scopes=[MAIL_GOOGLE_COM_SCOPE])


def _narrow_creds():
    """Credentials with send scope but NOT the mail.google.com scope."""
    return _make_creds(scopes=["https://www.googleapis.com/auth/gmail.send"])


def _decode_raw_sent(service) -> bytes:
    """Decode the raw MIME bytes passed to messages().send()."""
    call = service.users.return_value.messages.return_value.send.call_args
    raw_b64 = call.kwargs["body"]["raw"]
    return base64.urlsafe_b64decode(raw_b64)


def _forward_original_message():
    """Minimal Gmail API payload for a forwardable message."""
    return {
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Hello"},
                {"name": "From", "value": "bob@example.com"},
                {"name": "To", "value": "alice@example.com"},
                {"name": "Date", "value": "Mon, 01 Jan 2024 10:00:00 +0000"},
            ],
            "body": {"data": base64.urlsafe_b64encode(b"Body text").decode()},
        }
    }


def _forward_service(sent_id: str = "fwd-api-001"):
    svc = _gmail_service(sent_id=sent_id)
    svc.users.return_value.messages.return_value.get.return_value.execute.return_value = _forward_original_message()
    return svc


# ---------------------------------------------------------------------------
# 1. send_gmail_message — env=smtp + scoped creds → SMTP path
# ---------------------------------------------------------------------------


class TestSendSmtpPath:
    @pytest.mark.asyncio
    async def test_smtp_called_not_api_send(self, monkeypatch):
        """SMTP transport is used; messages().send() is NOT called."""
        smtp_calls = []

        async def fake_smtp(sender, recipients, raw_bytes, user_email, token):
            smtp_calls.append({"sender": sender, "recipients": recipients})
            return "OK queued"

        monkeypatch.setattr(transport_mod, "send_via_smtp", fake_smtp)
        _patch_transport(monkeypatch, "smtp", _smtp_creds(), "")

        svc = _gmail_service()

        result = await _unwrap(send_gmail_message)(
            service=svc,
            people_service=_people_service_empty(),
            user_google_email=_USER,
            to="carol@example.com",
            bcc="dave@example.com",
            subject="Test subject",
            body="Hello",
            include_signature=False,
        )

        assert smtp_calls, "send_via_smtp was not called"
        svc.users.return_value.messages.return_value.send.assert_not_called()
        assert "via SMTP!" in result

    @pytest.mark.asyncio
    async def test_smtp_raw_has_no_bcc_header(self, monkeypatch):
        """SMTP path: the raw MIME bytes must NOT contain a Bcc header."""
        captured = {}

        async def fake_smtp(sender, recipients, raw_bytes, user_email, token):
            captured["raw"] = raw_bytes
            captured["recipients"] = recipients
            return "OK"

        monkeypatch.setattr(transport_mod, "send_via_smtp", fake_smtp)
        _patch_transport(monkeypatch, "smtp", _smtp_creds(), "")

        svc = _gmail_service()
        await _unwrap(send_gmail_message)(
            service=svc,
            people_service=_people_service_empty(),
            user_google_email=_USER,
            to="carol@example.com",
            bcc="dave@example.com",
            subject="Bcc test",
            body="Check bcc",
            include_signature=False,
        )

        assert "raw" in captured
        msg = message_from_bytes(captured["raw"])
        assert msg["Bcc"] is None, (
            f"Bcc header must be absent in SMTP raw; got: {msg['Bcc']}"
        )

    @pytest.mark.asyncio
    async def test_smtp_envelope_includes_bcc(self, monkeypatch):
        """SMTP path: the bcc address must appear in the envelope recipients."""
        captured = {}

        async def fake_smtp(sender, recipients, raw_bytes, user_email, token):
            captured["recipients"] = recipients
            return "OK"

        monkeypatch.setattr(transport_mod, "send_via_smtp", fake_smtp)
        _patch_transport(monkeypatch, "smtp", _smtp_creds(), "")

        svc = _gmail_service()
        await _unwrap(send_gmail_message)(
            service=svc,
            people_service=_people_service_empty(),
            user_google_email=_USER,
            to="carol@example.com",
            bcc="dave@example.com",
            subject="Envelope test",
            body="Check envelope",
            include_signature=False,
        )

        assert "dave@example.com" in captured["recipients"]
        assert "carol@example.com" in captured["recipients"]


# ---------------------------------------------------------------------------
# 2. _forward_gmail_message_impl — env=smtp + scoped creds → SMTP path
# ---------------------------------------------------------------------------


class TestForwardSmtpPath:
    @pytest.mark.asyncio
    async def test_smtp_called_not_api_send(self, monkeypatch):
        """Forward: SMTP transport used; messages().send() NOT called."""
        smtp_calls = []

        async def fake_smtp(sender, recipients, raw_bytes, user_email, token):
            smtp_calls.append(True)
            return "OK queued"

        monkeypatch.setattr(transport_mod, "send_via_smtp", fake_smtp)
        _patch_transport(monkeypatch, "smtp", _smtp_creds(), "")

        svc = _forward_service()
        result = await _forward_gmail_message_impl(
            service=svc,
            message_id="orig-001",
            to="carol@example.com",
            bcc="dave@example.com",
            user_google_email=_USER,
        )

        assert smtp_calls, "send_via_smtp was not called"
        svc.users.return_value.messages.return_value.send.assert_not_called()
        assert "via SMTP!" in result

    @pytest.mark.asyncio
    async def test_smtp_raw_has_no_bcc_header(self, monkeypatch):
        """Forward SMTP path: raw MIME bytes must NOT contain a Bcc header."""
        captured = {}

        async def fake_smtp(sender, recipients, raw_bytes, user_email, token):
            captured["raw"] = raw_bytes
            captured["recipients"] = recipients
            return "OK"

        monkeypatch.setattr(transport_mod, "send_via_smtp", fake_smtp)
        _patch_transport(monkeypatch, "smtp", _smtp_creds(), "")

        svc = _forward_service()
        await _forward_gmail_message_impl(
            service=svc,
            message_id="orig-002",
            to="carol@example.com",
            bcc="dave@example.com",
            user_google_email=_USER,
        )

        msg = message_from_bytes(captured["raw"])
        assert msg["Bcc"] is None, (
            f"Bcc header must be absent in SMTP raw; got: {msg['Bcc']}"
        )

    @pytest.mark.asyncio
    async def test_smtp_envelope_includes_bcc(self, monkeypatch):
        """Forward SMTP path: bcc address present in SMTP envelope."""
        captured = {}

        async def fake_smtp(sender, recipients, raw_bytes, user_email, token):
            captured["recipients"] = recipients
            return "OK"

        monkeypatch.setattr(transport_mod, "send_via_smtp", fake_smtp)
        _patch_transport(monkeypatch, "smtp", _smtp_creds(), "")

        svc = _forward_service()
        await _forward_gmail_message_impl(
            service=svc,
            message_id="orig-003",
            to="carol@example.com",
            bcc="dave@example.com",
            user_google_email=_USER,
        )

        assert "dave@example.com" in captured["recipients"]
        assert "carol@example.com" in captured["recipients"]


# ---------------------------------------------------------------------------
# 3. env=smtp + missing mail.google.com scope → API fallback
# ---------------------------------------------------------------------------


class TestSmtpMissingScopeApiPath:
    @pytest.mark.asyncio
    async def test_send_falls_back_to_api_with_note(self, monkeypatch):
        """When SMTP is configured but scope is missing: use API, result has fallback note."""
        smtp_calls = []
        monkeypatch.setattr(
            transport_mod,
            "send_via_smtp",
            AsyncMock(side_effect=lambda *a, **kw: smtp_calls.append(True)),
        )
        _patch_transport(
            monkeypatch, "api", _narrow_creds(), " Note: SMTP scope missing"
        )

        svc = _gmail_service(sent_id="api-fallback-001")
        result = await _unwrap(send_gmail_message)(
            service=svc,
            people_service=_people_service_empty(),
            user_google_email=_USER,
            to="carol@example.com",
            bcc="dave@example.com",
            subject="Fallback test",
            body="Check fallback",
            include_signature=False,
        )

        assert not smtp_calls
        svc.users.return_value.messages.return_value.send.assert_called()
        assert "Message ID:" in result
        assert "SMTP scope missing" in result

    @pytest.mark.asyncio
    async def test_send_api_fallback_keeps_bcc_header(self, monkeypatch):
        """API fallback: Bcc header MUST be present in the raw MIME bytes."""
        monkeypatch.setattr(transport_mod, "send_via_smtp", AsyncMock())
        _patch_transport(
            monkeypatch, "api", _narrow_creds(), " Note: SMTP scope missing"
        )

        svc = _gmail_service()
        await _unwrap(send_gmail_message)(
            service=svc,
            people_service=_people_service_empty(),
            user_google_email=_USER,
            to="carol@example.com",
            bcc="dave@example.com",
            subject="Bcc header test",
            body="Should keep bcc",
            include_signature=False,
        )

        raw = _decode_raw_sent(svc)
        msg = message_from_bytes(raw)
        assert msg["Bcc"] is not None, "API path must keep Bcc header in raw MIME"
        assert "dave@example.com" in msg["Bcc"]


# ---------------------------------------------------------------------------
# 4. env=api (default) — byte-identical result string
# ---------------------------------------------------------------------------


class TestApiPathByteIdentical:
    @pytest.mark.asyncio
    async def test_send_result_byte_identical_no_attachments(self, monkeypatch):
        """env=api: result must be exactly 'Email sent! Message ID: <id>'."""
        _patch_transport(monkeypatch, "api", None, "")

        svc = _gmail_service(sent_id="exact-001")
        result = await _unwrap(send_gmail_message)(
            service=svc,
            people_service=_people_service_empty(),
            user_google_email=_USER,
            to="carol@example.com",
            subject="Byte identity",
            body="Check",
            include_signature=False,
        )

        assert result == "Email sent! Message ID: exact-001"

    @pytest.mark.asyncio
    async def test_forward_result_byte_identical(self, monkeypatch):
        """env=api: forward result must be exactly 'Email forwarded! Message ID: <id>'."""
        _patch_transport(monkeypatch, "api", None, "")

        svc = _forward_service(sent_id="fwd-exact-001")
        result = await _forward_gmail_message_impl(
            service=svc,
            message_id="orig-exact",
            to="carol@example.com",
            user_google_email=_USER,
        )

        assert result == "Email forwarded! Message ID: fwd-exact-001"

    @pytest.mark.asyncio
    async def test_api_path_bcc_header_present(self, monkeypatch):
        """env=api: Bcc header must be present in raw MIME bytes."""
        _patch_transport(monkeypatch, "api", None, "")

        svc = _gmail_service()
        await _unwrap(send_gmail_message)(
            service=svc,
            people_service=_people_service_empty(),
            user_google_email=_USER,
            to="carol@example.com",
            bcc="dave@example.com",
            subject="API bcc",
            body="Check",
            include_signature=False,
        )

        raw = _decode_raw_sent(svc)
        msg = message_from_bytes(raw)
        assert msg["Bcc"] is not None
        assert "dave@example.com" in msg["Bcc"]


# ---------------------------------------------------------------------------
# 5. ISOLATION: draft_gmail_message does NOT touch transport
# ---------------------------------------------------------------------------


class TestDraftIsolation:
    @pytest.mark.asyncio
    async def test_draft_does_not_call_resolve_effective_transport(self, monkeypatch):
        """draft_gmail_message must never call resolve_effective_transport."""
        from gmail.gmail_tools import draft_gmail_message

        transport_calls = []

        def tracking_resolve(user):
            transport_calls.append(user)
            return ("api", None, "")

        monkeypatch.setattr(
            transport_mod, "resolve_effective_transport", tracking_resolve
        )
        monkeypatch.setattr(tools_mod, "resolve_effective_transport", tracking_resolve)
        monkeypatch.setattr(
            transport_mod,
            "send_via_smtp",
            AsyncMock(side_effect=AssertionError("send_via_smtp must not be called")),
        )

        svc = Mock()
        svc.users.return_value.settings.return_value.sendAs.return_value.list.return_value.execute.return_value = {
            "sendAs": []
        }
        svc.users.return_value.drafts.return_value.create.return_value.execute.return_value = {
            "id": "draft-001"
        }

        result = await _unwrap(draft_gmail_message)(
            service=svc,
            people_service=None,
            user_google_email=_USER,
            to="carol@example.com",
            subject="Draft test",
            body="Draft body",
            include_signature=False,
        )

        assert not transport_calls, (
            f"resolve_effective_transport must not be called from draft; "
            f"called with: {transport_calls}"
        )
        assert "Draft" in result
        svc.users.return_value.drafts.return_value.create.assert_called()

    @pytest.mark.asyncio
    async def test_draft_uses_drafts_create_not_send(self, monkeypatch):
        """draft_gmail_message must call drafts().create, not messages().send."""
        from gmail.gmail_tools import draft_gmail_message

        _patch_transport(monkeypatch, "smtp", _smtp_creds(), "")
        monkeypatch.setattr(
            transport_mod,
            "send_via_smtp",
            AsyncMock(side_effect=AssertionError("send_via_smtp must not be called")),
        )

        svc = Mock()
        svc.users.return_value.settings.return_value.sendAs.return_value.list.return_value.execute.return_value = {
            "sendAs": []
        }
        svc.users.return_value.drafts.return_value.create.return_value.execute.return_value = {
            "id": "draft-002"
        }

        result = await _unwrap(draft_gmail_message)(
            service=svc,
            people_service=None,
            user_google_email=_USER,
            to="carol@example.com",
            subject="Draft isolation",
            body="Body",
            include_signature=False,
        )

        svc.users.return_value.drafts.return_value.create.assert_called()
        svc.users.return_value.messages.return_value.send.assert_not_called()
        assert "Draft" in result


# ---------------------------------------------------------------------------
# 6. API byte-identity WITH attachments
# ---------------------------------------------------------------------------


class TestApiPathWithAttachments:
    @pytest.mark.asyncio
    async def test_send_result_byte_identical_with_attachments(self, monkeypatch):
        """env=api with attachments: result must be exactly
        'Email sent with 1 attachment(s)! Message ID: <id>'."""
        import base64 as _base64

        _patch_transport(monkeypatch, "api", None, "")

        svc = _gmail_service(sent_id="attach-001")

        # A tiny valid PNG (1x1 pixel)
        png_b64 = _base64.b64encode(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
            b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        ).decode()

        result = await _unwrap(send_gmail_message)(
            service=svc,
            people_service=_people_service_empty(),
            user_google_email=_USER,
            to="carol@example.com",
            subject="Attachment test",
            body="See attachment",
            include_signature=False,
            attachments=[{"content": png_b64, "filename": "pixel.png"}],
        )

        assert result == "Email sent with 1 attachment(s)! Message ID: attach-001"


# ---------------------------------------------------------------------------
# 7. Forward SMTP-fallback keeps Bcc header (missing scope → API path)
# ---------------------------------------------------------------------------


class TestForwardSmtpFallbackKeepsBcc:
    @pytest.mark.asyncio
    async def test_forward_api_fallback_keeps_bcc_header(self, monkeypatch):
        """Forward: SMTP configured but scope missing → API path, Bcc header kept."""
        monkeypatch.setattr(transport_mod, "send_via_smtp", AsyncMock())
        _patch_transport(
            monkeypatch, "api", _narrow_creds(), " Note: SMTP scope missing"
        )

        svc = _forward_service(sent_id="fwd-fallback-001")
        result = await _forward_gmail_message_impl(
            service=svc,
            message_id="orig-fwd-fallback",
            to="carol@example.com",
            bcc="dave@example.com",
            user_google_email=_USER,
        )

        raw = _decode_raw_sent(svc)
        msg = message_from_bytes(raw)
        assert msg["Bcc"] is not None, (
            "Forward API fallback must keep Bcc header in raw MIME"
        )
        assert "dave@example.com" in msg["Bcc"]
        assert "Message ID:" in result
        assert "SMTP scope missing" in result


# ---------------------------------------------------------------------------
# 8. Non-web _prepare_gmail_message with include_bcc_header=False
# ---------------------------------------------------------------------------


class TestPrepareGmailMessageNonWebBccGate:
    def test_non_web_no_bcc_header_when_include_bcc_header_false(self):
        """web_compose=False + include_bcc_header=False must NOT add Bcc header."""
        from gmail.gmail_tools import _prepare_gmail_message

        raw_b64, _thread, _count, _errors = _prepare_gmail_message(
            subject="Bcc gate test",
            body="Body text",
            to="carol@example.com",
            bcc="dave@example.com",
            web_compose=False,
            include_bcc_header=False,
        )
        raw = base64.urlsafe_b64decode(raw_b64)
        msg = message_from_bytes(raw)
        assert msg["Bcc"] is None, (
            f"Bcc header must be absent when include_bcc_header=False; got: {msg['Bcc']}"
        )
