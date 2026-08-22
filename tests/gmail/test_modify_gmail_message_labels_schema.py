from core.server import server
from core.tool_registry import get_tool_components
import gmail.gmail_tools  # noqa: F401


def _assert_optional_string_array_anyof(field_schema):
    """Assert that field_schema describes `Optional[List[str]]` in Moonshot-
    compatible form: an ``anyOf`` with an array-of-strings branch and a null
    branch, and no redundant parent-level ``type``/``items``.
    """
    assert "anyOf" in field_schema
    assert "type" not in field_schema, (
        "Parent-level `type` alongside `anyOf` breaks Moonshot/Kimi strict "
        "JSON Schema validation"
    )
    assert "items" not in field_schema
    assert field_schema["default"] is None

    branches = field_schema["anyOf"]
    assert len(branches) == 2
    assert {"type": "array", "items": {"type": "string"}} in branches
    assert {"type": "null"} in branches


def test_modify_gmail_message_labels_optional_arrays_publish_anyof_array_null():
    components = get_tool_components(server)
    schema = components["modify_gmail_message_labels"].parameters["properties"]

    for field_name in ("add_label_ids", "remove_label_ids"):
        _assert_optional_string_array_anyof(schema[field_name])


def test_batch_modify_gmail_message_labels_optional_arrays_publish_anyof_array_null():
    components = get_tool_components(server)
    schema = components["batch_modify_gmail_message_labels"].parameters["properties"]

    for field_name in ("add_label_ids", "remove_label_ids"):
        _assert_optional_string_array_anyof(schema[field_name])
