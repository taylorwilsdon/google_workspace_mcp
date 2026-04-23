"""Unit tests for modify_doc_text full-document replacement guard."""

import inspect
import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gdocs.docs_tools import (  # noqa: E402
    _extract_body_end_index,
    _is_full_document_replacement_request,
    modify_doc_text,
)


def _create_mock_docs_service(body_end_index: int = 100) -> tuple[Mock, Mock]:
    """Create a Google Docs service mock with configurable body end index."""
    mock_service = Mock()
    docs_resource = Mock()
    mock_service.documents.return_value = docs_resource

    get_request = Mock()
    get_request.execute = Mock(
        return_value={"body": {"content": [{"endIndex": body_end_index}]}}
    )
    docs_resource.get.return_value = get_request

    batch_request = Mock()
    batch_request.execute = Mock(return_value={})
    docs_resource.batchUpdate.return_value = batch_request

    return mock_service, docs_resource


def test_extract_body_end_index_uses_max_content_end_index() -> None:
    document_metadata = {
        "body": {"content": [{"endIndex": 5}, {"endIndex": 17}, {"endIndex": 12}]}
    }
    assert _extract_body_end_index(document_metadata) == 17


def test_extract_body_end_index_handles_missing_body() -> None:
    assert _extract_body_end_index({}) == 0
    assert _extract_body_end_index({"body": {"content": "invalid"}}) == 0


def test_is_full_document_replacement_request_detection() -> None:
    assert (
        _is_full_document_replacement_request(
            start_index=1, end_index=99, body_end_index=100
        )
        is True
    )
    assert (
        _is_full_document_replacement_request(
            start_index=0, end_index=99, body_end_index=100
        )
        is True
    )
    assert (
        _is_full_document_replacement_request(
            start_index=10, end_index=20, body_end_index=100
        )
        is False
    )


@pytest.mark.asyncio
async def test_modify_doc_text_rejects_full_document_replacement() -> None:
    service, docs_resource = _create_mock_docs_service(body_end_index=100)
    raw_modify_doc_text = inspect.unwrap(modify_doc_text)

    result = await raw_modify_doc_text(
        service=service,
        user_google_email="user@example.com",
        document_id="1A2B3C4D5E6F7G8H9I0JkLmNoPqR",
        start_index=1,
        end_index=99,
        text="replacement",
    )

    assert (
        result
        == "Error: Full-document replacement is blocked in modify_doc_text. Use find_and_replace_doc for global substitutions."
    )
    docs_resource.batchUpdate.assert_not_called()
    docs_resource.get.assert_called_once()


@pytest.mark.asyncio
async def test_modify_doc_text_allows_partial_replacement() -> None:
    service, docs_resource = _create_mock_docs_service(body_end_index=100)
    raw_modify_doc_text = inspect.unwrap(modify_doc_text)

    result = await raw_modify_doc_text(
        service=service,
        user_google_email="user@example.com",
        document_id="1A2B3C4D5E6F7G8H9I0JkLmNoPqR",
        start_index=10,
        end_index=20,
        text="replacement",
    )

    assert "Replaced text from index 10 to 20" in result
    docs_resource.batchUpdate.assert_called_once()
    docs_resource.get.assert_called_once()
