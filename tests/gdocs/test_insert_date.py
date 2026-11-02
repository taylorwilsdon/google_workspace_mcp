"""Tests for the insert_date batch_update_doc operation."""

from unittest.mock import Mock

import pytest
from pydantic import TypeAdapter, ValidationError

from gdocs.managers.batch_operation_manager import BatchOperationManager
from gdocs.operation_schemas import BatchDocOperation


async def _execute(operation):
    service = Mock()
    docs = service.documents.return_value
    docs.batchUpdate.return_value.execute.return_value = {"replies": [{}]}
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc123", [operation]
    )
    assert success, message
    return docs.batchUpdate.call_args.kwargs["body"]["requests"]


@pytest.mark.asyncio
async def test_insert_date_builds_insert_date_request():
    operation = TypeAdapter(BatchDocOperation).validate_python(
        {"type": "insert_date", "date": "2026-11-03", "index": 5}
    )
    requests = await _execute(operation.model_dump(exclude_none=True))
    assert requests == [
        {
            "insertDate": {
                "dateElementProperties": {
                    "timestamp": "2026-11-03T12:00:00Z",
                    "dateFormat": "DATE_FORMAT_MONTH_DAY_YEAR_ABBREVIATED",
                },
                "location": {"index": 5},
            }
        }
    ]


@pytest.mark.asyncio
async def test_insert_date_at_end_of_segment_with_timezone():
    operation = TypeAdapter(BatchDocOperation).validate_python(
        {
            "type": "insert_date",
            "date": "2026-11-03T09:30:00Z",
            "end_of_segment": True,
            "timezone": "Australia/Sydney",
            "time_format": "TIME_FORMAT_HOUR_MINUTE",
        }
    )
    requests = await _execute(operation.model_dump(exclude_none=True))
    props = requests[0]["insertDate"]["dateElementProperties"]
    assert props["timestamp"] == "2026-11-03T09:30:00Z"
    assert props["timeZoneId"] == "Australia/Sydney"
    assert props["timeFormat"] == "TIME_FORMAT_HOUR_MINUTE"
    assert "endOfSegmentLocation" in requests[0]["insertDate"]


@pytest.mark.parametrize("bad_date", ["", "tomorrow", "2026-13-45"])
def test_insert_date_rejects_invalid_dates(bad_date):
    with pytest.raises(ValidationError):
        TypeAdapter(BatchDocOperation).validate_python(
            {"type": "insert_date", "date": bad_date, "index": 1}
        )
