"""Tests for calendar sharing (ACL) management via manage_calendar_sharing."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError

import gcalendar.calendar_tools  # noqa: F401
from auth.permissions import set_permissions
from auth.scopes import CALENDAR_SCOPE
from core import tool_registry
from core.server import server
from core.utils import UserInputError, handle_http_errors
from gcalendar.calendar_tools import manage_calendar_sharing

RULE = {
    "id": "user:alice@example.com",
    "role": "reader",
    "scope": {"type": "user", "value": "alice@example.com"},
}


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _service():
    service = Mock()
    acl = service.acl.return_value
    acl.list.return_value.execute.return_value = {"items": [RULE]}
    acl.insert.return_value.execute.return_value = RULE
    acl.patch.return_value.execute.return_value = {**RULE, "role": "writer"}
    acl.delete.return_value.execute.return_value = ""
    return service, acl


async def _call(service, **kwargs):
    return await _unwrap(manage_calendar_sharing)(
        service=service, user_google_email="me@example.com", **kwargs
    )


@pytest.mark.asyncio
async def test_calendar_sharing_tool_is_registered():
    tool = await server.get_tool("manage_calendar_sharing")
    assert tool is not None
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is True


@pytest.mark.asyncio
async def test_calendar_sharing_tool_requires_full_calendar_scope():
    tool = await server.get_tool("manage_calendar_sharing")
    assert tool.fn._required_google_scopes == [CALENDAR_SCOPE]


@pytest.mark.asyncio
async def test_list_returns_rules_and_next_page_token():
    service, acl = _service()
    acl.list.return_value.execute.return_value = {
        "items": [RULE],
        "nextPageToken": "tok2",
    }

    result = await _call(service, action="list", page_token="tok1")

    acl.list.assert_called_once_with(calendarId="primary", pageToken="tok1")
    assert "- reader: user alice@example.com (Rule ID: user:alice@example.com)" in (
        result
    )
    assert "Next page token: tok2" in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope_type", "scope_value", "expected_scope"),
    [
        ("user", "alice@example.com", {"type": "user", "value": "alice@example.com"}),
        ("group", "team@example.com", {"type": "group", "value": "team@example.com"}),
        ("domain", "example.com", {"type": "domain", "value": "example.com"}),
        ("default", None, {"type": "default"}),
        ("USER", " josé@exämple.com ", {"type": "user", "value": "josé@exämple.com"}),
    ],
)
async def test_share_inserts_rule_for_each_scope_type(
    scope_type, scope_value, expected_scope
):
    service, acl = _service()

    result = await _call(
        service,
        action="share",
        calendar_id="cal@group.calendar.google.com",
        role="freebusyreader",
        scope_type=scope_type,
        scope_value=scope_value,
        send_notifications=False,
    )

    acl.insert.assert_called_once_with(
        calendarId="cal@group.calendar.google.com",
        body={"role": "freeBusyReader", "scope": expected_scope},
        sendNotifications=False,
    )
    assert result.startswith("Shared calendar 'cal@group.calendar.google.com'")


@pytest.mark.asyncio
async def test_update_patches_role():
    service, acl = _service()

    result = await _call(
        service, action="update", rule_id="user:alice@example.com", role="writer"
    )

    acl.patch.assert_called_once_with(
        calendarId="primary",
        ruleId="user:alice@example.com",
        body={"role": "writer"},
        sendNotifications=True,
    )
    assert "- writer: user alice@example.com" in result


@pytest.mark.asyncio
async def test_remove_deletes_rule():
    service, acl = _service()

    result = await _call(service, action="Remove", rule_id="domain:example.com")

    acl.delete.assert_called_once_with(
        calendarId="primary", ruleId="domain:example.com"
    )
    assert (
        result == "Removed sharing rule 'domain:example.com' from calendar 'primary'."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"action": "share", "scope_value": "a@example.com"}, "role is required"),
        (
            {"action": "share", "role": "admin", "scope_value": "a@example.com"},
            "Invalid role 'admin'",
        ),
        ({"action": "share", "role": "reader"}, "scope_value is required"),
        (
            {"action": "share", "role": "reader", "scope_value": "   "},
            "scope_value is required",
        ),
        (
            {"action": "share", "role": "reader", "scope_type": "anyone"},
            "Invalid scope_type",
        ),
        (
            {
                "action": "share",
                "role": "reader",
                "scope_type": "default",
                "scope_value": "x",
            },
            "must be omitted",
        ),
        ({"action": "update", "role": "reader"}, "rule_id is required"),
        ({"action": "update", "rule_id": "user:a@example.com"}, "role is required"),
        ({"action": "remove", "rule_id": " "}, "rule_id is required"),
        ({"action": "grant"}, "Invalid action 'grant'"),
        ({"action": "remove", "rule_id": "user:ME@example.com"}, "owner's access"),
        (
            {
                "action": "update",
                "calendar_id": "me@example.com",
                "rule_id": "user:me@example.com",
                "role": "reader",
            },
            "owner's access",
        ),
    ],
)
async def test_invalid_input_raises_before_any_api_call(kwargs, message):
    service, acl = _service()

    with pytest.raises(UserInputError, match=message):
        await _call(service, **kwargs)

    for method in (acl.list, acl.insert, acl.patch, acl.delete):
        method.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["update", "remove"])
async def test_missing_rule_reports_list_hint(action):
    service, acl = _service()
    not_found = HttpError(Mock(status=404, reason="Not Found"), b"{}")
    acl.patch.return_value.execute.side_effect = not_found
    acl.delete.return_value.execute.side_effect = not_found

    with pytest.raises(UserInputError, match="No sharing rule 'user:x@example.com'"):
        await _call(service, action=action, rule_id="user:x@example.com", role="reader")


@pytest.mark.asyncio
async def test_other_api_errors_surface_through_handler():
    service, acl = _service()
    acl.delete.return_value.execute.side_effect = HttpError(
        Mock(status=403, reason="Forbidden"),
        b'{"error": {"code": 403, "message": "Cannot change your own access level."}}',
    )
    tool = handle_http_errors("manage_calendar_sharing", service_type="calendar")(
        _unwrap(manage_calendar_sharing)
    )

    with pytest.raises(Exception, match="Cannot change your own access level"):
        await tool(
            service=service,
            user_google_email="me@example.com",
            action="remove",
            calendar_id="team@group.calendar.google.com",
            rule_id="user:me@example.com",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("level", "removed"), [("readonly", ["manage_calendar_sharing"]), ("full", [])]
)
async def test_permissions_gate_tool_like_other_calendar_writes(
    monkeypatch, level, removed
):
    tool = await server.get_tool("manage_calendar_sharing")
    provider = SimpleNamespace(
        _components={"tool:manage_calendar_sharing@1": tool}, removed=[]
    )
    provider.remove_tool = provider.removed.append
    monkeypatch.setattr(tool_registry, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(tool_registry, "get_enabled_tools", lambda: None)
    monkeypatch.setattr(tool_registry, "get_disabled_tools", set)
    set_permissions({"calendar": level})
    try:
        tool_registry.filter_server_tools(SimpleNamespace(local_provider=provider))
    finally:
        set_permissions(None)

    assert provider.removed == removed
