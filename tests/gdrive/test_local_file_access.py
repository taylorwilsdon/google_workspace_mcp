"""Opt-in disabling of server-side file paths.

'file_path' resolves on the machine the SERVER runs on. That works for stdio and
for streamable-http on localhost, but not for a hosted deployment with no view of
the caller's disk. Operators of such deployments set
WORKSPACE_MCP_DISABLE_LOCAL_FILES=true (stateless mode implies it), which hides
file_path from tool schemas and refuses server-side paths at runtime.
Transport alone never disables it.
"""

import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastmcp import Client, FastMCP

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import (  # noqa: E402
    UserInputError,
    hide_local_file_args,
    local_file_access_enabled,
    validate_file_path,
)
from gdrive.drive_helpers import (  # noqa: E402
    GOOGLE_DOCS_IMPORT_FORMATS,
    _resolve_import_media,
)
from gdrive.drive_tools import (  # noqa: E402
    import_to_google_doc,
    update_drive_file,
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
DISABLED = patch("gdrive.drive_helpers.local_file_access_enabled", return_value=False)


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _run_subprocess(code: str, extra_env: dict[str, str]) -> str:
    # Modes that reshape tool signatures (OAuth 2.1 drops user_google_email) or
    # toggle local file access must not leak in from the developer's shell.
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "MCP_ENABLE_OAUTH21",
            "EXTERNAL_OAUTH21_PROVIDER",
            "WORKSPACE_MCP_STATELESS_MODE",
            "MCP_SINGLE_USER_MODE",
            "WORKSPACE_MCP_DISABLE_LOCAL_FILES",
        )
    }
    env.update(
        GOOGLE_OAUTH_CLIENT_ID="test-client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="test-client-secret",
        **extra_env,
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


class TestLocalFileAccessSetting:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", raising=False)
        with patch("core.utils.is_stateless_mode", return_value=False):
            assert local_file_access_enabled()

    def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", "true")
        with patch("core.utils.is_stateless_mode", return_value=False):
            assert not local_file_access_enabled()

    @pytest.mark.parametrize("value", [" true ", "TRUE", "true\n"])
    def test_disabled_by_untidy_true(self, value, monkeypatch):
        """A stray space or newline (YAML, .env) must not silently re-enable
        local files: the setting fails closed."""
        monkeypatch.setenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", value)
        with patch("core.utils.is_stateless_mode", return_value=False):
            assert not local_file_access_enabled()

    @pytest.mark.parametrize("value", ["false", ""])
    def test_enabled_by_non_true(self, value, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", value)
        with patch("core.utils.is_stateless_mode", return_value=False):
            assert local_file_access_enabled()

    def test_disabled_by_stateless_mode(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", raising=False)
        with patch("core.utils.is_stateless_mode", return_value=True):
            assert not local_file_access_enabled()

    @patch("core.utils.get_transport_mode", return_value="streamable-http")
    def test_transport_alone_does_not_disable(self, _mode, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", raising=False)
        with patch("core.utils.is_stateless_mode", return_value=False):
            assert local_file_access_enabled()


ENABLED_IN_UTILS = patch("core.utils.local_file_access_enabled", return_value=True)
DISABLED_IN_UTILS = patch("core.utils.local_file_access_enabled", return_value=False)


async def _sample(file_name: str, file_path: str | None = None) -> str:
    return f"{file_name}:{file_path}"


class TestHideLocalFileArgs:
    """FastMCP 4 removed ``exclude_args``, so hiding goes through
    ``__signature__``, which ``inspect.signature`` (and so FastMCP's schema
    builder) honours on every FastMCP version this project supports."""

    def test_no_op_when_enabled(self):
        async def fn(file_name: str, file_path: str | None = None) -> str:
            return ""

        with ENABLED_IN_UTILS:
            assert hide_local_file_args("file_path")(fn) is fn
        assert list(inspect.signature(fn).parameters) == ["file_name", "file_path"]

    def test_hides_only_the_named_parameters_when_disabled(self):
        async def fn(file_name: str, file_path: str | None = None) -> str:
            return ""

        with DISABLED_IN_UTILS:
            assert hide_local_file_args("file_path")(fn) is fn
        assert list(inspect.signature(fn).parameters) == ["file_name"]

    @pytest.mark.parametrize("enabled", [True, False])
    def test_unknown_name_fails_at_decoration_time(self, enabled):
        """A stale name must not pass silently under either setting."""
        with patch("core.utils.local_file_access_enabled", return_value=enabled):
            with pytest.raises(ValueError, match="no_such_param"):
                hide_local_file_args("no_such_param")(_sample)

    def test_no_tool_still_uses_exclude_args(self):
        """``exclude_args`` raises TypeError at import on FastMCP 4."""
        offenders = [
            path
            for path in Path(REPO_ROOT).rglob("*.py")
            if not any(part.startswith(".") or part == "tests" for part in path.parts)
            and re.search(r"\bexclude_args\s*=", path.read_text())
        ]
        assert offenders == []

    @pytest.mark.asyncio
    async def test_hidden_argument_is_rejected_by_fastmcp(self):
        """A client holding a cached schema cannot reach the tool body."""
        mcp = FastMCP("hide-test")

        async def fn(file_name: str, file_path: str | None = None) -> str:
            return f"{file_name}:{file_path}"

        with DISABLED_IN_UTILS:
            mcp.tool(hide_local_file_args("file_path")(fn))

        async with Client(mcp) as client:
            (tool,) = await client.list_tools()
            assert "file_path" not in tool.inputSchema["properties"]

            ok = await client.call_tool("fn", {"file_name": "n"})
            assert ok.content[0].text == "n:None"

            stale = await client.call_tool(
                "fn", {"file_name": "n", "file_path": "/x"}, raise_on_error=False
            )
            assert stale.is_error
            assert "file_path" in stale.content[0].text
            assert "Unexpected keyword argument" in stale.content[0].text


class TestGuidanceWhenDisabled:
    """Runtime advice must not send a model after a parameter the schema hides."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("enabled", [True, False])
    async def test_binary_content_error_names_file_path_only_when_enabled(
        self, enabled
    ):
        with patch(
            "gdrive.drive_helpers.local_file_access_enabled", return_value=enabled
        ):
            with pytest.raises(ValueError) as exc:
                await _resolve_import_media(
                    tool_name="t",
                    file_name="r.docx",
                    content="x",
                    file_path=None,
                    file_url=None,
                    source_format=None,
                    format_map=GOOGLE_DOCS_IMPORT_FORMATS,
                )
        msg = str(exc.value)
        assert "'file_url'" in msg
        assert ("file_path" in msg) is enabled

    @pytest.mark.asyncio
    @pytest.mark.parametrize("enabled", [True, False])
    async def test_missing_source_error_names_file_path_only_when_enabled(
        self, enabled
    ):
        with patch(
            "gdrive.drive_helpers.local_file_access_enabled", return_value=enabled
        ):
            with pytest.raises(ValueError) as exc:
                await _resolve_import_media(
                    tool_name="t",
                    file_name="r.md",
                    content=None,
                    file_path=None,
                    file_url=None,
                    source_format=None,
                    format_map=GOOGLE_DOCS_IMPORT_FORMATS,
                )
        msg = str(exc.value)
        assert "'content'" in msg and "'base64_content'" in msg
        assert ("file_path" in msg) is enabled

    @pytest.mark.asyncio
    async def test_update_append_error_does_not_name_file_path(self):
        with pytest.raises(ValueError, match="requires 'content'") as exc:
            await _unwrap(update_drive_file)(
                service=Mock(),
                user_google_email="user@example.com",
                file_id="abc123",
                file_url="https://example.com/notes.md",
                mode="append",
            )
        assert "file_path" not in str(exc.value)

    @pytest.mark.asyncio
    @DISABLED
    @patch("gdrive.drive_helpers.resolve_folder_id", new_callable=AsyncMock)
    async def test_inline_content_still_works(self, mock_folder, _enabled):
        mock_folder.return_value = "root"
        service = Mock()
        service.files().create().execute.return_value = {
            "id": "doc1",
            "name": "Notes",
            "webViewLink": "https://docs.google.com/doc1",
            "mimeType": "application/vnd.google-apps.document",
        }
        service.files().create.reset_mock()

        result = await _unwrap(import_to_google_doc)(
            service=service,
            user_google_email="user@example.com",
            file_name="Notes.md",
            content="# Title\n\nHello",
        )

        service.files().create.assert_called_once()
        assert "Successfully imported" in result


class TestFilePathWorksByDefault:
    @pytest.mark.asyncio
    @patch("core.utils.get_transport_mode", return_value="streamable-http")
    @patch("gdrive.drive_helpers.resolve_folder_id", new_callable=AsyncMock)
    async def test_localhost_http_keeps_file_path(
        self, mock_folder, _mode, tmp_path, monkeypatch
    ):
        """streamable-http on localhost shares the caller's filesystem, so the
        file_path route works end to end without any opt-in."""
        monkeypatch.delenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", raising=False)
        monkeypatch.setenv("ALLOWED_FILE_DIRS", str(tmp_path))
        src = tmp_path / "notes.md"
        src.write_text("# Title\n\nHello")
        mock_folder.return_value = "root"
        service = Mock()
        service.files().create().execute.return_value = {
            "id": "doc1",
            "name": "notes",
            "webViewLink": "https://docs.google.com/doc1",
            "mimeType": "application/vnd.google-apps.document",
        }
        service.files().create.reset_mock()

        with patch("core.utils.is_stateless_mode", return_value=False):
            result = await _unwrap(import_to_google_doc)(
                service=service,
                user_google_email="user@example.com",
                file_name="notes.md",
                file_path=str(src),
            )

        service.files().create.assert_called_once()
        assert "Successfully imported" in result


class TestValidateFilePath:
    def test_refuses_every_path_when_disabled(self, tmp_path, monkeypatch):
        """Covers call sites without their own guard (create_drive_file's
        file:// URLs, Gmail attachment paths)."""
        monkeypatch.setenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", "true")
        with pytest.raises(UserInputError, match="Local file access is disabled"):
            validate_file_path(str(tmp_path))

    @patch("core.utils.get_transport_mode", return_value="streamable-http")
    def test_missing_path_over_http_hints_at_the_server_boundary(
        self, _mode, monkeypatch
    ):
        monkeypatch.delenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", raising=False)
        with patch("core.utils.is_stateless_mode", return_value=False):
            with pytest.raises(FileNotFoundError) as exc:
                validate_file_path("/definitely/not/here.md")
        assert "different machine" in str(exc.value)

    @patch("core.utils.get_transport_mode", return_value="stdio")
    def test_missing_path_on_stdio_stays_plain(self, _mode, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", raising=False)
        with patch("core.utils.is_stateless_mode", return_value=False):
            with pytest.raises(FileNotFoundError) as exc:
                validate_file_path("/definitely/not/here.md")
        assert "different machine" not in str(exc.value)


class TestSchemaThroughFastMCP:
    """The signature is rewritten at decoration time, from the setting in force
    at import, so an in-process suite under the default can never see the
    hidden shape of the real tools. Go through a real client in a subprocess
    to cover both settings, and confirm a stale client's file_path is rejected
    by FastMCP's argument validation rather than reaching the tool body."""

    CODE = """
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from core.server import server, set_transport_mode
set_transport_mode('streamable-http')
import auth.service_decorator as sd
import gdrive.drive_tools
from fastmcp import Client

async def main():
    auth = AsyncMock(return_value=(MagicMock(), 'user@example.com'))
    with patch.object(sd, '_authenticate_service', auth):
        async with Client(server) as client:
            tools = {t.name: t for t in await client.list_tools()}
            for name in ('import_to_google_doc', 'import_to_google_slides',
                         'import_to_google_sheets', 'update_drive_file'):
                advertised = 'file_path' in tools[name].inputSchema['properties']
                print(f'ADVERTISED:{name}={advertised}')
            result = await client.call_tool(
                'import_to_google_slides',
                {'file_name': 'Deck', 'file_path': '/Users/someone/deck.pptx',
                 'user_google_email': 'user@example.com'},
                raise_on_error=False,
            )
            print('RESULT:' + result.content[0].text)

asyncio.run(main())
"""

    TOOLS = (
        "import_to_google_doc",
        "import_to_google_slides",
        "import_to_google_sheets",
        "update_drive_file",
    )

    def test_disabled_hides_file_path_and_rejects_stale_clients(self):
        out = _run_subprocess(self.CODE, {"WORKSPACE_MCP_DISABLE_LOCAL_FILES": "true"})
        for name in self.TOOLS:
            assert f"ADVERTISED:{name}=False" in out
        text = out.split("RESULT:", 1)[1]
        assert "file_path" in text
        assert "Unexpected keyword argument" in text
        # Rejected before the guard: the tool-specific advice never ran.
        assert "local file access is disabled" not in text

    def test_http_without_opt_in_still_advertises_file_path(self):
        out = _run_subprocess(self.CODE, {})
        for name in self.TOOLS:
            assert f"ADVERTISED:{name}=True" in out


class TestShippedTextNeverNamesAHiddenParameter:
    """FastMCP ships the docstring body above ``Args:`` as the tool description
    and each ``Args:`` line as its property's description. Hiding ``file_path``
    from the signature drops its own line, but prose elsewhere that names it
    would send a model after a parameter the schema does not have. One static
    text serves both settings, so it must not enumerate the local-only route."""

    CODE = """
import asyncio, json
from core.server import server, set_transport_mode
set_transport_mode('streamable-http')
import gdrive.drive_tools
from fastmcp import Client

async def main():
    async with Client(server) as client:
        shipped = {
            t.name: {
                "description": t.description or "",
                "properties": {
                    name: prop.get("description", "")
                    for name, prop in t.inputSchema["properties"].items()
                },
            }
            for t in await client.list_tools()
            if t.name in %r
        }
        print("SHIPPED:" + json.dumps(shipped, sort_keys=True))

asyncio.run(main())
""" % (TestSchemaThroughFastMCP.TOOLS,)

    def _shipped(self, env):
        out = _run_subprocess(self.CODE, env)
        return json.loads(out.split("SHIPPED:", 1)[1])

    def test_disabled_ships_no_text_naming_file_path(self):
        shipped = self._shipped({"WORKSPACE_MCP_DISABLE_LOCAL_FILES": "true"})
        assert set(shipped) == set(TestSchemaThroughFastMCP.TOOLS)
        offenders = [
            (name, where)
            for name, tool in shipped.items()
            for where, text in [("description", tool["description"])]
            + list(tool["properties"].items())
            if "file_path" in text
        ]
        assert offenders == []

    def test_enabled_ships_the_same_text_plus_the_parameter(self):
        """The wording is not switched per setting: each setting swaps file_path
        for return_upload_url, and the description and every other property
        read the same. The description names neither, since each is hidden
        under one setting."""
        swapped = {"file_path", "return_upload_url"}
        on = self._shipped({"WORKSPACE_MCP_DISABLE_LOCAL_FILES": "true"})
        off = self._shipped({})
        for name in TestSchemaThroughFastMCP.TOOLS:
            assert off[name]["description"] == on[name]["description"]
            assert "return_upload_url" not in off[name]["description"]
            assert swapped & set(off[name]["properties"]) == {"file_path"}
            assert swapped & set(on[name]["properties"]) == {"return_upload_url"}
            assert {
                k: v for k, v in off[name]["properties"].items() if k not in swapped
            } == {k: v for k, v in on[name]["properties"].items() if k not in swapped}
