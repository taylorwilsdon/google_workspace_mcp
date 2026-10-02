"""
Unit tests for Google Apps Script MCP tools

Tests all Apps Script tools with mocked API responses
"""

import asyncio
import inspect
import json
import os
import shutil
import subprocess
import sys
import threading
from typing import get_type_hints
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

from googleapiclient.discovery import build_from_document
from googleapiclient.http import HttpMock
from pydantic import TypeAdapter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
import auth.service_decorator as service_decorator

# Import the internal implementation functions (not the decorated ones)
from gappsscript.apps_script_tools import (
    _list_script_projects_impl,
    _get_script_project_impl,
    _create_script_project_impl,
    _update_script_content_impl,
    _merge_script_files,
    _run_script_function_impl,
    _resolve_execution_deployment_id,
    _create_deployment_impl,
    _list_deployments_impl,
    _update_deployment_impl,
    _delete_deployment_impl,
    _list_script_processes_impl,
    _delete_script_project_impl,
    _list_versions_impl,
    _create_version_impl,
    _get_version_impl,
    _get_script_metrics_impl,
    _generate_trigger_code_impl,
    _list_script_triggers_impl,
    _delete_script_trigger_impl,
    _ensure_trigger_admin_file,
    _require_project_action_service,
    _TRIGGER_ADMIN_FILE_NAME,
    _TRIGGER_ADMIN_MARKER,
    _TRIGGER_ADMIN_SOURCE,
    manage_deployment,
    get_script_project,
    list_script_deployments,
    manage_script_project,
    manage_script_content,
    get_script_version,
    manage_script_version,
    manage_script_trigger,
    get_script_activity,
    run_script_function,
)


def _parameters_adapter():
    hint = get_type_hints(run_script_function, include_extras=True)["parameters"]
    return TypeAdapter(hint)


def test_run_script_function_parameters_coerces_json_string():
    """A JSON-encoded array string is parsed into a real list."""
    assert _parameters_adapter().validate_python('["PrestaShop"]') == ["PrestaShop"]


def test_run_script_function_parameters_accepts_native_list():
    """Native lists (including heterogeneous items) pass through unchanged."""
    payload = ["PrestaShop", 1, {"a": 2}]
    assert _parameters_adapter().validate_python(payload) == payload


def test_run_script_function_parameters_accepts_none():
    """``parameters`` is optional."""
    assert _parameters_adapter().validate_python(None) is None


@pytest.mark.asyncio
async def test_list_script_projects():
    """Test listing Apps Script projects via Drive API"""
    mock_service = Mock()
    mock_response = {
        "files": [
            {
                "id": "test123",
                "name": "Test Project",
                "createdTime": "2025-01-10T10:00:00Z",
                "modifiedTime": "2026-01-12T15:30:00Z",
            },
        ]
    }

    mock_service.files().list().execute.return_value = mock_response

    result = await _list_script_projects_impl(
        service=mock_service, user_google_email="test@example.com", page_size=50
    )

    assert "Found 1 Apps Script projects" in result
    assert "Test Project" in result
    assert "test123" in result


@pytest.mark.asyncio
async def test_get_script_project():
    """Test retrieving complete project details"""
    mock_service = Mock()

    # projects().get() returns metadata only (no files)
    mock_metadata_response = {
        "scriptId": "test123",
        "title": "Test Project",
        "creator": {"email": "creator@example.com"},
        "createTime": "2025-01-10T10:00:00Z",
        "updateTime": "2026-01-12T15:30:00Z",
    }

    # projects().getContent() returns files with source code
    mock_content_response = {
        "scriptId": "test123",
        "files": [
            {
                "name": "Code",
                "type": "SERVER_JS",
                "source": "function test() { return 'hello'; }",
            }
        ],
    }

    mock_service.projects().get().execute.return_value = mock_metadata_response
    mock_service.projects().getContent().execute.return_value = mock_content_response

    result = await _get_script_project_impl(
        service=mock_service, user_google_email="test@example.com", script_id="test123"
    )

    assert "Test Project" in result
    assert "creator@example.com" in result
    assert "Code" in result


@pytest.mark.asyncio
async def test_create_script_project():
    """Test creating new Apps Script project"""
    mock_service = Mock()
    mock_response = {"scriptId": "new123", "title": "New Project"}

    mock_service.projects().create().execute.return_value = mock_response

    result = await _create_script_project_impl(
        service=mock_service, user_google_email="test@example.com", title="New Project"
    )

    assert "Script ID: new123" in result
    assert "New Project" in result


@pytest.mark.asyncio
async def test_update_script_content():
    """Test updating script project files with merge disabled."""
    mock_service = Mock()
    files_to_update = [
        {"name": "Code", "type": "SERVER_JS", "source": "function main() {}"}
    ]
    mock_response = {"files": files_to_update}

    mock_service.projects().updateContent().execute.return_value = mock_response

    result = await _update_script_content_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        files=files_to_update,
        merge=False,
    )

    assert "Updated script project: test123" in result
    assert "replaced entire project" in result
    assert "Code" in result
    mock_service.projects().getContent.assert_not_called()


def test_merge_script_files_overlays_updates_and_preserves_existing():
    existing = [
        {"name": "Code", "type": "SERVER_JS", "source": "old code"},
        {"name": "appsscript", "type": "JSON", "source": "{}"},
    ]
    updates = [{"name": "Code", "type": "SERVER_JS", "source": "new code"}]

    merged = _merge_script_files(existing, updates)

    assert len(merged) == 2
    by_name = {file["name"]: file for file in merged}
    assert by_name["Code"]["source"] == "new code"
    assert by_name["appsscript"]["source"] == "{}"


def test_merge_script_files_adds_new_file():
    existing = [{"name": "Code", "type": "SERVER_JS", "source": "code"}]
    updates = [{"name": "Utils", "type": "SERVER_JS", "source": "function util() {}"}]

    merged = _merge_script_files(existing, updates)

    assert len(merged) == 2
    by_name = {file["name"]: file for file in merged}
    assert by_name["Utils"]["source"] == "function util() {}"
    assert by_name["Code"]["source"] == "code"


def test_merge_script_files_rejects_update_without_name():
    existing = [{"name": "Code", "type": "SERVER_JS", "source": "code"}]

    with pytest.raises(UserInputError, match="index 0.*non-empty 'name'"):
        _merge_script_files(existing, [{"type": "SERVER_JS", "source": "new code"}])


