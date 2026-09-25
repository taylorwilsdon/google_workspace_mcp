import inspect
import os
import sys
from unittest.mock import Mock

import pytest
from pydantic import TypeAdapter, ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from fastmcp.exceptions import ToolError

from auth.scopes import GMAIL_MODIFY_SCOPE, GMAIL_SETTINGS_BASIC_SCOPE
from core.utils import JsonDict
from gmail.gmail_helpers import filter_criteria_to_query
from gmail.gmail_tools import manage_gmail_filter


def _unwrap(tool):
    """Unwrap decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _annotation_adapter(param_name: str) -> TypeAdapter:
    """Match the Pydantic validation path used for annotated tool params."""
    annotation = (
        inspect.signature(manage_gmail_filter).parameters[param_name].annotation
    )
    return TypeAdapter(annotation)


class TestJsonDictValidation:
    def test_json_dict_accepts_native_dict(self):
        assert TypeAdapter(JsonDict).validate_python({"from": "test@example.com"}) == {
            "from": "test@example.com"
        }

    def test_json_dict_coerces_json_object_string(self):
        assert TypeAdapter(JsonDict).validate_python('{"from":"test@example.com"}') == {
            "from": "test@example.com"
        }

    def test_json_dict_rejects_non_object_json_string(self):
        with pytest.raises(ValidationError):
            TypeAdapter(JsonDict).validate_python('["a", "b"]')

    def test_manage_gmail_filter_signature_coerces_json_strings(self):
        criteria = _annotation_adapter("criteria").validate_python(
            '{"from":"notifications@github.com"}'
        )
        filter_action = _annotation_adapter("filter_action").validate_python(
            '{"addLabelIds":["Label_1"],"removeLabelIds":["INBOX"]}'
        )

        assert criteria == {"from": "notifications@github.com"}
        assert filter_action == {
            "addLabelIds": ["Label_1"],
            "removeLabelIds": ["INBOX"],
        }


@pytest.mark.asyncio
async def test_manage_gmail_filter_create_uses_coerced_dict_params():
    mock_service = Mock()
    mock_service.users().settings().filters().create().execute.return_value = {
        "id": "filter_abc"
    }

    criteria = _annotation_adapter("criteria").validate_python(
        '{"from":"notifications@github.com"}'
    )
    filter_action = _annotation_adapter("filter_action").validate_python(
        '{"addLabelIds":["Label_1"],"removeLabelIds":["INBOX"]}'
    )

    result = await _unwrap(manage_gmail_filter)(
        service=mock_service,
        user_google_email="user@example.com",
        action="create",
        criteria=criteria,
        filter_action=filter_action,
    )

    mock_service.users().settings().filters().create.assert_any_call(
        userId="me",
        body={
            "criteria": {"from": "notifications@github.com"},
            "action": {
                "addLabelIds": ["Label_1"],
                "removeLabelIds": ["INBOX"],
            },
        },
    )
    assert "filter_abc" in result


@pytest.mark.asyncio
async def test_manage_gmail_filter_delete_works():
    mock_service = Mock()
    mock_service.users().settings().filters().get().execute.return_value = {
        "id": "filter_123",
        "criteria": {"from": "old@example.com"},
        "action": {"addLabelIds": ["TRASH"]},
    }
    mock_service.users().settings().filters().delete().execute.return_value = None

    result = await _unwrap(manage_gmail_filter)(
        service=mock_service,
        user_google_email="user@example.com",
        action="delete",
        filter_id="filter_123",
    )

    assert "deleted" in result.lower()
    assert "filter_123" in result


# --- update / apply ---------------------------------------------------------


def test_filter_criteria_to_query_translates_all_supported_fields():
    query = filter_criteria_to_query(
        {
            "from": "a@b.com",
            "to": "me@x.com",
            "subject": "monthly invoice",
            "query": "pdf OR invoice",
            "negatedQuery": "newsletter",
            "hasAttachment": True,
            "size": 1000,
            "sizeComparison": "larger",
        }
    )
    assert query == (
        "from:a@b.com to:me@x.com subject:(monthly invoice) (pdf OR invoice) "
        "-(newsletter) has:attachment larger:1000"
    )


def test_filter_criteria_to_query_empty():
    assert filter_criteria_to_query({}) == ""


@pytest.mark.parametrize("comparison", [None, "unspecified"])
def test_filter_criteria_to_query_rejects_size_without_comparison(comparison):
    criteria = {"from": "a@b.com", "size": 1000}
    if comparison:
        criteria["sizeComparison"] = comparison
    with pytest.raises(ValueError, match="sizeComparison"):
        filter_criteria_to_query(criteria)


@pytest.mark.asyncio
async def test_update_creates_new_filter_before_deleting_old_and_keeps_criteria():
    mock_service = Mock()
    filters = mock_service.users().settings().filters()
    filters.get().execute.return_value = {
        "id": "old",
        "criteria": {"from": "a@b.com"},
        "action": {"addLabelIds": ["L1"]},
    }
    calls = []

    def _create(**kwargs):
        calls.append(("create", kwargs["body"]))
        return Mock(execute=lambda: {"id": "new"})

    def _delete(**kwargs):
        calls.append(("delete", kwargs["id"]))
        return Mock(execute=lambda: None)

    filters.create.side_effect = _create
    filters.delete.side_effect = _delete

    result = await _unwrap(manage_gmail_filter)(
        service=mock_service,
        user_google_email="user@example.com",
        action="update",
        filter_id="old",
        filter_action={"addLabelIds": ["L2"], "removeLabelIds": ["INBOX"]},
    )

    assert calls == [
        (
            "create",
            {
                "criteria": {"from": "a@b.com"},
                "action": {"addLabelIds": ["L2"], "removeLabelIds": ["INBOX"]},
            },
        ),
        ("delete", "old"),
    ]
    assert "New filter ID: new" in result


@pytest.mark.asyncio
async def test_update_reports_both_ids_when_old_filter_delete_fails():
    mock_service = Mock()
    filters = mock_service.users().settings().filters()
    filters.get().execute.return_value = {
        "id": "old",
        "criteria": {"from": "a@b.com"},
        "action": {"addLabelIds": ["L1"]},
    }
    filters.create().execute.return_value = {"id": "new"}
    filters.delete().execute.side_effect = RuntimeError("backend error")

    with pytest.raises(ToolError) as excinfo:
        await _unwrap(manage_gmail_filter)(
            service=mock_service,
            user_google_email="user@example.com",
            action="update",
            filter_id="old",
            filter_action={"addLabelIds": ["L2"]},
        )

    message = str(excinfo.value)
    assert "new" in message and "old" in message
    assert "both are active" in message


@pytest.mark.asyncio
async def test_update_requires_filter_id_and_a_change():
    with pytest.raises(ValueError):
        await _unwrap(manage_gmail_filter)(
            service=Mock(),
            user_google_email="user@example.com",
            action="update",
            filter_action={"addLabelIds": ["L"]},
        )
    with pytest.raises(ValueError):
        await _unwrap(manage_gmail_filter)(
            service=Mock(),
            user_google_email="user@example.com",
            action="update",
            filter_id="f",
        )


@pytest.mark.asyncio
async def test_apply_dry_run_counts_without_modifying():
    mock_service = Mock()
    mock_service.users().settings().filters().get().execute.return_value = {
        "criteria": {"from": "x@y.com"},
        "action": {"addLabelIds": ["Label_1"], "removeLabelIds": ["INBOX"]},
    }
    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}, {"id": "m2"}]
    }

    result = await _unwrap(manage_gmail_filter)(
        service=mock_service,
        user_google_email="user@example.com",
        action="apply",
        filter_id="f1",
        dry_run=True,
    )

    assert "DRY RUN" in result
    assert "Query: from:x@y.com" in result
    assert "Matching messages: 2" in result
    mock_service.users().messages().batchModify.assert_not_called()


@pytest.mark.asyncio
async def test_apply_paginates_and_chunks_batch_modify():
    mock_service = Mock()
    mock_service.users().messages().list().execute.side_effect = [
        {"messages": [{"id": f"a{i}"} for i in range(500)], "nextPageToken": "p2"},
        {"messages": [{"id": f"b{i}"} for i in range(700)]},
    ]

    result = await _unwrap(manage_gmail_filter)(
        service=mock_service,
        user_google_email="user@example.com",
        action="apply",
        criteria={"from": "x@y.com"},
        filter_action={"addLabelIds": ["Label_1"], "forward": "z@z.com"},
    )

    bodies = [
        c.kwargs["body"]
        for c in mock_service.users().messages().batchModify.call_args_list
        if "body" in c.kwargs
    ]
    assert [len(b["ids"]) for b in bodies] == [1000, 200]
    assert all(b["addLabelIds"] == ["Label_1"] for b in bodies)
    assert "Applied to 1200 messages." in result
    assert "Forwarding is not applied" in result


@pytest.mark.asyncio
async def test_apply_respects_max_messages():
    mock_service = Mock()
    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": f"m{i}"} for i in range(500)],
        "nextPageToken": "more",
    }

    result = await _unwrap(manage_gmail_filter)(
        service=mock_service,
        user_google_email="user@example.com",
        action="apply",
        criteria={"from": "x"},
        filter_action={"addLabelIds": ["L"]},
        max_messages=10,
    )

    body = mock_service.users().messages().batchModify.call_args.kwargs["body"]
    assert len(body["ids"]) == 10
    assert "Matching messages: 10+" in result
    assert "raise it" in result


@pytest.mark.asyncio
async def test_apply_refuses_empty_query_and_missing_input():
    with pytest.raises(ValueError):
        await _unwrap(manage_gmail_filter)(
            service=Mock(),
            user_google_email="user@example.com",
            action="apply",
        )
    with pytest.raises(ValueError):
        await _unwrap(manage_gmail_filter)(
            service=Mock(),
            user_google_email="user@example.com",
            action="apply",
            criteria={"excludeChats": True},
            filter_action={"addLabelIds": ["L"]},
        )


def test_manage_gmail_filter_requires_only_settings_basic_scope():
    assert manage_gmail_filter._required_google_scopes == [GMAIL_SETTINGS_BASIC_SCOPE]


@pytest.mark.asyncio
async def test_apply_refuses_token_without_modify_scope():
    mock_service = Mock()
    mock_service._http.credentials.scopes = [GMAIL_SETTINGS_BASIC_SCOPE]

    with pytest.raises(ToolError, match="gmail.modify"):
        await _unwrap(manage_gmail_filter)(
            service=mock_service,
            user_google_email="user@example.com",
            action="apply",
            criteria={"from": "x@y.com"},
            filter_action={"addLabelIds": ["L"]},
        )
    mock_service.users().messages().list.assert_not_called()


@pytest.mark.asyncio
async def test_apply_runs_with_modify_scope():
    mock_service = Mock()
    mock_service._http.credentials.scopes = [
        GMAIL_SETTINGS_BASIC_SCOPE,
        GMAIL_MODIFY_SCOPE,
    ]
    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}]
    }

    result = await _unwrap(manage_gmail_filter)(
        service=mock_service,
        user_google_email="user@example.com",
        action="apply",
        criteria={"from": "x@y.com"},
        filter_action={"addLabelIds": ["L"]},
        dry_run=True,
    )

    assert "Matching messages: 1" in result
