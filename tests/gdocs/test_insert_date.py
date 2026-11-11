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
            "time_format": "TIME_FORMAT_HOUR_MINUTE_TIMEZONE",
        }
    )
    requests = await _execute(operation.model_dump(exclude_none=True))
    props = requests[0]["insertDate"]["dateElementProperties"]
    assert props["timestamp"] == "2026-11-03T09:30:00Z"
    assert props["timeZoneId"] == "Australia/Sydney"
    assert props["timeFormat"] == "TIME_FORMAT_HOUR_MINUTE_TIMEZONE"
    assert "endOfSegmentLocation" in requests[0]["insertDate"]


@pytest.mark.parametrize(
    "bad_date",
    ["", "tomorrow", "2026-13-45", "2026-11-03T09:30:00", "2026-11-03 09:30:00Z"],
)
def test_insert_date_rejects_invalid_dates(bad_date):
    with pytest.raises(ValidationError):
        TypeAdapter(BatchDocOperation).validate_python(
            {"type": "insert_date", "date": bad_date, "index": 1}
        )


@pytest.mark.asyncio
async def test_date_only_with_timezone_is_local_noon_in_that_zone():
    operation = TypeAdapter(BatchDocOperation).validate_python(
        {
            "type": "insert_date",
            "date": "2026-11-03",
            "index": 5,
            "timezone": "Pacific/Auckland",
            "time_format": "TIME_FORMAT_HOUR_MINUTE_TIMEZONE",
        }
    )
    requests = await _execute(operation.model_dump(exclude_none=True))
    props = requests[0]["insertDate"]["dateElementProperties"]
    assert props["timestamp"] == "2026-11-02T23:00:00Z"
    assert props["timeZoneId"] == "Pacific/Auckland"


@pytest.mark.parametrize(
    "extra, message",
    [
        ({"timezone": "Pacific/Auckland"}, "requires time_format"),
        (
            {
                "timezone": "Pacific/Auckland",
                "time_format": "TIME_FORMAT_HOUR_MINUTE",
            },
            "requires time_format",
        ),
        (
            {
                "timezone": "Mars/Base",
                "time_format": "TIME_FORMAT_HOUR_MINUTE_TIMEZONE",
            },
            "unknown IANA time zone",
        ),
        (
            {"timezone": "utc", "time_format": "TIME_FORMAT_HOUR_MINUTE_TIMEZONE"},
            "unknown IANA time zone",
        ),
        ({"date_format": "DATE_FORMAT_BOGUS"}, "date_format"),
    ],
)
def test_insert_date_rejects_invalid_zone_and_format_options(extra, message):
    with pytest.raises(ValidationError, match=message):
        TypeAdapter(BatchDocOperation).validate_python(
            {"type": "insert_date", "date": "2026-11-03", "index": 1, **extra}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "given, expected",
    [
        ("2026-11-03T09:30Z", "2026-11-03T09:30:00Z"),
        ("2026-11-03T20:30:00+11:00", "2026-11-03T09:30:00Z"),
        ("2026-11-03T09:30:00.250Z", "2026-11-03T09:30:00.250000Z"),
        ("0032-03-01T09:30:00Z", "0032-03-01T09:30:00Z"),
        ("0032-03-01", "0032-03-01T12:00:00Z"),
    ],
)
async def test_timestamps_are_normalised_to_utc(given, expected):
    operation = TypeAdapter(BatchDocOperation).validate_python(
        {"type": "insert_date", "date": given, "index": 1}
    )
    requests = await _execute(operation.model_dump(exclude_none=True))
    props = requests[0]["insertDate"]["dateElementProperties"]
    assert props["timestamp"] == expected


def test_supported_operations_include_insert_date():
    supported = BatchOperationManager(Mock()).get_supported_operations()[
        "supported_operations"
    ]
    assert supported["insert_date"]["required"] == ["date"]
    assert "timezone" in supported["insert_date"]["optional"]
