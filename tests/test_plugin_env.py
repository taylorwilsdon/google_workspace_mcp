import os
import subprocess
import sys

from core.plugin_env import drop_unexpanded_plugin_options, load_user_env_file

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_drop_unexpanded_plugin_options_removes_only_placeholders():
    environ = {
        "WORKSPACE_MCP_TOOLS": "${user_config.tools}",
        "WORKSPACE_MCP_PERMISSIONS": " ${user_config.permissions} ",
        "GOOGLE_OAUTH_CLIENT_ID": "id.apps.googleusercontent.com",
        "WORKSPACE_MCP_READ_ONLY": "",
        "SOME_PATH": "${HOME}/x",
    }

    dropped = drop_unexpanded_plugin_options(environ)

    assert dropped == ["WORKSPACE_MCP_PERMISSIONS", "WORKSPACE_MCP_TOOLS"]
    assert environ == {
        "GOOGLE_OAUTH_CLIENT_ID": "id.apps.googleusercontent.com",
        "WORKSPACE_MCP_READ_ONLY": "",
        "SOME_PATH": "${HOME}/x",
    }


def test_load_user_env_file_does_not_override_set_variables(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WORKSPACE_MCP_PERMISSIONS=gmail:readonly\nUSER_GOOGLE_EMAIL=file@example.com\n"
    )
    monkeypatch.delenv("WORKSPACE_MCP_PERMISSIONS", raising=False)
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "host@example.com")

    assert load_user_env_file(env_file) == env_file
    assert os.environ["WORKSPACE_MCP_PERMISSIONS"] == "gmail:readonly"
    assert os.environ["USER_GOOGLE_EMAIL"] == "host@example.com"


def test_load_user_env_file_missing_returns_none(tmp_path):
    assert load_user_env_file(tmp_path / "missing.env") is None


def test_server_starts_with_unexpanded_plugin_env_and_user_settings(tmp_path):
    """The plugin manifest's environment as a host without plugin options passes it."""
    config_dir = tmp_path / ".config" / "google-workspace-mcp"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text("WORKSPACE_MCP_PERMISSIONS=gmail:readonly\n")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("WORKSPACE_MCP_", "GOOGLE_"))
    }
    env.update(
        HOME=str(tmp_path),
        GOOGLE_OAUTH_CLIENT_ID="${user_config.google_oauth_client_id}",
        GOOGLE_OAUTH_CLIENT_SECRET="${user_config.google_oauth_client_secret}",
        WORKSPACE_MCP_TOOLS="${user_config.tools}",
        WORKSPACE_MCP_TOOL_TIER="${user_config.tool_tier}",
        WORKSPACE_MCP_READ_ONLY="${user_config.read_only}",
        WORKSPACE_MCP_PERMISSIONS="${user_config.permissions}",
        WORKSPACE_MCP_HTTP_PORT="${user_config.http_port}",
        OAUTHLIB_INSECURE_TRANSPORT="1",
        MCP_ENABLE_OAUTH21="false",
    )
    script = (
        "import sys, main\n"
        "sys.argv = ['main.py']\n"
        "main.validate_streamable_http_auth = lambda transport: sys.exit(0)\n"
        "main.main()\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "invalid WORKSPACE_MCP" not in result.stderr
    assert "Ignoring unexpanded plugin options" in result.stderr
    assert "Loaded settings from" in result.stderr
