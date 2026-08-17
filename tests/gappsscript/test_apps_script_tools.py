"""
Unit tests for Google Apps Script MCP tools

Tests all Apps Script tools with mocked API responses
"""

import asyncio
import os
import sys
import threading
from typing import get_type_hints
from unittest.mock import Mock

import pytest

from pydantic import TypeAdapter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError

# Import the internal implementation functions (not the decorated ones)
from gappsscript.apps_script_tools import (
    _list_script_projects_impl,
    _get_script_project_impl,
    _create_script_project_impl,
    _update_script_content_impl,
    _merge_script_files,
    _run_script_function_impl,
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
    manage_deployment,
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

    mock_service.scripts().run().execute.return_value = mock_response

    result = await _run_script_function_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        function_name="myFunction",
        dev_mode=True,
    )

    assert "Execution successful" in result
    assert "myFunction" in result


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
    """Test listing deployments"""
    mock_service = Mock()
    mock_response = {
        "deployments": [
            {
                "deploymentId": "deploy123",
                "description": "Production",
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
