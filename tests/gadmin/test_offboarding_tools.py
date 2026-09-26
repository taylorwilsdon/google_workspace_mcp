"""Offboarding tools cross the real MCP boundary, with only Google clients faked."""

import json
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client

import auth.scopes as scopes
import auth.service_decorator as decorator
import gadmin.admin_tools as tools
import gadmin.auth as auth
from core.server import server
from gadmin.confirm import ConfirmationStore
from gadmin.offboarding_store import WorkflowStore
from tests.gadmin.test_offboarding import ADMIN, LEAVER, MANAGER, Workspace


@pytest.fixture
def setup(monkeypatch, tmp_path):
    workspace = Workspace()
    monkeypatch.setattr(
        scopes,
        "_ENABLED_TOOLS",
        ["admin-directory", "admin-datatransfer", "admin-licensing"],
    )
    monkeypatch.setattr(
        decorator,
        "_authenticate_service",
        AsyncMock(return_value=(workspace.directory, ADMIN)),
    )

    async def get_service(
        use_oauth21,
        service,
        version,
        tool,
        selected,
        requested_scopes,
        session,
        identity_email,
        **kwargs,
    ):
        assert selected == ADMIN and kwargs.get("verify_account") is True
        return (
            workspace.transfer_api
            if version == "datatransfer_v1"
            else workspace.licensing_api
        ), ADMIN

    monkeypatch.setattr(auth, "_authenticate_service", get_service)
    monkeypatch.setattr(
        tools,
        "default_workflows",
        lambda: WorkflowStore(tmp_path / "workflows"),
        raising=False,
    )
    monkeypatch.setattr(
        tools,
        "default_confirmations",
        lambda: ConfirmationStore(tmp_path / "confirmations"),
        raising=False,
    )
    monkeypatch.setattr(
        tools,
        "_audit_sink",
        lambda: tools.AuditSink(tmp_path / "audit.jsonl"),
        raising=False,
    )
    return workspace


async def call(tool, **arguments):
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    return result, result.content[0].text


@pytest.mark.asyncio
async def test_plan_tool_reads_only_and_returns_persisted_workflow(setup):
    result, text = await call(
        "plan_user_offboarding",
        user_google_email=ADMIN,
        target_email=LEAVER,
        transfer_recipient=MANAGER,
    )
    assert not result.is_error, text
    plan = json.loads(text)
    assert plan["target_email"] == LEAVER
    assert plan["transfer_applications"] == ["Drive and Docs", "Calendar"]
    assert setup.writes() == []
    status, text = await call(
        "get_offboarding_status",
        user_google_email=ADMIN,
        workflow_id=plan["workflow_id"],
    )
    assert not status.is_error, text
    assert json.loads(text)["current_step"] == "suspend_user"


@pytest.mark.asyncio
async def test_advance_tool_requires_exact_confirmation_before_suspension(setup):
    result, text = await call(
        "plan_user_offboarding",
        user_google_email=ADMIN,
        target_email=LEAVER,
        transfer_recipient=MANAGER,
    )
    assert not result.is_error, text
    workflow_id = json.loads(text)["workflow_id"]
    result, text = await call(
        "advance_user_offboarding", user_google_email=ADMIN, workflow_id=workflow_id
    )
    assert not result.is_error, text
    state = json.loads(text)
    assert state["status"] == "awaiting_confirmation"
    assert "directory.users.update" in state["steps"][0]["message"]
    assert setup.writes() == []
    result, text = await call(
        "advance_user_offboarding",
        user_google_email=ADMIN,
        workflow_id=workflow_id,
        confirmation=state["confirmation"],
    )
    assert not result.is_error, text
    assert setup.directory.users_by_key[LEAVER]["suspended"] is True


@pytest.mark.asyncio
async def test_plan_tool_denies_other_customer_and_other_identity(setup, monkeypatch):
    setup.directory.add_user(
        "visitor@other.example", "U_VISITOR", customer_id="C_OTHER"
    )
    result, text = await call(
        "plan_user_offboarding",
        user_google_email=ADMIN,
        target_email="visitor@other.example",
        transfer_recipient=MANAGER,
    )
    assert result.is_error and "another customer" in text
    monkeypatch.setattr(
        decorator,
        "_get_auth_context",
        AsyncMock(return_value=("wrong@op.example", "oauth21", None)),
    )
    result, text = await call(
        "plan_user_offboarding",
        user_google_email=ADMIN,
        target_email=LEAVER,
        transfer_recipient=MANAGER,
    )
    assert result.is_error and "does not match" in text
    assert setup.writes() == []


@pytest.mark.asyncio
async def test_status_tool_denies_a_workflow_owned_by_another_actor(setup):
    result, text = await call(
        "plan_user_offboarding",
        user_google_email=ADMIN,
        target_email=LEAVER,
        transfer_recipient=MANAGER,
    )
    assert not result.is_error, text
    workflow_id = json.loads(text)["workflow_id"]
    setup.directory.add_user("second@op.example", "U_SECOND", delegated_admin=True)
    # The OAuth credential, not the caller's selected account, determines the actor.
    result, text = await call(
        "get_offboarding_status",
        user_google_email="second@op.example",
        workflow_id=workflow_id,
    )
    assert result.is_error
    assert "does not match" in text or "another admin" in text