def test_merge_script_files_keeps_existing_fields_when_omitted():
    existing = [{"name": "Code", "type": "SERVER_JS", "source": "code"}]
    updates = [{"name": "Code", "source": "new code"}]

    merged = _merge_script_files(existing, updates)

    assert merged == [{"name": "Code", "type": "SERVER_JS", "source": "new code"}]


def test_merge_script_files_keeps_existing_type_when_update_type_is_none():
    existing = [{"name": "Code", "type": "SERVER_JS", "source": "code"}]
    updates = [{"name": "Code", "type": None, "source": "new code"}]

    merged = _merge_script_files(existing, updates)

    assert merged == [{"name": "Code", "type": "SERVER_JS", "source": "new code"}]


@pytest.mark.parametrize("file_type", ["TEXT", "", 123])
def test_merge_script_files_rejects_unsupported_explicit_type(file_type):
    with pytest.raises(UserInputError, match="unsupported type"):
        _merge_script_files(
            [],
            [{"name": "Code", "type": file_type, "source": "source"}],
        )


def test_merge_script_files_rejects_json_file_without_manifest_name():
    with pytest.raises(UserInputError, match="manifest name 'appsscript'"):
        _merge_script_files(
            [],
            [{"name": "config", "type": "JSON", "source": "{}"}],
        )


def test_merge_script_files_keeps_same_name_different_type():
    """Script API names exclude extensions, so Code.gs and Code.html collide."""
    existing = [
        {"name": "Code", "type": "SERVER_JS", "source": "server"},
        {"name": "Code", "type": "HTML", "source": "<html></html>"},
    ]
    updates = [{"name": "Code", "type": "SERVER_JS", "source": "new server"}]

    merged = _merge_script_files(existing, updates)

    assert merged == [
        {"name": "Code", "type": "SERVER_JS", "source": "new server"},
        {"name": "Code", "type": "HTML", "source": "<html></html>"},
    ]


def test_merge_script_files_does_not_guess_when_name_is_ambiguous():
    """An update without a type must not clobber one of two same-name files."""
    existing = [
        {"name": "Code", "type": "SERVER_JS", "source": "server"},
        {"name": "Code", "type": "HTML", "source": "<html></html>"},
    ]
    updates = [{"name": "Code", "source": "new source"}]

    with pytest.raises(UserInputError, match="missing 'type'"):
        _merge_script_files(existing, updates)


def test_merge_script_files_requires_type_for_new_file():
    existing = [{"name": "Code", "type": "SERVER_JS", "source": "server"}]
    updates = [{"name": "Utils", "source": "function util() {}"}]

    with pytest.raises(UserInputError, match="missing 'type'"):
        _merge_script_files(existing, updates)


@pytest.mark.asyncio
async def test_update_script_content_merge_fetches_existing_files():
    """Test the default merge mode overlays updates onto the current project."""
    mock_service = Mock()
    existing_files = [
        {"name": "Code", "type": "SERVER_JS", "source": "old code"},
        {"name": "appsscript", "type": "JSON", "source": "{}"},
    ]
    files_to_update = [{"name": "Code", "type": "SERVER_JS", "source": "new code"}]
    merged_files = _merge_script_files(existing_files, files_to_update)

    mock_service.projects().getContent().execute.return_value = {
        "files": existing_files
    }
    mock_service.projects().updateContent().execute.return_value = {
        "files": merged_files
    }

    result = await _update_script_content_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        files=files_to_update,
    )

    update_body = mock_service.projects().updateContent.call_args.kwargs["body"]
    assert update_body == {"files": merged_files}
    assert "merged into project" in result
    assert "Code" in result
    assert "appsscript" in result


@pytest.mark.asyncio
async def test_update_script_content_orders_concurrent_merges_for_same_script():
    """A later merge must fetch content after an earlier update completes."""
    content = [{"name": "Base", "type": "SERVER_JS", "source": "base"}]
    first_update_started = threading.Event()
    allow_first_update = threading.Event()
    first_update_applied = threading.Event()
    second_get_started = threading.Event()
    update_count = 0
    get_count = 0

    class Request:
        def __init__(self, execute):
            self._execute = execute

        def execute(self):
            return self._execute()

    class Projects:
        def getContent(self, scriptId):
            nonlocal get_count
            get_count += 1
            if get_count == 2:
                second_get_started.set()
            snapshot = [file.copy() for file in content]
            return Request(lambda: {"files": snapshot})

        def updateContent(self, scriptId, body):
            nonlocal update_count
            update_count += 1
            update_number = update_count

            def execute():
                nonlocal content
                if update_number == 1:
                    first_update_started.set()
                    assert allow_first_update.wait(timeout=1)
                else:
                    assert first_update_applied.wait(timeout=1)
                content = [file.copy() for file in body["files"]]
                if update_number == 1:
                    first_update_applied.set()
                return {"files": content}

            return Request(execute)

    projects = Projects()
    service = Mock()
    service.projects.side_effect = lambda: projects

    first_update = asyncio.create_task(
        _update_script_content_impl(
            service=service,
            user_google_email="test@example.com",
            script_id="test123",
            files=[{"name": "First", "type": "SERVER_JS", "source": "first"}],
        )
    )
    assert await asyncio.to_thread(first_update_started.wait, 1)

    second_update = asyncio.create_task(
        _update_script_content_impl(
            service=service,
            user_google_email="test@example.com",
            script_id="test123",
            files=[{"name": "Second", "type": "SERVER_JS", "source": "second"}],
        )
    )
    await asyncio.sleep(0)
    second_get_raced = second_get_started.is_set()
    allow_first_update.set()
    await asyncio.gather(first_update, second_update)

    assert not second_get_raced
    assert {file["name"] for file in content} == {"Base", "First", "Second"}


@pytest.mark.asyncio
async def test_run_script_function():
    """Test executing script function"""
    mock_service = Mock()
    mock_response = {"response": {"result": "Success"}}

    mock_service.projects().deployments().list().execute.return_value = {
        "deployments": [
            {
                "deploymentId": "deploy123",
                "deploymentConfig": {"versionNumber": 1},
                "entryPoints": [{"entryPointType": "EXECUTION_API"}],
            }
        ]
    }
    mock_service.scripts().run.return_value.execute.return_value = mock_response

    result = await _run_script_function_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        function_name="myFunction",
        dev_mode=True,
    )

    assert "Execution successful" in result
    assert "myFunction" in result
    mock_service.scripts().run.assert_called_once_with(
        scriptId="deploy123",
        body={"function": "myFunction", "devMode": True},
    )


