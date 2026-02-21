"""
Common test utils for gkeep tests.
"""

def unwrap(tool):
    """Unwrap a FunctionTool + decorator chain to the original async function."""
    fn = getattr(tool, "fn", tool)  # FunctionTool stores the wrapped callable in .fn
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def make_note_dict(
    name="notes/abc123",
    title="Test Note",
    text="Hello world",
    trashed=False,
    attachments=None,
    permissions=None,
):
    """Build a minimal Keep API note dict for testing (simulates raw API response)."""
    note = {
        "name": name,
        "title": title,
        "body": {"text": {"text": text}},
        "createTime": "2025-01-01T00:00:00Z",
        "updateTime": "2025-01-01T12:00:00Z",
        "trashed": trashed,
    }
    if trashed:
        note["trashTime"] = "2025-01-02T00:00:00Z"
    if attachments is not None:
        note["attachments"] = attachments
    if permissions is not None:
        note["permissions"] = permissions
    return note


def make_list_note_dict(
    name="notes/list123",
    title="Checklist",
    items=None,
):
    """Build a Keep note dict with list/checklist body (simulates raw API response)."""
    if items is None:
        items = [
            {"text": {"text": "Item 1"}, "checked": False},
            {"text": {"text": "Item 2"}, "checked": True},
        ]
    return {
        "name": name,
        "title": title,
        "body": {"list": {"listItems": items}},
        "createTime": "2025-01-01T00:00:00Z",
        "updateTime": "2025-01-01T12:00:00Z",
        "trashed": False,
    }