"""Tests for reading every signed-in account at once (user_google_email="all")."""

from types import SimpleNamespace

import httplib2
import pytest
from googleapiclient.errors import HttpError

import auth.multi_account as multi_account
import auth.service_decorator as service_decorator
from auth.multi_account import AccountService
from core.utils import UserInputError


def _modes(monkeypatch, transport="stdio", **overrides):
    flags = {
        "is_oauth21_enabled": False,
        "is_trust_gateway_identity": False,
        "is_service_account_enabled": False,
        "is_stateless_mode": False,
    }
    flags.update(overrides)
    for name, value in flags.items():
        monkeypatch.setattr(multi_account, name, lambda value=value: value)
    monkeypatch.setattr(multi_account, "get_transport_mode", lambda: transport)
    monkeypatch.delenv("WORKSPACE_MCP_ALLOW_ALL_ACCOUNTS", raising=False)


def _store(monkeypatch, users):
    store = SimpleNamespace(list_users=lambda: list(users))
    monkeypatch.setattr(multi_account, "get_credential_store", lambda: store)


def _http_error(status=403):
    return HttpError(httplib2.Response({"status": status}), b"denied")


@pytest.mark.parametrize("value", ["all", "ALL", " All "])
def test_is_all_accounts(value):
    assert multi_account.is_all_accounts(value)


@pytest.mark.parametrize("value", [None, "", "user@example.com", "allison@example.com"])
def test_is_not_all_accounts(value):
    assert not multi_account.is_all_accounts(value)


@pytest.mark.parametrize(
    "mode",
    [
        "is_oauth21_enabled",
        "is_trust_gateway_identity",
        "is_service_account_enabled",
        "is_stateless_mode",
    ],
)
@pytest.mark.asyncio
async def test_refused_where_the_account_is_not_the_callers_choice(monkeypatch, mode):
    _modes(monkeypatch, **{mode: True})
    with pytest.raises(UserInputError, match="not available"):
        await multi_account.open_all_account_services("calendar", "v3", [], "t")


@pytest.mark.asyncio
async def test_http_needs_an_explicit_opt_in(monkeypatch):
    _modes(monkeypatch, transport="streamable-http")
    _store(monkeypatch, ["personal@example.com"])
    with pytest.raises(UserInputError, match="WORKSPACE_MCP_ALLOW_ALL_ACCOUNTS"):
        await multi_account.open_all_account_services("calendar", "v3", [], "t")

    monkeypatch.setenv("WORKSPACE_MCP_ALLOW_ALL_ACCOUNTS", "true")
    monkeypatch.setattr(multi_account, "get_credentials", lambda **kwargs: None)
    accounts = await multi_account.open_all_account_services("calendar", "v3", [], "t")
    assert [a.email for a in accounts] == ["personal@example.com"]


@pytest.mark.asyncio
async def test_open_all_reports_unusable_accounts_without_starting_auth(monkeypatch):
    _modes(monkeypatch)
    _store(
        monkeypatch,
        ["work@example.com", "personal@example.com", "broken@example.com"],
    )

    def fake_get_credentials(user_google_email, **kwargs):
        assert kwargs["session_id"] is None
        if user_google_email == "work@example.com":
            return None
        return SimpleNamespace(valid=True, email=user_google_email)

    def fake_build(name, version, creds):
        if creds.email == "broken@example.com":
            raise _http_error(500)
        return f"{name}-service"

    monkeypatch.setattr(multi_account, "get_credentials", fake_get_credentials)
    monkeypatch.setattr(multi_account, "build_google_service", fake_build)

    accounts = await multi_account.open_all_account_services("calendar", "v3", [], "t")

    assert [a.email for a in accounts] == [
        "broken@example.com",
        "personal@example.com",
        "work@example.com",
    ]
    assert accounts[0].service is None and accounts[0].error
    assert accounts[1].service == "calendar-service" and accounts[1].error is None
    assert "user_google_email='work@example.com'" in accounts[2].error


@pytest.mark.asyncio
async def test_open_all_with_no_accounts(monkeypatch):
    _modes(monkeypatch)
    _store(monkeypatch, [])
    with pytest.raises(UserInputError, match="No Google accounts"):
        await multi_account.open_all_account_services("gmail", "v1", [], "t")