@pytest.mark.asyncio
async def test_run_script_function_uses_only_api_executable_deployment():
    """HEAD and non-executable deployments are skipped."""
    mock_service = Mock()
    mock_service.projects().deployments().list().execute.return_value = {
        "deployments": [
            {"deploymentId": "head", "deploymentConfig": {}},
            {
                "deploymentId": "web-app",
                "deploymentConfig": {"versionNumber": 2},
                "entryPoints": [{"entryPointType": "WEB_APP"}],
            },
            {
                "deploymentId": "api-executable",
                "deploymentConfig": {"versionNumber": 5},
                "entryPoints": [{"entryPointType": "EXECUTION_API"}],
            },
        ]
    }
    mock_service.scripts().run.return_value.execute.return_value = {
        "response": {"result": "Success"}
    }

    await _run_script_function_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        function_name="myFunction",
    )

    mock_service.scripts().run.assert_called_once_with(
        scriptId="api-executable",
        body={"function": "myFunction", "devMode": False},
    )


@pytest.mark.asyncio
async def test_run_script_function_selects_highest_version_deployment():
    """Automatic discovery picks the API deployment with the highest version."""
    mock_service = Mock()
    mock_service.projects().deployments().list().execute.return_value = {
        "deployments": [
            {
                "deploymentId": "older",
                "deploymentConfig": {"versionNumber": 4},
                "entryPoints": [{"entryPointType": "EXECUTION_API"}],
            },
            {
                "deploymentId": "newest",
                "deploymentConfig": {"versionNumber": 7},
                "entryPoints": [{"entryPointType": "EXECUTION_API"}],
            },
            {
                "deploymentId": "middle",
                "deploymentConfig": {"versionNumber": 5},
                "entryPoints": [{"entryPointType": "EXECUTION_API"}],
            },
        ]
    }
    mock_service.scripts().run.return_value.execute.return_value = {
        "response": {"result": "Success"}
    }

    await _run_script_function_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        function_name="myFunction",
    )

    mock_service.scripts().run.assert_called_once_with(
        scriptId="newest",
        body={"function": "myFunction", "devMode": False},
    )


@pytest.mark.asyncio
async def test_run_script_function_searches_all_deployment_pages():
    """A runnable deployment on a later page is discovered."""
    mock_service = Mock()
    deployment_list = mock_service.projects().deployments().list
    deployment_list.return_value.execute.side_effect = [
        {
            "deployments": [
                {
                    "deploymentId": "web-app",
                    "deploymentConfig": {"versionNumber": 8},
                    "entryPoints": [{"entryPointType": "WEB_APP"}],
                }
            ],
            "nextPageToken": "page-2",
        },
        {
            "deployments": [
                {
                    "deploymentId": "api-executable",
                    "deploymentConfig": {"versionNumber": 3},
                    "entryPoints": [{"entryPointType": "EXECUTION_API"}],
                }
            ]
        },
    ]
    mock_service.scripts().run.return_value.execute.return_value = {
        "response": {"result": "Success"}
    }

    await _run_script_function_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        function_name="myFunction",
    )

    assert deployment_list.call_args_list == [
        call(scriptId="test123"),
        call(scriptId="test123", pageToken="page-2"),
    ]
    mock_service.scripts().run.assert_called_once_with(
        scriptId="api-executable",
        body={"function": "myFunction", "devMode": False},
    )


@pytest.mark.asyncio
async def test_run_script_function_uses_supplied_deployment_without_lookup():
    """A supplied deployment ID bypasses deployment discovery."""
    mock_service = Mock()
    mock_service.scripts().run.return_value.execute.return_value = {
        "response": {"result": "Success"}
    }

    await _run_script_function_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        function_name="myFunction",
        deployment_id="deploy-known",
    )

    mock_service.projects().deployments().list.assert_not_called()
    mock_service.scripts().run.assert_called_once_with(
        scriptId="deploy-known",
        body={"function": "myFunction", "devMode": False},
    )


@pytest.mark.asyncio
async def test_run_script_function_requires_api_executable_deployment():
    """Execution is not attempted without a versioned API executable."""
    mock_service = Mock()
    mock_service.projects().deployments().list().execute.return_value = {
        "deployments": [
            {"deploymentId": "head", "deploymentConfig": {}},
            {
                "deploymentId": "web-app",
                "deploymentConfig": {"versionNumber": 1},
                "entryPoints": [{"entryPointType": "WEB_APP"}],
            },
        ]
    }

    result = await _run_script_function_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        function_name="myFunction",
    )

    assert "No versioned API Executable deployment was found" in result
    assert "Deploy > New deployment > API Executable" in result
    assert "manifest already defines executionApi" in result
    mock_service.scripts().run.assert_not_called()


@pytest.mark.asyncio
async def test_create_deployment():
    """Test creating deployment"""
    mock_service = Mock()

    # Mock version creation (called first)
    mock_version_response = {"versionNumber": 1}
    mock_service.projects().versions().create().execute.return_value = (
        mock_version_response
    )

    # Mock deployment creation (called second)
    mock_deploy_response = {
        "deploymentId": "deploy123",
        "deploymentConfig": {},
    }
    mock_service.projects().deployments().create().execute.return_value = (
        mock_deploy_response
    )

    result = await _create_deployment_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        description="Test deployment",
    )

    assert "Deployment ID: deploy123" in result
    assert "Test deployment" in result
    assert "Version: 1" in result


@pytest.mark.asyncio
async def test_list_deployments():
    """Listing surfaces the bound version and description from deploymentConfig (issue #922)."""
    mock_service = Mock()
    # The real API nests description and versionNumber under deploymentConfig.
    mock_response = {
        "deployments": [
            {
                "deploymentId": "deploy123",
                "deploymentConfig": {
                    "scriptId": "test123",
                    "versionNumber": 7,
                    "description": "Production",
                },
                "updateTime": "2026-01-12T15:30:00Z",
            }
        ]
    }

    mock_service.projects().deployments().list().execute.return_value = mock_response

    result = await _list_deployments_impl(
        service=mock_service, user_google_email="test@example.com", script_id="test123"
    )

    assert "Production" in result
    assert "deploy123" in result
    # Version must be visible so callers can verify which version is served.
    assert "Version: 7" in result


