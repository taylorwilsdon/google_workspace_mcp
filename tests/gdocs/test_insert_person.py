"""Tests for the insert_person batch_update_doc operation."""

from unittest.mock import Mock

import pytest
from pydantic import TypeAdapter, ValidationError

from gdocs.docs_helpers import validate_operation
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
async def test_insert_person_builds_insert_person_request():
    operation = TypeAdapter(BatchDocOperation).validate_python(
        {"type": "insert_person", "email": "dustin.smith@example.com", "index": 5}
    )
    requests = await _execute(operation.model_dump(exclude_none=True))
    assert requests == [
        {
            "insertPerson": {
                "personProperties": {"email": "dustin.smith@example.com"},
                "location": {"index": 5},
            }
        }
    ]


@pytest.mark.asyncio
async def test_insert_person_at_end_of_segment_in_tab():
    requests = await _execute(
        {
            "type": "insert_person",
            "email": "dustin.smith@example.com",
            "end_of_segment": True,
            "segment_id": "kix.footer1",
            "tab_id": "t.1",
        }
    )
    assert requests[0]["insertPerson"]["endOfSegmentLocation"] == {
        "segmentId": "kix.footer1",
        "tabId": "t.1",
    }


def test_insert_person_requires_email():
    operation = {"type": "insert_person", "index": 1}
    with pytest.raises(ValidationError, match="email"):
        TypeAdapter(BatchDocOperation).validate_python(operation)
    assert validate_operation(operation) == (False, "Missing required field: email")


def test_insert_person_rejects_empty_email():
    with pytest.raises(ValidationError, match="at least 1 character"):
        TypeAdapter(BatchDocOperation).validate_python(
            {"type": "insert_person", "email": "", "index": 1}
        )