@pytest.mark.asyncio
async def test_gather_per_account_isolates_api_failures():
    accounts = [
        AccountService(email="a@example.com", service="svc-a"),
        AccountService(email="b@example.com", error="signed out"),
        AccountService(email="c@example.com", service="svc-c"),
    ]

    async def fetch(account):
        if account.email == "c@example.com":
            raise _http_error()
        return account.email.upper()

    results = await multi_account.gather_per_account(accounts, fetch)

    assert results[0] == "A@EXAMPLE.COM"
    assert results[1].error == "signed out"
    assert results[2].email == "c@example.com" and results[2].error
    lines = multi_account.format_account_errors(results)
    assert "- b@example.com: signed out" in lines


@pytest.mark.asyncio
async def test_gather_per_account_does_not_hide_bugs():
    async def fetch(account):
        raise KeyError("bug")

    with pytest.raises(KeyError):
        await multi_account.gather_per_account(
            [AccountService(email="a@example.com", service="svc")], fetch
        )


class _FakeService:
    def __init__(self, name, events):
        self.name = name
        self._events = events
        self._http = SimpleNamespace(http=f"http:{name}")

    def close(self):
        self._events.append(f"close:{self.name}")


def _patch_decorator(monkeypatch, email):
    async def fake_get_auth_context(tool_name):
        return (None, None, None)

    monkeypatch.setattr(service_decorator, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(service_decorator, "_get_auth_context", fake_get_auth_context)
    monkeypatch.setattr(
        service_decorator,
        "_extract_oauth20_user_email",
        lambda args, kwargs, wrapper_sig: email,
    )


@pytest.mark.asyncio
async def test_decorator_passes_every_account_and_cleans_up(monkeypatch):
    _patch_decorator(monkeypatch, "all")
    events = []
    accounts = [
        AccountService(email="a@example.com", service=_FakeService("a", events)),
        AccountService(email="b@example.com", error="signed out"),
    ]
    opened = {}

    async def fake_open(service_name, version, scopes, tool_name):
        opened.update(service=service_name, version=version, tool=tool_name)
        return accounts

    monkeypatch.setattr(service_decorator, "open_all_account_services", fake_open)
    monkeypatch.setattr(
        service_decorator,
        "_release_google_service_cycles",
        lambda: events.append("collect"),
    )

    @service_decorator.require_google_service(
        "calendar", "calendar_read", allow_all_accounts=True
    )
    async def sample_tool(service, user_google_email: str):
        assert service is accounts
        assert user_google_email == "all"
        raise RuntimeError("tool failed")

    with pytest.raises(RuntimeError):
        await sample_tool(user_google_email="all")
    assert opened == {"service": "calendar", "version": "v3", "tool": "sample_tool"}
    assert events == ["close:a", "collect"]


@pytest.mark.asyncio
async def test_decorator_without_opt_in_treats_all_as_an_address(monkeypatch):
    _patch_decorator(monkeypatch, "all")

    async def fail_open(*args, **kwargs):
        raise AssertionError("must not open every account")

    async def fake_authenticate_service(*args, **kwargs):
        return _FakeService("one", []), "all"

    monkeypatch.setattr(service_decorator, "open_all_account_services", fail_open)
    monkeypatch.setattr(
        service_decorator, "_detect_oauth_version", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        service_decorator, "_authenticate_service", fake_authenticate_service
    )

    @service_decorator.require_google_service("gmail", "gmail_send")
    async def write_tool(service, user_google_email: str):
        assert isinstance(service, _FakeService)
        return "single"

    assert await write_tool(user_google_email="all") == "single"


@pytest.mark.asyncio
async def test_open_all_closes_what_it_opened_when_one_account_breaks(monkeypatch):
    _modes(monkeypatch)
    _store(monkeypatch, ["a@example.com", "b@example.com"])
    events = []

    def fake_get_credentials(user_google_email, **kwargs):
        return SimpleNamespace(valid=True, email=user_google_email)

    def fake_build(name, version, creds):
        if creds.email == "b@example.com":
            raise KeyError("bug")
        return _FakeService(creds.email, events)

    monkeypatch.setattr(multi_account, "get_credentials", fake_get_credentials)
    monkeypatch.setattr(multi_account, "build_google_service", fake_build)

    with pytest.raises(KeyError):
        await multi_account.open_all_account_services("calendar", "v3", [], "t")
    assert events == ["close:a@example.com"]