@pytest.mark.asyncio
async def test_list_deployments_head_deployment_has_no_version():
    """A HEAD deployment (no versionNumber) is labelled rather than shown blank (issue #922)."""
    mock_service = Mock()
    mock_response = {
        "deployments": [
            {
                "deploymentId": "head123",
                "deploymentConfig": {"scriptId": "test123"},
                "updateTime": "2026-01-12T15:30:00Z",
            }
        ]
    }

    mock_service.projects().deployments().list().execute.return_value = mock_response

    result = await _list_deployments_impl(
        service=mock_service, user_google_email="test@example.com", script_id="test123"
    )

    assert "head123" in result
    assert "HEAD (latest)" in result


@pytest.mark.asyncio
async def test_update_deployment():
    """Test updating deployment wraps fields in deploymentConfig (issue #836)."""
    mock_service = Mock()
    mock_response = {
        "deploymentId": "deploy123",
        "deploymentConfig": {
            "scriptId": "test123",
            "description": "Updated description",
        },
    }

    mock_service.projects().deployments().update().execute.return_value = mock_response

    result = await _update_deployment_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        deployment_id="deploy123",
        description="Updated description",
    )

    # The Apps Script API rejects a flat body; fields must live under
    # ``deploymentConfig`` and the config must always carry ``scriptId``.
    _, update_kwargs = mock_service.projects().deployments().update.call_args
    assert update_kwargs["scriptId"] == "test123"
    assert update_kwargs["deploymentId"] == "deploy123"
    assert update_kwargs["body"] == {
        "deploymentConfig": {
            "scriptId": "test123",
            "description": "Updated description",
        }
    }
    assert "description" not in update_kwargs["body"]

    assert "Updated deployment: deploy123" in result
    assert "Updated description" in result


@pytest.mark.asyncio
async def test_update_deployment_with_version_number():
    """Updating with a version_number repoints the deployment (issue #836)."""
    mock_service = Mock()
    mock_response = {
        "deploymentId": "deploy123",
        "deploymentConfig": {
            "scriptId": "test123",
            "versionNumber": 2,
            "description": "v2 - updated layout",
        },
    }

    mock_service.projects().deployments().update().execute.return_value = mock_response

    result = await _update_deployment_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        deployment_id="deploy123",
        description="v2 - updated layout",
        version_number=2,
    )

    _, update_kwargs = mock_service.projects().deployments().update.call_args
    assert update_kwargs["body"] == {
        "deploymentConfig": {
            "scriptId": "test123",
            "versionNumber": 2,
            "description": "v2 - updated layout",
        }
    }

    assert "Version: 2" in result
    assert "v2 - updated layout" in result


@pytest.mark.asyncio
async def test_manage_deployment_update_allows_version_number_without_description():
    """The public update branch allows roll-forward updates without a description."""
    mock_service = Mock()
    mock_response = {
        "deploymentId": "deploy123",
        "deploymentConfig": {
            "scriptId": "test123",
            "versionNumber": 2,
        },
    }
    mock_service.projects().deployments().update().execute.return_value = mock_response

    undecorated_manage_deployment = manage_deployment.__wrapped__.__wrapped__
    result = await undecorated_manage_deployment(
        service=mock_service,
        user_google_email="test@example.com",
        action="update",
        script_id="test123",
        deployment_id="deploy123",
        version_number=2,
    )

    _, update_kwargs = mock_service.projects().deployments().update.call_args
    assert update_kwargs["body"] == {
        "deploymentConfig": {
            "scriptId": "test123",
            "versionNumber": 2,
        }
    }
    assert "Version: 2" in result


@pytest.mark.asyncio
async def test_delete_deployment():
    """Test deleting deployment"""
    mock_service = Mock()
    mock_service.projects().deployments().delete().execute.return_value = {}

    result = await _delete_deployment_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        deployment_id="deploy123",
    )

    assert "Deleted deployment: deploy123 from script: test123" in result


@pytest.mark.asyncio
async def test_list_script_processes():
    """Test listing script processes"""
    mock_service = Mock()
    mock_response = {
        "processes": [
            {
                "functionName": "myFunction",
                "processStatus": "COMPLETED",
                "startTime": "2026-01-12T15:30:00Z",
                "duration": "5s",
            }
        ]
    }

    mock_service.processes().list().execute.return_value = mock_response

    result = await _list_script_processes_impl(
        service=mock_service, user_google_email="test@example.com", page_size=50
    )

    assert "myFunction" in result
    assert "COMPLETED" in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("script_id", "expected_path", "expected_query"),
    [
        (None, "/v1/processes?", "pageSize=25"),
        ("abc123", "/v1/processes:listScriptProcesses?", "scriptId=abc123&pageSize=25"),
    ],
)
async def test_list_script_processes_against_discovery_schema(
    script_id, expected_path, expected_query
):
    """Build the service from the real Script API v1 discovery document so an
    invalid kwarg raises TypeError here, which a Mock service would accept."""
    fixture_path = os.path.join(
        os.path.dirname(__file__), "fixtures", "script_discovery_v1.json"
    )
    with open(fixture_path, encoding="utf-8") as f:
        discovery_doc = json.load(f)
    http = HttpMock()
    http.data = b'{"processes": []}'
    service = build_from_document(discovery_doc, http=http)

    result = await _list_script_processes_impl(
        service=service,
        user_google_email="test@example.com",
        page_size=25,
        script_id=script_id,
    )

    assert expected_path in http.uri
    assert expected_query in http.uri
    assert result == "No recent script executions found."


@pytest.mark.asyncio
async def test_delete_script_project():
    """Test deleting a script project"""
    mock_service = Mock()
    mock_service.files().delete().execute.return_value = {}

    result = await _delete_script_project_impl(
        service=mock_service, user_google_email="test@example.com", script_id="test123"
    )

    assert "Deleted Apps Script project: test123" in result


@pytest.mark.asyncio
async def test_list_versions():
    """Test listing script versions"""
    mock_service = Mock()
    mock_response = {
        "versions": [
            {
                "versionNumber": 1,
                "description": "Initial version",
                "createTime": "2025-01-10T10:00:00Z",
            },
            {
                "versionNumber": 2,
                "description": "Bug fix",
                "createTime": "2026-01-12T15:30:00Z",
            },
        ]
    }

    mock_service.projects().versions().list().execute.return_value = mock_response

    result = await _list_versions_impl(
        service=mock_service, user_google_email="test@example.com", script_id="test123"
    )

    assert "Version 1" in result
    assert "Initial version" in result
    assert "Version 2" in result
    assert "Bug fix" in result


