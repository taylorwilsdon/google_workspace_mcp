from auth.scopes import GMAIL_COMPOSE_SCOPE
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


def test_gmail_draft_lifecycle_tools_are_registered():
    components = get_tool_components(server)

    assert "draft_gmail_message" in components
    assert "update_gmail_draft" in components
    assert "delete_gmail_draft" in components


def test_gmail_draft_lifecycle_tools_require_draft_id_for_mutation():
    components = get_tool_components(server)

    for tool_name in ("update_gmail_draft", "delete_gmail_draft"):
        schema = components[tool_name].parameters
        assert "draft_id" in schema["required"]
        assert schema["properties"]["draft_id"]["type"] == "string"


def test_gmail_draft_lifecycle_tools_use_compose_scope_not_send_scope():
    components = get_tool_components(server)

    for tool_name in (
        "draft_gmail_message",
        "update_gmail_draft",
        "delete_gmail_draft",
    ):
        assert components[tool_name].fn._required_google_scopes == [GMAIL_COMPOSE_SCOPE]


def test_update_gmail_draft_schema_documents_replacement_attachments():
    components = get_tool_components(server)
    properties = components["update_gmail_draft"].parameters["properties"]

    attachments_description = properties["attachments"]["description"]
    assert "'url'" in attachments_description
    assert "'mime_type'" in attachments_description
    assert "Omit to preserve" not in attachments_description
