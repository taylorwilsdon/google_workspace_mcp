"""Schema contract tests for analyze_thread_ownership.

Follows the pattern in test_modify_gmail_message_labels_schema.py: inspect
the registered tool via core.tool_registry.get_tool_components and assert
the input schema shape. This locks the public contract so parameter renames
or type changes break CI instead of silently shipping.

The tool's return type is `Dict[str, Any]`, which FastMCP serializes as an
unconstrained object — we cannot assert return-shape keys via the schema
alone. The behavioral tests in test_analyze_thread_ownership.py pin the
return keys via direct dict assertions instead.
"""

from core.server import server
from core.tool_registry import get_tool_components
import gmail.gmail_tools  # noqa: F401


def test_analyze_thread_ownership_is_registered():
    """Tool must appear in the registered tool components. If this fails,
    either the @server.tool decoration is missing or the module isn't
    imported by the server entrypoint."""
    components = get_tool_components(server)
    assert "analyze_thread_ownership" in components


def test_analyze_thread_ownership_required_params():
    """thread_id and user_google_email are both required string params."""
    components = get_tool_components(server)
    tool = components["analyze_thread_ownership"]
    params = tool.parameters

    properties = params["properties"]
    assert "thread_id" in properties
    assert "user_google_email" in properties
    assert properties["thread_id"]["type"] == "string"
    assert properties["user_google_email"]["type"] == "string"

    # Both parameters must be required — no defaults.
    required = set(params.get("required", []))
    assert "thread_id" in required, (
        "thread_id must be required; a missing thread ID has no safe default"
    )
    assert "user_google_email" in required, (
        "user_google_email must be required; ball-in-court determination "
        "depends on knowing the authenticated user"
    )


def test_analyze_thread_ownership_no_unexpected_params():
    """Guard against accidental parameter bloat. If a new parameter is
    added, this test forces a deliberate update rather than silent
    expansion of the public contract."""
    components = get_tool_components(server)
    params = components["analyze_thread_ownership"].parameters
    properties = set(params["properties"].keys())
    assert properties == {"thread_id", "user_google_email"}, (
        f"Unexpected parameters in analyze_thread_ownership schema: "
        f"{properties - {'thread_id', 'user_google_email'}}"
    )