@pytest.mark.asyncio
async def test_create_version():
    """Test creating a new version"""
    mock_service = Mock()
    mock_response = {
        "versionNumber": 3,
        "createTime": "2026-01-13T10:00:00Z",
    }

    mock_service.projects().versions().create().execute.return_value = mock_response

    result = await _create_version_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        description="New feature",
    )

    assert "Created version 3" in result
    assert "New feature" in result


@pytest.mark.asyncio
async def test_get_version():
    """Test getting a specific version"""
    mock_service = Mock()
    mock_response = {
        "versionNumber": 2,
        "description": "Bug fix",
        "createTime": "2026-01-12T15:30:00Z",
    }

    mock_service.projects().versions().get().execute.return_value = mock_response

    result = await _get_version_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        version_number=2,
    )

    assert "Version 2" in result
    assert "Bug fix" in result


@pytest.mark.asyncio
async def test_get_script_metrics():
    """Test getting script metrics"""
    mock_service = Mock()
    mock_response = {
        "activeUsers": [
            {"startTime": "2026-01-01", "endTime": "2026-01-02", "value": "10"}
        ],
        "totalExecutions": [
            {"startTime": "2026-01-01", "endTime": "2026-01-02", "value": "100"}
        ],
        "failedExecutions": [
            {"startTime": "2026-01-01", "endTime": "2026-01-02", "value": "5"}
        ],
    }

    mock_service.projects().getMetrics().execute.return_value = mock_response

    result = await _get_script_metrics_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        metrics_granularity="DAILY",
    )

    assert "Active Users" in result
    assert "10 users" in result
    assert "Total Executions" in result
    assert "100 executions" in result
    assert "Failed Executions" in result
    assert "5 failures" in result


def test_generate_trigger_code_daily():
    """Test generating daily trigger code"""
    result = _generate_trigger_code_impl(
        trigger_type="time_daily",
        function_name="sendReport",
        schedule="9",
    )

    assert "INSTALLABLE TRIGGER" in result
    assert "createDailyTrigger_sendReport" in result
    assert "everyDays(1)" in result
    assert "atHour(9)" in result


def test_generate_trigger_code_on_edit():
    """Test generating onEdit trigger code"""
    result = _generate_trigger_code_impl(
        trigger_type="on_edit",
        function_name="processEdit",
    )

    assert "SIMPLE TRIGGER" in result
    assert "function onEdit" in result
    assert "processEdit()" in result


def test_generate_trigger_code_invalid():
    """Test generating trigger code with invalid type"""
    result = _generate_trigger_code_impl(
        trigger_type="invalid_type",
        function_name="test",
    )

    assert "Unknown trigger type" in result
    assert "Valid types:" in result


# ---------------------------------------------------------------------------
# Trigger management (list_script_triggers / delete_script_trigger)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ensure_trigger_admin_file_injects_when_missing():
    """The admin file is appended, existing files are left untouched."""
    mock_service = Mock()
    existing_files = [
        {"name": "Code", "type": "SERVER_JS", "source": "function foo(){}"}
    ]
    mock_service.projects().getContent().execute.return_value = {
        "files": existing_files
    }

    await _ensure_trigger_admin_file(mock_service, "script123")

    _, call_kwargs = mock_service.projects().updateContent.call_args
    written_files = call_kwargs["body"]["files"]
    names = {f["name"] for f in written_files}
    assert names == {"Code", _TRIGGER_ADMIN_FILE_NAME}
    # The original file's source must be untouched.
    original = next(f for f in written_files if f["name"] == "Code")
    assert original["source"] == "function foo(){}"
    admin = next(f for f in written_files if f["name"] == _TRIGGER_ADMIN_FILE_NAME)
    assert admin["source"] == _TRIGGER_ADMIN_SOURCE


@pytest.mark.asyncio
async def test_ensure_trigger_admin_file_noop_when_current():
    """No write happens when the admin file already matches."""
    mock_service = Mock()
    mock_service.projects().getContent().execute.return_value = {
        "files": [
            {
                "name": _TRIGGER_ADMIN_FILE_NAME,
                "type": "SERVER_JS",
                "source": _TRIGGER_ADMIN_SOURCE,
            }
        ]
    }

    await _ensure_trigger_admin_file(mock_service, "script123")

    mock_service.projects().updateContent.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_trigger_admin_file_rejects_user_file_collision():
    """A user-managed file with the reserved name must never be overwritten."""
    mock_service = Mock()
    mock_service.projects().getContent().execute.return_value = {
        "files": [
            {
                "name": _TRIGGER_ADMIN_FILE_NAME,
                "type": "SERVER_JS",
                "source": "function userCode() {}",
            }
        ]
    }

    with pytest.raises(UserInputError, match="user-managed"):
        await _ensure_trigger_admin_file(mock_service, "script123")

    mock_service.projects().updateContent.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_trigger_admin_file_refreshes_stale_helper():
    """A helper written by an older server version is overwritten, not rejected."""
    mock_service = Mock()
    mock_service.projects().getContent().execute.return_value = {
        "files": [
            {
                "name": _TRIGGER_ADMIN_FILE_NAME,
                "type": "SERVER_JS",
                "source": _TRIGGER_ADMIN_MARKER + "\nfunction __mcpOld() {}",
            }
        ]
    }

    await _ensure_trigger_admin_file(mock_service, "script123")

    _, call_kwargs = mock_service.projects().updateContent.call_args
    assert call_kwargs["body"]["files"] == [
        {
            "name": _TRIGGER_ADMIN_FILE_NAME,
            "type": "SERVER_JS",
            "source": _TRIGGER_ADMIN_SOURCE,
        }
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize(
    "trigger_id, handler_function, expected_ids",
    [
        ("1", None, ["1"]),
        (None, "sendReport", ["1", "2"]),
        ("1", "sendReport", ["1"]),
        ("3", "sendReport", []),
        (None, None, []),
    ],
)
def test_trigger_admin_delete_requires_every_selector_to_match(
    trigger_id, handler_function, expected_ids
):
    """Run the helper JS against a fake ScriptApp to check selector semantics."""
    harness = f"""
    var store = [["1", "sendReport"], ["2", "sendReport"], ["3", "cleanup"]].map(
      function (p) {{
        return {{getUniqueId: () => p[0], getHandlerFunction: () => p[1]}};
      }});
    var ScriptApp = {{
      getProjectTriggers: () => store.slice(),
      deleteTrigger: (t) => {{ store = store.filter((x) => x !== t); }}
    }};
    {_TRIGGER_ADMIN_SOURCE}
    console.log(__mcpDeleteTrigger({json.dumps(trigger_id)}, {json.dumps(handler_function)}));
    """
    result = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, check=True
    )

    deleted = json.loads(result.stdout)
    assert [trigger["uniqueId"] for trigger in deleted] == expected_ids


