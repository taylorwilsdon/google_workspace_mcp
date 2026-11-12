"""batch_update_doc reports how many occurrences each find_replace changed."""

from unittest.mock import Mock

import pytest

from gdocs.managers.batch_operation_manager import BatchOperationManager


@pytest.mark.asyncio
async def test_batch_find_replace_reports_occurrence_counts() -> None:
    service = Mock()
    service.documents.return_value.get.return_value.execute.return_value = {
        "revisionId": "rev1",
        "tabs": [],
    }
    # Docs omits occurrencesChanged when nothing matched.
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": [
            {"replaceAllText": {"occurrencesChanged": 3}},
            {},
            {"replaceAllText": {}},
        ]
    }
    operations = [
        {"type": "find_replace", "find_text": "{{name}}", "replace_text": "Ada"},
        {"type": "insert_text", "index": 1, "text": "Hi "},
        {"type": "find_replace", "find_text": "{{missing}}", "replace_text": "x"},
    ]

    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc123", operations
    )

    assert success
    assert "Replaced 3 occurrence(s) of '{{name}}'" in message
    assert "Replaced 0 occurrence(s) of '{{missing}}'" in message
