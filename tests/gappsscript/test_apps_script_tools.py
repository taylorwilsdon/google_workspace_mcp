"""
Unit tests for Google Apps Script MCP tools

Tests all Apps Script tools with mocked API responses
"""

import pytest
from unittest.mock import Mock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# Import the internal implementation functions (not the decorated ones)
from gappsscript.apps_script_tools import (
    _list_script_projects_impl,
    _get_script_project_impl,
    _create_script_project_impl,
    _update_script_content_impl,
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
)


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
    """Test updating script project files"""
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
    )

    assert "Updated script project: test123" in result
    assert "Code" in result


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
    """Test updating deployment"""
    mock_service = Mock()
    mock_response = {
        "deploymentId": "deploy123",
        "description": "Updated description",
    }

    mock_service.projects().deployments().update().execute.return_value = mock_response

    result = await _update_deployment_impl(
        service=mock_service,
        user_google_email="test@example.com",
        script_id="test123",
        deployment_id="deploy123",
        description="Updated description",
    )

    assert "Updated deployment: deploy123" in result


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