@pytest.mark.asyncio
async def test_resolve_execution_deployment_id_uses_latest_api_executable():
    mock_service = Mock()
    mock_service.projects().deployments().list().execute.return_value = {
        "deployments": [
            {
                "deploymentId": "web",
                "deploymentConfig": {"versionNumber": 9},
                "entryPoints": [{"entryPointType": "WEB_APP"}],
            },
            {
                "deploymentId": "exec-2",
                "deploymentConfig": {"versionNumber": 2},
                "entryPoints": [{"entryPointType": "EXECUTION_API"}],
            },
            {
                "deploymentId": "exec-4",
                "deploymentConfig": {"versionNumber": 4},
                "entryPoints": [{"entryPointType": "EXECUTION_API"}],
            },
        ]
    }

    assert await _resolve_execution_deployment_id(mock_service, "script123") == "exec-4"


@pytest.mark.asyncio
async def test_list_script_triggers():
    """Test listing triggers on a script project"""
    mock_service = Mock()
    mock_service.projects().getContent().execute.return_value = {"files": []}
    mock_service.scripts().run().execute.return_value = {
        "response": {
            "result": json.dumps(
                [
                    {
                        "uniqueId": "abc123",
                        "handlerFunction": "sendDailyReport",
                        "eventType": "CLOCK",
                        "triggerSource": "CLOCK",
                    }
                ]
            )
        }
    }

    result = await _list_script_triggers_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="script123",
        deployment_id="deployment123",
    )

    assert "sendDailyReport" in result
    assert "abc123" in result

    # Must have provisioned the admin file before running it.
    mock_service.projects().updateContent.assert_called_once()
    _, run_kwargs = mock_service.scripts().run.call_args
    assert run_kwargs["scriptId"] == "deployment123"
    assert run_kwargs["body"]["function"] == "__mcpListTriggers"


@pytest.mark.asyncio
async def test_list_script_triggers_none_found():
    """Test listing triggers when none exist"""
    mock_service = Mock()
    mock_service.projects().getContent().execute.return_value = {"files": []}
    mock_service.scripts().run().execute.return_value = {"response": {"result": "[]"}}

    result = await _list_script_triggers_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="script123",
        deployment_id="deployment123",
    )

    assert "No triggers found" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("helper_present", [False, True])
async def test_list_triggers_checks_deployed_version(automatic, helper_present):
    service = Mock()
    deployment = {
        "deploymentId": "deployment123",
        "deploymentConfig": {"versionNumber": 7},
        "entryPoints": [{"entryPointType": "EXECUTION_API"}],
    }
    service.projects().deployments().list().execute.return_value = {
        "deployments": [deployment]
    }
    service.projects().deployments().get().execute.return_value = deployment
    # Even source mentioning the helper must not count as an executable function.
    service.projects().getContent().execute.return_value = {
        "files": [
            {
                "type": "SERVER_JS",
                "source": "// function __mcpListTriggers() {}",
                "functionSet": {
                    "values": (
                        [{"name": "__mcpListTriggers"}] if helper_present else []
                    )
                },
            }
        ]
    }
    service.scripts().run().execute.return_value = {"response": {"result": "[]"}}
    service.reset_mock()

    kwargs = dict(
        service=service,
        user_google_email="u@e.com",
        script_id="script123",
        dev_mode=False,
        deployment_id=None if automatic else "deployment123",
    )
    if helper_present:
        assert "No triggers found" in await _list_script_triggers_impl(**kwargs)
        service.scripts().run.assert_called_once_with(
            scriptId="deployment123",
            body={"function": "__mcpListTriggers", "devMode": False},
        )
    else:
        with pytest.raises(UserInputError, match="does not contain __mcpListTriggers"):
            await _list_script_triggers_impl(**kwargs)
        service.scripts().run.assert_not_called()

    service.projects().deployments().get.assert_called_once_with(
        scriptId="script123", deploymentId="deployment123"
    )
    service.projects().getContent.assert_called_once_with(
        scriptId="script123", versionNumber=7
    )
    service.projects().updateContent.assert_not_called()
    service.projects().deployments().update.assert_not_called()


@pytest.mark.asyncio
async def test_list_triggers_rejects_unversioned_deployment():
    service = Mock()
    service.projects().deployments().get().execute.return_value = {
        "deploymentConfig": {}
    }
    with pytest.raises(UserInputError, match="must reference a script version"):
        await _list_script_triggers_impl(
            service, "u@e.com", "script123", False, "deployment123"
        )
    service.projects().getContent.assert_not_called()
    service.projects().updateContent.assert_not_called()
    service.scripts.assert_not_called()


@pytest.mark.asyncio
async def test_list_script_triggers_requires_deployment_before_writing_helper():
    """A missing API Executable deployment must not mutate the project."""
    mock_service = Mock()
    mock_service.projects().deployments().list().execute.return_value = {
        "deployments": []
    }

    with pytest.raises(UserInputError, match="API Executable deployment"):
        await _list_script_triggers_impl(
            service=mock_service,
            user_google_email="test@example.com",
            script_id="script123",
        )

    mock_service.projects().getContent.assert_not_called()
    mock_service.projects().updateContent.assert_not_called()


@pytest.mark.asyncio
async def test_delete_script_trigger_requires_a_selector():
    """Must supply trigger_id or handler_function."""
    mock_service = Mock()
    with pytest.raises(UserInputError):
        await _delete_script_trigger_impl(
            service=mock_service,
            user_google_email="test@example.com",
            script_id="script123",
        )


@pytest.mark.asyncio
async def test_delete_script_trigger_by_id():
    """Test deleting a trigger by unique ID"""
    mock_service = Mock()
    mock_service.projects().getContent().execute.return_value = {"files": []}
    mock_service.scripts().run().execute.return_value = {
        "response": {
            "result": json.dumps(
                [{"uniqueId": "abc123", "handlerFunction": "sendDailyReport"}]
            )
        }
    }

    result = await _delete_script_trigger_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="script123",
        trigger_id="abc123",
        deployment_id="deployment123",
    )

    assert "Deleted 1 trigger" in result
    assert "sendDailyReport" in result
    _, run_kwargs = mock_service.scripts().run.call_args
    assert run_kwargs["body"]["parameters"] == ["abc123", None]


