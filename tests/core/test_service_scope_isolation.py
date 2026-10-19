from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import nullcontext

import pytest

import auth.service_decorator as decorators
from auth.scopes import GMAIL_READONLY_SCOPE, PROTOCOL_AUTH_SCOPES


@pytest.mark.asyncio
@pytest.mark.parametrize("multiple", [False, True])
async def test_missing_chat_permissions_do_not_prevent_gmail(monkeypatch, multiple):
    email = "user@example.com"
    monkeypatch.setattr(decorators, "is_oauth21_enabled", lambda: True)
    monkeypatch.setattr(decorators, "is_service_account_enabled", lambda: False)
    monkeypatch.setattr(decorators, "is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(decorators, "get_auth_provider", lambda: object())
    monkeypatch.setattr(
        decorators, "get_access_token", lambda: SimpleNamespace(claims={"email": email})
    )
    monkeypatch.setattr(
        decorators,
        "_get_auth_context",
        AsyncMock(return_value=(email, "fastmcp_oauth", None)),
    )
    monkeypatch.setattr(
        decorators,
        "ensure_session_from_access_token",
        AsyncMock(
            return_value=SimpleNamespace(
                scopes=[*PROTOCOL_AUTH_SCOPES, GMAIL_READONLY_SCOPE]
            )
        ),
    )
    built = []

    def build(service_name, version, credentials):
        built.append(service_name)
        return SimpleNamespace()

    monkeypatch.setattr(decorators, "build_google_service", build)
    monkeypatch.setattr(decorators, "recycling", nullcontext)

    if multiple:

        @decorators.require_multiple_services(
            [
                {
                    "service_type": "chat",
                    "scopes": "chat_spaces_readonly",
                    "param_name": "service",
                },
                {
                    "service_type": "people",
                    "scopes": "contacts_read",
                    "param_name": "people_service",
                },
            ]
        )
        async def chat_tool(service, people_service, user_google_email):
            pytest.fail("Chat must not execute without permission")
    else:

        @decorators.require_google_service("chat", "chat_spaces_readonly")
        async def chat_tool(service, user_google_email):
            pytest.fail("Chat must not execute without permission")

    @decorators.require_google_service("gmail", "gmail_read")
    async def gmail_tool(service, user_google_email):
        return "email works"

    result = await chat_tool()
    assert "Permission required for chat" in result
    assert await gmail_tool() == "email works"
    assert built == ["gmail"]


@pytest.mark.asyncio
async def test_identity_errors_still_fail(monkeypatch):
    monkeypatch.setattr(decorators, "is_oauth21_enabled", lambda: True)
    monkeypatch.setattr(decorators, "is_service_account_enabled", lambda: False)
    monkeypatch.setattr(decorators, "is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(
        decorators,
        "_get_auth_context",
        AsyncMock(return_value=("user@example.com", "fastmcp_oauth", None)),
    )
    monkeypatch.setattr(
        decorators,
        "_authenticate_service",
        AsyncMock(side_effect=decorators.GoogleAuthenticationError("Account mismatch")),
    )

    @decorators.require_google_service("gmail", "gmail_read")
    async def gmail_tool(service, user_google_email):
        pytest.fail("A failed identity check must not execute the tool")

    with pytest.raises(decorators.GoogleAuthenticationError, match="Account mismatch"):
        await gmail_tool()
