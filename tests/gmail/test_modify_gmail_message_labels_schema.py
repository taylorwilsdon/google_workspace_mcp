import inspect

import gmail.gmail_tools  # noqa: F401
import pytest
from pydantic import TypeAdapter, ValidationError

from core.server import server
from core.tool_registry import get_tool_components


def _assert_label_id_list_schema(field_schema):
    assert field_schema["type"] == "array"
    assert field_schema["items"] == {"type": "string"}
    assert "anyOf" not in field_schema
    assert field_schema["default"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    ["modify_gmail_message_labels", "batch_modify_gmail_message_labels"],
)
async def test_gmail_label_lists_publish_top_level_array_schema(tool_name):
    tools = {tool.name: tool for tool in await server.list_tools()}
    schema = tools[tool_name].parameters["properties"]

    for field_name in ("add_label_ids", "remove_label_ids"):
        _assert_label_id_list_schema(schema[field_name])


@pytest.mark.parametrize(
    "tool_name",
    ["modify_gmail_message_labels", "batch_modify_gmail_message_labels"],
)
def test_gmail_label_lists_accept_runtime_compatibility_inputs(tool_name):
    tool = get_tool_components(server)[tool_name]

    for field_name in ("add_label_ids", "remove_label_ids"):
        annotation = inspect.signature(tool.fn).parameters[field_name].annotation
        adapter = TypeAdapter(annotation)

        assert adapter.validate_python(["Label_57"]) == ["Label_57"]
        assert adapter.validate_python('["Label_57"]') == ["Label_57"]
        assert adapter.validate_python(None) is None
        with pytest.raises(ValidationError):
            adapter.validate_python("Label_57")