@pytest.mark.asyncio
async def test_delete_script_trigger_no_match():
    """Test deleting a trigger that doesn't exist"""
    mock_service = Mock()
    mock_service.projects().getContent().execute.return_value = {"files": []}
    mock_service.scripts().run().execute.return_value = {"response": {"result": "[]"}}

    result = await _delete_script_trigger_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="script123",
        trigger_id="nope",
        deployment_id="deployment123",
    )

    assert "No matching trigger found" in result


@pytest.mark.asyncio
async def test_list_script_triggers_execution_error():
    """Test that a scripts.run() error surfaces as an exception."""
    mock_service = Mock()
    mock_service.projects().getContent().execute.return_value = {"files": []}
    mock_service.scripts().run().execute.return_value = {
        "error": {"message": "Script function not found: __mcpListTriggers"}
    }

    with pytest.raises(RuntimeError, match="Script function not found"):
        await _list_script_triggers_impl(
            service=mock_service,
            user_google_email="test@example.com",
            script_id="script123",
            deployment_id="deployment123",
        )


@pytest.mark.asyncio
async def test_delete_script_trigger_by_handler():
    """Deleting by handler_function passes it through and reports every match."""
    mock_service = Mock()
    mock_service.projects().getContent().execute.return_value = {"files": []}
    mock_service.scripts().run().execute.return_value = {
        "response": {
            "result": json.dumps(
                [
                    {"uniqueId": "abc123", "handlerFunction": "sendDailyReport"},
                    {"uniqueId": "def456", "handlerFunction": "sendDailyReport"},
                ]
            )
        }
    }

    result = await _delete_script_trigger_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="script123",
        handler_function="sendDailyReport",
        deployment_id="deployment123",
    )

    assert "Deleted 2 trigger" in result
    assert "abc123" in result and "def456" in result
    _, run_kwargs = mock_service.scripts().run.call_args
    assert run_kwargs["body"]["parameters"] == [None, "sendDailyReport"]


@pytest.mark.asyncio
async def test_delete_script_trigger_by_id_and_handler():
    """Both selectors are forwarded so the helper can require both to match."""
    mock_service = Mock()
    mock_service.projects().getContent().execute.return_value = {"files": []}
    mock_service.scripts().run().execute.return_value = {
        "response": {
            "result": json.dumps(
                [{"uniqueId": "abc123", "handlerFunction": "sendDailyReport"}]
            )
        }
    }

    result = await _delete_script_trigger_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="script123",
        trigger_id="abc123",
        handler_function="sendDailyReport",
        deployment_id="deployment123",
    )

    assert "Deleted 1 trigger" in result
    _, run_kwargs = mock_service.scripts().run.call_args
    assert run_kwargs["body"]["parameters"] == ["abc123", "sendDailyReport"]


# ============================================================================
# Consolidated tool dispatch (action routing + argument validation)
#
# The public @server.tool wrappers are thin action dispatchers over the _impl
# functions tested above. These tests unwrap the auth/error decorators and
# verify that each action routes to the right _impl (with the right service for
# multi-service tools) and that missing required arguments raise UserInputError.
# ============================================================================


def _undecorated(tool):
    """Strip the two auth/error decorators to reach the raw dispatcher."""
    return tool.__wrapped__.__wrapped__


@pytest.mark.asyncio
@pytest.mark.parametrize("managed_email", [False, True])
@pytest.mark.parametrize(
    "tool, action, service_type, scopes, impl_name, kwargs",
    [
        (
            get_script_project,
            " LIST ",
            "drive",
            "drive_read",
            "_list_script_projects_impl",
            {"page_size": 12, "page_token": "next"},
        ),
        (
            get_script_project,
            "get",
            "script",
            "script_readonly",
            "_get_script_project_impl",
            {"script_id": "s1"},
        ),
        (
            get_script_project,
            "get",
            "script",
            "script_readonly",
            "_get_script_content_impl",
            {"script_id": "s1", "file_name": "Code"},
        ),
        (
            manage_script_project,
            "create",
            "script",
            "script_projects",
            "_create_script_project_impl",
            {"title": "T", "parent_id": "p1"},
        ),
        (
            manage_script_project,
            " DELETE ",
            "drive",
            "drive_full",
            "_delete_script_project_impl",
            {"script_id": "s1"},
        ),
    ],
)
async def test_project_actions_authenticate_only_required_service(
    monkeypatch, managed_email, tool, action, service_type, scopes, impl_name, kwargs
):
    monkeypatch.setattr(
        service_decorator, "_user_email_is_managed", lambda: managed_email
    )
    monkeypatch.setattr(
        service_decorator,
        "_get_auth_context",
        AsyncMock(
            return_value=("u@e.com", "oauth21", "session")
            if managed_email
            else (None, None, None)
        ),
    )
    monkeypatch.setattr(
        service_decorator, "_detect_oauth_version", lambda *a: managed_email
    )
    service = Mock()
    authenticate = AsyncMock(return_value=(service, "u@e.com"))
    monkeypatch.setattr(service_decorator, "_authenticate_service", authenticate)
    mapping = (
        {"list": ("drive", "drive_read"), "get": ("script", "script_readonly")}
        if tool is get_script_project
        else {
            "create": ("script", "script_projects"),
            "delete": ("drive", "drive_full"),
        }
    )
    # Rebuild to exercise both signatures, which are fixed at decoration time.
    fn = _require_project_action_service(mapping)(tool.__wrapped__)
    signature = inspect.signature(fn)
    assert "drive_service" not in signature.parameters
    assert "script_service" not in signature.parameters
    assert ("user_google_email" not in signature.parameters) == managed_email
    with patch(
        f"gappsscript.apps_script_tools.{impl_name}", new=AsyncMock(return_value="ok")
    ) as impl:
        if managed_email:
            result = await fn(action=action, **kwargs)
        else:
            result = await fn("u@e.com", action, **kwargs)
    assert result == "ok"
    authenticate.assert_awaited_once()
    assert authenticate.call_args.args[1] == service_type
    assert authenticate.call_args.args[5] == service_decorator._resolve_scopes(scopes)
    assert impl.call_args.args[:2] == (service, "u@e.com")
    service.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [get_script_project, manage_script_project])
