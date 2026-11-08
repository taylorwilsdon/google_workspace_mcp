"""Tests for create_sheet duplication (source_sheet_name parameter)."""

from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

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


def _create_copy_service(sheets_metadata, copy_response, batch_update_effect=None):
    """Create a Sheets service mock for cross-spreadsheet copies."""
    service = Mock()
    spreadsheets = service.spreadsheets.return_value
    spreadsheets.get.return_value.execute.return_value = sheets_metadata
    spreadsheets.sheets.return_value.copyTo.return_value.execute.return_value = (
        copy_response
    )
    spreadsheets.batchUpdate.return_value.execute.side_effect = batch_update_effect
    return service


def _batch_update_calls(service):
    return [call.kwargs for call in service.spreadsheets().batchUpdate.call_args_list]


def _http_error(status=400):
    return HttpError(Response({"status": str(status)}), b"error")


TEMPLATE_METADATA = {"sheets": [{"properties": {"sheetId": 100, "title": "Template"}}]}


def test_create_sheet_schema_exposes_destination_spreadsheet_id():
    parameters = get_tool_components(server)["create_sheet"].parameters

    assert "destination_spreadsheet_id" not in parameters["required"]
    assert parameters["properties"]["destination_spreadsheet_id"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "default": None,
    }


@pytest.mark.asyncio
async def test_copy_sheet_to_other_spreadsheet_calls_copy_to():
    service = _create_copy_service(
        TEMPLATE_METADATA, {"sheetId": 555, "title": "Copy of Template"}
    )

    result = await _call_create_sheet(
        service,
        sheet_name=None,
        source_sheet_name="Template",
        destination_spreadsheet_id=" dest-456 ",
    )

    service.spreadsheets().sheets().copyTo.assert_called_once_with(
        spreadsheetId="spreadsheet-123",
        sheetId=100,
        body={"destinationSpreadsheetId": "dest-456"},
    )
    assert _batch_update_calls(service) == []
    assert (
        "Successfully duplicated 'Template' to 'Copy of Template' (ID: 555) "
        "in spreadsheet dest-456"
    ) in result


@pytest.mark.asyncio
async def test_copy_sheet_to_other_spreadsheet_applies_name_and_index():
    service = _create_copy_service(
        {"sheets": [{"properties": {"sheetId": 100, "title": "보고서 템플릿"}}]},
        {"sheetId": 555, "title": "Copy of 보고서 템플릿"},
        batch_update_effect=[{}],
    )

    result = await _call_create_sheet(
        service,
        sheet_name="2026년 11월",
        source_sheet_name="보고서 템플릿",
        insert_sheet_index=0,
        destination_spreadsheet_id="dest-456",
    )

    assert _batch_update_calls(service) == [
        {
            "spreadsheetId": "dest-456",
            "body": {
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": 555,
                                "title": "2026년 11월",
                                "index": 0,
                            },
                            "fields": "title,index",
                        }
                    }
                ]
            },
        }
    ]
    assert "'2026년 11월' (ID: 555)" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [_http_error(), TimeoutError("timed out")])
async def test_copy_sheet_rename_failure_removes_copy(error):
    service = _create_copy_service(
        TEMPLATE_METADATA,
        {"sheetId": 555, "title": "Copy of Template"},
        batch_update_effect=[error, {}],
    )

    with pytest.raises(type(error)) as excinfo:
        await _call_create_sheet(
            service,
            sheet_name="Taken",
            source_sheet_name="Template",
            destination_spreadsheet_id="dest-456",
        )

    assert excinfo.value.args == error.args
    assert _batch_update_calls(service)[1] == {
        "spreadsheetId": "dest-456",
        "body": {"requests": [{"deleteSheet": {"sheetId": 555}}]},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "errors",
    [
        [_http_error(), _http_error(500)],
        [TimeoutError("timed out"), TimeoutError("timed out")],
    ],
)
async def test_copy_sheet_reports_stray_copy_when_cleanup_fails(errors):
    service = _create_copy_service(
        TEMPLATE_METADATA,
        {"sheetId": 555, "title": "Copy of Template"},
        batch_update_effect=errors,
    )

    with pytest.raises(RuntimeError, match=r"'Copy of Template' \(ID: 555\)"):
        await _call_create_sheet(
            service,
            sheet_name="Taken",
            source_sheet_name="Template",
            destination_spreadsheet_id="dest-456",
        )


@pytest.mark.asyncio
async def test_copy_sheet_to_same_spreadsheet_duplicates_in_place():
    service = _create_copy_service(
        TEMPLATE_METADATA,
        {},
        batch_update_effect=[
            {
                "replies": [
                    {"duplicateSheet": {"properties": {"sheetId": 200, "title": "Q4"}}}
                ]
            }
        ],
    )

    result = await _call_create_sheet(
        service,
        sheet_name="Q4",
        source_sheet_name="Template",
        destination_spreadsheet_id="spreadsheet-123",
    )

    service.spreadsheets().sheets().copyTo.assert_not_called()
    request = _batch_update_calls(service)[0]["body"]["requests"][0]
    assert request["duplicateSheet"] == {"sourceSheetId": 100, "newSheetName": "Q4"}
    assert "Successfully duplicated 'Template' to 'Q4'" in result


@pytest.mark.asyncio
async def test_copy_sheet_source_not_found_skips_copy():
    service = _create_copy_service(TEMPLATE_METADATA, {})

    with pytest.raises(UserInputError, match="not found"):
        await _call_create_sheet(
            service,
            source_sheet_name="Missing",
            destination_spreadsheet_id="dest-456",
        )

    service.spreadsheets().sheets().copyTo.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_sheet_name", "destination_spreadsheet_id", "message"),
    [
        ("Template", "", "destination_spreadsheet_id must be a non-empty string"),
        ("Template", "  ", "destination_spreadsheet_id must be a non-empty string"),
        (None, "dest-456", "destination_spreadsheet_id requires source_sheet_name"),
    ],
)
async def test_copy_sheet_rejects_invalid_destination(
    source_sheet_name, destination_spreadsheet_id, message
):
    service = Mock()

    with pytest.raises(UserInputError, match=message):
        await _call_create_sheet(
            service,
            source_sheet_name=source_sheet_name,
            destination_spreadsheet_id=destination_spreadsheet_id,
        )

    service.spreadsheets.assert_not_called()
