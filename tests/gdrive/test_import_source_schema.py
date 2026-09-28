"""Schema guardrails for conversion tools with mutually exclusive source fields."""

import json
import os
import subprocess
import sys


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def _run_subprocess(code: str, extra_env: dict[str, str]) -> str:
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


class TestImportConversionSchemas:
    CODE = """
import asyncio, json
from unittest.mock import AsyncMock, MagicMock, patch
from core.server import server
import auth.service_decorator as sd
import gdrive.drive_tools
from fastmcp import Client

NAMES = ['import_to_google_doc', 'import_to_google_sheets', 'import_to_google_slides']

async def main():
    auth = AsyncMock(return_value=(MagicMock(), 'user@example.com'))
    with patch.object(sd, '_authenticate_service', auth):
        async with Client(server) as client:
            tools = {t.name: t for t in await client.list_tools()}
            print('SCHEMAS:' + json.dumps({n: tools[n].inputSchema for n in NAMES}, sort_keys=True))

asyncio.run(main())
"""

    def _schemas(self, extra_env):
        out = _run_subprocess(self.CODE, extra_env)
        return json.loads(out.split("SCHEMAS:", 1)[1])

    def test_local_mode_advertises_exactly_one_source_field(self):
        schemas = self._schemas({})
        for name, schema in schemas.items():
            assert "return_upload_url" not in schema["properties"], name
            assert schema["oneOf"] == self.EXPECTED_LOCAL_ONE_OF[name], name

    def test_remote_mode_includes_upload_url_variant(self):
        schemas = self._schemas({"WORKSPACE_MCP_DISABLE_LOCAL_FILES": "true"})
        for name, schema in schemas.items():
            assert "file_path" not in schema["properties"], name
            assert "return_upload_url" in schema["properties"], name
            assert schema["oneOf"] == self.EXPECTED_REMOTE_ONE_OF[name], name

    EXPECTED_LOCAL_ONE_OF = {
        "import_to_google_doc": [
            {"required": ["content"]},
            {"required": ["file_path"]},
            {"required": ["file_url"]},
            {"required": ["base64_content"]},
        ],
        "import_to_google_sheets": [
            {"required": ["content"]},
            {"required": ["file_path"]},
            {"required": ["file_url"]},
            {"required": ["base64_content"]},
        ],
        "import_to_google_slides": [
            {"required": ["file_path"]},
            {"required": ["file_url"]},
            {"required": ["base64_content"]},
        ],
    }
    EXPECTED_REMOTE_ONE_OF = {
        "import_to_google_doc": [
            {"required": ["content"]},
            {"required": ["file_url"]},
            {"required": ["base64_content"]},
            {"required": ["return_upload_url", "source_format"]},
        ],
        "import_to_google_sheets": [
            {"required": ["content"]},
            {"required": ["file_url"]},
            {"required": ["base64_content"]},
            {"required": ["return_upload_url", "source_format"]},
        ],
        "import_to_google_slides": [
            {"required": ["file_url"]},
            {"required": ["base64_content"]},
            {"required": ["return_upload_url", "source_format"]},
        ],
    }