async def test_invalid_project_action_precedes_authentication(tool):
    with patch.object(service_decorator, "_get_auth_context", new=AsyncMock()) as auth:
        kwargs = {"action": "bogus"}
        if "user_google_email" in inspect.signature(tool).parameters:
            kwargs["user_google_email"] = "u@e.com"
        with pytest.raises(UserInputError, match="Invalid action"):
            await tool(**kwargs)
    auth.assert_not_awaited()


@pytest.mark.asyncio
async def test_manage_script_project_routes_actions_to_correct_service():
    drive_service = Mock()
    script_service = Mock()
    fn = _undecorated(manage_script_project)

    with (
        patch(
            "gappsscript.apps_script_tools._create_script_project_impl",
            new=AsyncMock(return_value="created"),
        ) as create_impl,
        patch(
            "gappsscript.apps_script_tools._delete_script_project_impl",
            new=AsyncMock(return_value="deleted"),
        ) as delete_impl,
    ):
        assert (
            await fn(drive_service, script_service, "u@e.com", "create", title="T")
            == "created"
        )
        assert (
            await fn(drive_service, script_service, "u@e.com", "delete", script_id="s1")
            == "deleted"
        )

    # Delete uses the Drive client; create uses the Script client.
    assert delete_impl.call_args.args[0] is drive_service
    assert create_impl.call_args.args[0] is script_service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"action": "delete"}, "script_id is required"),
        ({"action": "create"}, "title is required"),
        ({"action": "bogus"}, "Invalid action"),
    ],
)
async def test_manage_script_project_validates_arguments(kwargs, message):
    fn = _undecorated(manage_script_project)
    with pytest.raises(UserInputError, match=message):
        await fn(Mock(), Mock(), "u@e.com", **kwargs)


@pytest.mark.asyncio
async def test_get_script_project_routes_list_project_and_file_reads():
    drive_service = Mock()
    script_service = Mock()
    fn = _undecorated(get_script_project)
    with (
        patch(
            "gappsscript.apps_script_tools._list_script_projects_impl",
            new=AsyncMock(return_value="listed"),
        ) as list_impl,
        patch(
            "gappsscript.apps_script_tools._get_script_content_impl",
            new=AsyncMock(return_value="file"),
        ) as file_impl,
        patch(
            "gappsscript.apps_script_tools._get_script_project_impl",
            new=AsyncMock(return_value="project"),
        ) as project_impl,
    ):
        assert await fn(drive_service, script_service, "u@e.com", "list") == "listed"
        assert (
            await fn(
                drive_service,
                script_service,
                "u@e.com",
                "get",
                "s1",
                file_name="Code",
            )
            == "file"
        )
        assert (
            await fn(drive_service, script_service, "u@e.com", "get", "s1") == "project"
        )

    assert list_impl.call_args.args[0] is drive_service
    assert file_impl.call_count == 1
    assert project_impl.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"action": "get"}, "script_id is required"),
        ({"action": "bogus"}, "Invalid action"),
    ],
)
async def test_get_script_project_validates_arguments(kwargs, message):
    fn = _undecorated(get_script_project)
    with pytest.raises(UserInputError, match=message):
        await fn(Mock(), Mock(), "u@e.com", **kwargs)


@pytest.mark.asyncio
async def test_manage_script_content_update_requires_files():
    fn = _undecorated(manage_script_content)
    with pytest.raises(UserInputError, match="files is required"):
        await fn(Mock(), "u@e.com", "update", "s1")


@pytest.mark.asyncio
async def test_manage_script_content_rejects_unknown_action():
    fn = _undecorated(manage_script_content)
    with pytest.raises(UserInputError, match="Invalid action"):
        await fn(Mock(), "u@e.com", "delete", "s1")


@pytest.mark.asyncio
async def test_list_script_deployments():
    mock_service = Mock()
    mock_service.projects().deployments().list().execute.return_value = {
        "deployments": []
    }
    fn = _undecorated(list_script_deployments)
    result = await fn(
        service=mock_service,
        user_google_email="u@e.com",
        script_id="s1",
    )
    assert "No deployments found" in result


@pytest.mark.asyncio
async def test_get_script_version_get_requires_version_number():
    fn = _undecorated(get_script_version)
    with pytest.raises(UserInputError, match="version_number is required"):
        await fn(Mock(), "u@e.com", "get", "s1")


@pytest.mark.asyncio
async def test_manage_script_version_rejects_unknown_action():
    fn = _undecorated(manage_script_version)
    with pytest.raises(UserInputError, match="Invalid action"):
        await fn(Mock(), "u@e.com", "delete", "s1")


@pytest.mark.asyncio
async def test_get_script_version_rejects_unknown_action():
    fn = _undecorated(get_script_version)
    with pytest.raises(UserInputError, match="Invalid action"):
        await fn(Mock(), "u@e.com", "create", "s1")


@pytest.mark.asyncio
async def test_get_script_activity_metrics_requires_script_id():
    fn = _undecorated(get_script_activity)
    with pytest.raises(UserInputError, match="script_id is required"):
        await fn(Mock(), "u@e.com", "metrics")


@pytest.mark.asyncio
async def test_get_script_activity_processes_allows_missing_script_id():
    fn = _undecorated(get_script_activity)
    with patch(
        "gappsscript.apps_script_tools._list_script_processes_impl",
        new=AsyncMock(return_value="ok"),
    ) as processes_impl:
        assert await fn(Mock(), "u@e.com", "processes") == "ok"
    # script_id defaults to None and is forwarded to the impl.
    assert processes_impl.call_args.args[3] is None


@pytest.mark.asyncio
async def test_manage_script_trigger_routes_list_and_delete():
    fn = _undecorated(manage_script_trigger)
    with (
        patch(
            "gappsscript.apps_script_tools._list_script_triggers_impl",
            new=AsyncMock(return_value="listed"),
        ),
        patch(
            "gappsscript.apps_script_tools._delete_script_trigger_impl",
            new=AsyncMock(return_value="deleted"),
        ),
    ):
        assert await fn(Mock(), "u@e.com", "list", "s1") == "listed"
        assert await fn(Mock(), "u@e.com", "delete", "s1", trigger_id="t1") == "deleted"


@pytest.mark.asyncio
async def test_manage_script_trigger_rejects_unknown_action():
    fn = _undecorated(manage_script_trigger)
    with pytest.raises(UserInputError, match="Invalid action"):
        await fn(Mock(), "u@e.com", "create", "s1")
