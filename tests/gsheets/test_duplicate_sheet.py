"""Tests for create_sheet duplication (source_sheet_name parameter)."""

from unittest.mock import Mock

import pytest

from core.server import server
from core.tool_registry import get_tool_components
from core.utils import UserInputError
from gsheets.sheets_tools import create_sheet


def _create_mock_service(sheets_metadata, batch_update_response):
    """Create a Sheets service mock for create_sheet."""
    mock_service = Mock()
    mock_service.spreadsheets().get().execute = Mock(return_value=sheets_metadata)
    mock_service.spreadsheets().batchUpdate().execute = Mock(
        return_value=batch_update_response
    )
    return mock_service


async def _call_create_sheet(service, **overrides):
    """Call the undecorated implementation to keep auth out of unit tests."""
    impl = create_sheet.__wrapped__.__wrapped__
    defaults = {
        "service": service,
        "user_google_email": "user@example.com",
        "spreadsheet_id": "spreadsheet-123",
        "sheet_name": "New Sheet",
    }
    defaults.update(overrides)
    return await impl(**defaults)


def test_create_sheet_schema_marks_sheet_name_optional_string():
    components = get_tool_components(server)
    parameters = components["create_sheet"].parameters

    assert "sheet_name" not in parameters["required"]
    assert parameters["properties"]["sheet_name"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "default": None,
    }


@pytest.mark.asyncio
async def test_duplicate_sheet_basic():
    service = _create_mock_service(
        sheets_metadata={
            "sheets": [
                {"properties": {"sheetId": 100, "title": "Original"}},
            ]
        },
        batch_update_response={
            "replies": [
                {
                    "duplicateSheet": {
                        "properties": {
                            "sheetId": 200,
                            "title": "Copy of Original",
                        }
                    }
                }
            ]
        },
    )

    result = await _call_create_sheet(
        service, sheet_name="Copy of Original", source_sheet_name="Original"
    )

    assert "Successfully duplicated" in result
    assert "'Original'" in result
    assert "'Copy of Original'" in result
    assert "(ID: 200)" in result
    request_body = service.spreadsheets().batchUpdate.call_args.kwargs["body"]
    assert request_body["requests"][0]["duplicateSheet"]["newSheetName"] == (
        "Copy of Original"
    )


@pytest.mark.asyncio
async def test_duplicate_sheet_omits_target_name_when_not_provided():
    service = _create_mock_service(
        sheets_metadata={
            "sheets": [
                {"properties": {"sheetId": 100, "title": "Original"}},
            ]
        },
        batch_update_response={
            "replies": [
                {
                    "duplicateSheet": {
                        "properties": {
                            "sheetId": 200,
                            "title": "Copy of Original",
                        }
                    }
                }
            ]
        },
    )

    result = await _call_create_sheet(
        service, sheet_name=None, source_sheet_name="Original"
    )

    assert "Successfully duplicated" in result
    assert "'Original'" in result
    assert "'Copy of Original'" in result
    request_body = service.spreadsheets().batchUpdate.call_args.kwargs["body"]
    assert request_body["requests"][0]["duplicateSheet"] == {"sourceSheetId": 100}


@pytest.mark.asyncio
async def test_duplicate_sheet_with_custom_name_and_index():
    service = _create_mock_service(
        sheets_metadata={
            "sheets": [
                {"properties": {"sheetId": 100, "title": "2026-04-21"}},
            ]
        },
        batch_update_response={
            "replies": [
                {
                    "duplicateSheet": {
                        "properties": {
                            "sheetId": 300,
                            "title": "2026-04-28",
                        }
                    }
                }
            ]
        },
    )

    result = await _call_create_sheet(
        service,
        sheet_name="2026-04-28",
        source_sheet_name="2026-04-21",
        insert_sheet_index=0,
    )

    assert "'2026-04-28'" in result
    assert "(ID: 300)" in result


@pytest.mark.asyncio
async def test_duplicate_sheet_source_not_found():
    service = _create_mock_service(
        sheets_metadata={
            "sheets": [
                {"properties": {"sheetId": 100, "title": "Sheet1"}},
            ]
        },
        batch_update_response={},
    )

    with pytest.raises(UserInputError, match="not found"):
        await _call_create_sheet(
            service, sheet_name="Copy", source_sheet_name="NonExistent"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("source_sheet_name", ["", "   ", "\t\n"])
async def test_duplicate_sheet_rejects_blank_source_sheet_name(source_sheet_name):
    service = Mock()

    with pytest.raises(
        UserInputError, match="source_sheet_name must be a non-empty string"
    ):
        await _call_create_sheet(
            service, sheet_name="Copy", source_sheet_name=source_sheet_name
        )

    service.spreadsheets.assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_sheet_trims_source_sheet_name():
    service = _create_mock_service(
        sheets_metadata={
            "sheets": [
                {"properties": {"sheetId": 100, "title": "Original"}},
            ]
        },
        batch_update_response={
            "replies": [
                {
                    "duplicateSheet": {
                        "properties": {
                            "sheetId": 200,
                            "title": "Copy of Original",
                        }
                    }
                }
            ]
        },
    )

    result = await _call_create_sheet(
        service, sheet_name="Copy of Original", source_sheet_name="  Original  "
    )

    assert "Successfully duplicated" in result
    assert "'Original'" in result
    assert "'  Original  '" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_sheet_name", "insert_sheet_index"),
    [
        (None, -1),
        (None, "1"),
        ("Original", -1),
        ("Original", "1"),
    ],
)
async def test_create_sheet_rejects_invalid_insert_sheet_index(
    source_sheet_name, insert_sheet_index
):
    service = Mock()

    with pytest.raises(
        UserInputError, match="insert_sheet_index must be a non-negative integer"
    ):
        await _call_create_sheet(
            service,
            source_sheet_name=source_sheet_name,
            insert_sheet_index=insert_sheet_index,
        )

    service.spreadsheets.assert_not_called()
