"""Finding 41: a tool that declares no Google scopes must not bypass the filters.

Both the read-only and the granular-permissions filter wrote
``if required_scopes:``, so a tool with no ``_required_google_scopes`` fell through
every check and stayed enabled -- the opposite of what a restrictive mode is for.
Unknown requirements are now a reason to disable; a tool that genuinely needs no
scopes says so with ``@requires_no_google_scopes``.
"""

from types import SimpleNamespace

import pytest

import core.tool_registry as tool_registry
from core.tool_registry import (
    _scope_requirement,
    filter_server_tools,
    requires_no_google_scopes,
)

DRIVE_READ = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_WRITE = "https://www.googleapis.com/auth/drive"


class TestScopeRequirement:
    def test_declared_scopes_are_returned(self):
        def tool():
            pass

        tool._required_google_scopes = [DRIVE_READ]

        assert _scope_requirement(tool) == [DRIVE_READ]

    def test_undeclared_is_none_not_empty(self):
        """`None` and `[]` must stay distinguishable: unknown vs known-to-be-none."""

        def tool():
            pass

        assert _scope_requirement(tool) is None

    def test_explicit_marker_reports_no_scopes(self):
        @requires_no_google_scopes
        def tool():
            pass

        assert _scope_requirement(tool) == []

    def test_reads_through_a_fastmcp_tool_wrapper(self):
        def fn():
            pass

        fn._required_google_scopes = [DRIVE_WRITE]

        assert _scope_requirement(SimpleNamespace(fn=fn)) == [DRIVE_WRITE]


def _tool(scopes=None, *, mark_scope_free=False):
    def fn():
        pass

    if scopes is not None:
        fn._required_google_scopes = list(scopes)
    if mark_scope_free:
        requires_no_google_scopes(fn)
    return SimpleNamespace(fn=fn)


class _FakeLocalProvider:
    """Removal target: filter_server_tools calls server.local_provider.remove_tool."""

    def __init__(self):
        self.removed = []

    def remove_tool(self, name):
        self.removed.append(name)


class _FakeServer:
    """Server stand-in exposing just what filter_server_tools touches."""

    def __init__(self):
        self.local_provider = _FakeLocalProvider()

    @property
    def removed(self):
        return self.local_provider.removed


