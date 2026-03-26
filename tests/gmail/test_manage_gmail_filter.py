import inspect
import os
import sys
from unittest.mock import Mock

import pytest
from pydantic import TypeAdapter, ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import JsonDict
from gmail.gmail_tools import manage_gmail_filter


def _unwrap(tool):
    """Unwrap decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _annotation_adapter(param_name: str) -> TypeAdapter:
    """Match the Pydantic validation path used for annotated tool params."""
    annotation = inspect.signature(manage_gmail_filter).parameters[param_name].annotation
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
