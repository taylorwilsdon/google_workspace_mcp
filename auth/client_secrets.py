"""Shared resolution and loading of the Google OAuth client secrets file.

Used by both the legacy per-user Google grant flow (auth/google_auth.py) and
the OAuth 2.1 protocol auth configuration (auth/oauth_config.py) so they agree
on where the client secrets file lives and how it is parsed.

Path resolution priority:
1. GOOGLE_CLIENT_SECRET_PATH
2. GOOGLE_CLIENT_SECRETS (legacy alias)
3. <repo root>/client_secret.json (default)
"""

import json
import os
from typing import Any, Dict

_CLIENT_SECRET_PATH_ENV = "GOOGLE_CLIENT_SECRET_PATH"
_CLIENT_SECRETS_ENV = "GOOGLE_CLIENT_SECRETS"


def get_client_secrets_path() -> str:
    """Resolve the client secrets file path from environment variables.

    A "~" prefix is expanded; the default is <repo root>/client_secret.json.

    Returns:
        The path to the client secrets JSON file.
    """
    path = os.getenv(_CLIENT_SECRET_PATH_ENV) or os.getenv(_CLIENT_SECRETS_ENV)
    if path:
        # Container images and MCP client configs routinely pass "~/..." with no
        # shell to expand it, so resolve it here rather than failing to find it.
        return os.path.expanduser(path)
    # Assumes this file is in auth/ and client_secret.json is in the root
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "client_secret.json",
    )


def load_client_secrets_file(client_secrets_path: str) -> Dict[str, Any]:
    """Load the client credentials section from a client secrets JSON file.

    Args:
        client_secrets_path: Path to the client secrets JSON file.

    Returns:
        The "web" or "installed" section of the client secrets file.

    Raises:
        ValueError: If the top-level value is not a JSON object, or the
            "web"/"installed" section is missing or not an object.
        IOError: If the file cannot be read.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    with open(client_secrets_path, "r") as f:
        client_config = json.load(f)
    if not isinstance(client_config, dict):
        raise ValueError(
            f"Client secrets file {client_secrets_path} has unexpected format. "
            "Expected a top-level JSON object with a 'web' or 'installed' section."
        )
    # The file usually contains a top-level key like "web" or "installed"
    for section_name in ("web", "installed"):
        if section_name in client_config:
            section = client_config[section_name]
            if not isinstance(section, dict):
                raise ValueError(
                    f"Client secrets file {client_secrets_path} has unexpected format. "
                    f"The '{section_name}' section must be a JSON object."
                )
            return section
    raise ValueError(
        f"Client secrets file {client_secrets_path} has unexpected format. "
        "Expected a 'web' or 'installed' section."
    )
