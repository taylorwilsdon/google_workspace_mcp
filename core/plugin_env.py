"""Environment handling for agent hosts that run the plugin's MCP server.

The plugin manifest (.claude-plugin/plugin.json) fills the server's environment
from Claude Code plugin options such as ``${user_config.permissions}``. Hosts
without plugin options, for example GitHub Copilot CLI and the VS Code agent
host, start the same server but pass these placeholders on unexpanded.
"""

import os
import re
from pathlib import Path
from typing import MutableMapping, Optional

from dotenv import load_dotenv

_UNEXPANDED_PLUGIN_OPTION = re.compile(r"\$\{user_config\.[A-Za-z0-9_]+\}")

USER_ENV_FILE = Path("~/.config/google-workspace-mcp/.env")


def drop_unexpanded_plugin_options(
    environ: MutableMapping[str, str] = os.environ,
) -> list[str]:
    """Remove variables whose value is an unexpanded plugin option placeholder.

    Such a value is never meant literally, so treating it as unset lets the
    server start with its defaults or with settings from another source.
    Returns the names of the removed variables.
    """
    names = sorted(
        name
        for name, value in environ.items()
        if _UNEXPANDED_PLUGIN_OPTION.fullmatch(value.strip())
    )
    for name in names:
        del environ[name]
    return names


def load_user_env_file(path: Path = USER_ENV_FILE) -> Optional[Path]:
    """Load the user's settings file without overriding variables already set.

    Hosts without plugin options cannot pass settings through the variables the
    plugin manifest sets, so this file is where their users configure the server.
    """
    resolved = path.expanduser()
    if not resolved.is_file():
        return None
    load_dotenv(dotenv_path=resolved, override=False)
    return resolved