@pytest.fixture
def registry(monkeypatch):
    """Neutralise every filter except the one under test."""
    monkeypatch.setattr(tool_registry, "get_enabled_tools", lambda: None)
    monkeypatch.setattr(tool_registry, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(tool_registry, "get_disabled_tools", lambda: set())
    monkeypatch.setattr(tool_registry, "is_permissions_mode", lambda: False)
    monkeypatch.setattr(tool_registry, "is_read_only_mode", lambda: False)
    return monkeypatch


def _run_filter(monkeypatch, tools):
    server = _FakeServer()
    monkeypatch.setattr(tool_registry, "get_tool_components", lambda _s: dict(tools))
    filter_server_tools(server)
    return set(server.removed)


class TestReadOnlyMode:
    def test_undeclared_tool_is_disabled(self, registry, monkeypatch):
        monkeypatch.setattr(tool_registry, "is_read_only_mode", lambda: True)
        monkeypatch.setattr(
            tool_registry, "get_all_read_only_scopes", lambda: [DRIVE_READ]
        )

        removed = _run_filter(monkeypatch, {"mystery": _tool(None)})

        assert removed == {"mystery"}

    def test_write_scoped_tool_is_still_disabled(self, registry, monkeypatch):
        monkeypatch.setattr(tool_registry, "is_read_only_mode", lambda: True)
        monkeypatch.setattr(
            tool_registry, "get_all_read_only_scopes", lambda: [DRIVE_READ]
        )

        removed = _run_filter(monkeypatch, {"writer": _tool([DRIVE_WRITE])})

        assert removed == {"writer"}

    def test_read_scoped_tool_is_kept(self, registry, monkeypatch):
        monkeypatch.setattr(tool_registry, "is_read_only_mode", lambda: True)
        monkeypatch.setattr(
            tool_registry, "get_all_read_only_scopes", lambda: [DRIVE_READ]
        )

        removed = _run_filter(monkeypatch, {"reader": _tool([DRIVE_READ])})

        assert removed == set()

    def test_explicitly_scope_free_tool_is_kept(self, registry, monkeypatch):
        """start_google_auth must survive, or the user can never authenticate."""
        monkeypatch.setattr(tool_registry, "is_read_only_mode", lambda: True)
        monkeypatch.setattr(
            tool_registry, "get_all_read_only_scopes", lambda: [DRIVE_READ]
        )

        removed = _run_filter(monkeypatch, {"bootstrap": _tool(mark_scope_free=True)})

        assert removed == set()


class TestPermissionsMode:
    def test_undeclared_tool_is_disabled(self, registry, monkeypatch):
        monkeypatch.setattr(tool_registry, "is_permissions_mode", lambda: True)
        monkeypatch.setattr(
            tool_registry, "get_allowed_scopes_set", lambda: {DRIVE_READ}
        )

        removed = _run_filter(monkeypatch, {"mystery": _tool(None)})

        assert removed == {"mystery"}

    def test_disallowed_scope_is_still_disabled(self, registry, monkeypatch):
        monkeypatch.setattr(tool_registry, "is_permissions_mode", lambda: True)
        monkeypatch.setattr(
            tool_registry, "get_allowed_scopes_set", lambda: {DRIVE_READ}
        )

        removed = _run_filter(monkeypatch, {"writer": _tool([DRIVE_WRITE])})

        assert removed == {"writer"}

    def test_allowed_scope_is_kept(self, registry, monkeypatch):
        monkeypatch.setattr(tool_registry, "is_permissions_mode", lambda: True)
        monkeypatch.setattr(
            tool_registry, "get_allowed_scopes_set", lambda: {DRIVE_READ}
        )

        removed = _run_filter(monkeypatch, {"reader": _tool([DRIVE_READ])})

        assert removed == set()

    def test_explicitly_scope_free_tool_is_kept(self, registry, monkeypatch):
        monkeypatch.setattr(tool_registry, "is_permissions_mode", lambda: True)
        monkeypatch.setattr(
            tool_registry, "get_allowed_scopes_set", lambda: {DRIVE_READ}
        )

        removed = _run_filter(monkeypatch, {"bootstrap": _tool(mark_scope_free=True)})

        assert removed == set()


class TestUnrestrictedMode:
    def test_undeclared_tool_is_kept_when_no_mode_restricts_it(
        self, registry, monkeypatch
    ):
        """The fail-closed rule belongs to the restrictive modes, not to normal use."""
        removed = _run_filter(monkeypatch, {"mystery": _tool(None)})

        assert removed == set()


def test_every_registered_tool_declares_its_scope_requirement():
    """A new tool must not silently become undeclared.

    This is the regression guard for finding 41: the filters are only as good as the
    declarations they read, so the whole registered set is checked.
    """
    import importlib
    import logging

    logging.disable(logging.CRITICAL)
    try:
        for module in (
            "gmail.gmail_tools",
            "gdrive.drive_tools",
            "gcalendar.calendar_tools",
            "gdocs.docs_tools",
            "gsheets.sheets_tools",
            "gchat.chat_tools",
            "gforms.forms_tools",
            "gslides.slides_tools",
            "gtasks.tasks_tools",
            "gcontacts.contacts_tools",
            "gsearch.search_tools",
            "gappsscript.apps_script_tools",
        ):
            importlib.import_module(module)

        from core.server import server
        from core.tool_registry import get_tool_components

        tools = get_tool_components(server)
        undeclared = sorted(
            name for name, obj in tools.items() if _scope_requirement(obj) is None
        )
    finally:
        logging.disable(logging.NOTSET)

    assert tools, "no tools were registered; the import list above is wrong"
    assert undeclared == [], (
        "these tools declare no Google scopes and would be disabled in read-only or "
        f"permission-restricted modes: {undeclared}. Add @require_google_service, or "
        "@requires_no_google_scopes if the tool calls no Google API."
    )
