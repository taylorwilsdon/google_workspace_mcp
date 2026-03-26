from core.server import server
from core.tool_registry import get_tool_components
import gmail.gmail_tools  # noqa: F401


def test_modify_gmail_message_labels_optional_arrays_publish_array_type():
    components = get_tool_components(server)
    schema = components["modify_gmail_message_labels"].parameters["properties"]

    for field_name in ("add_label_ids", "remove_label_ids"):
        field_schema = schema[field_name]
        assert field_schema["type"] == "array"
        assert field_schema["items"] == {"type": "string"}
        assert field_schema["default"] is None


def test_batch_modify_gmail_message_labels_optional_arrays_publish_array_type():
    components = get_tool_components(server)
    schema = components["batch_modify_gmail_message_labels"].parameters["properties"]

    for field_name in ("add_label_ids", "remove_label_ids"):
        field_schema = schema[field_name]
        assert field_schema["type"] == "array"
        assert field_schema["items"] == {"type": "string"}
        assert field_schema["default"] is None
