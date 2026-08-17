"""Regression tests for addDocumentTab response key handling.

The Google Docs batchUpdate response for an addDocumentTab request comes back
under the key ``addDocumentTab`` (matching the request field name), not
``createDocumentTab``. The original code looked for ``createDocumentTab``,
so tab_id extraction silently failed. These tests lock in the fix and its
backwards-compat fallback.
"""

from unittest.mock import Mock

import pytest

from core.utils import UserInputError
from gdocs import docs_tools
from gdocs.managers.batch_operation_manager import BatchOperationManager


COMMON_RESPONSE_KEYS = {
    "action",
    "success",
    "message",
    "tab_id",
    "requests_applied",
    "link",
}


def _unwrap(tool):
    """Unwrap the decorated tool function to the original implementation.

    Mirrors the helper in tests/gdocs/test_advanced_doc_formatting.py so we
    stay consistent with the existing fork convention.
    """
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _mock_service_with_reply(reply: dict) -> Mock:
    """Build a Docs service mock whose batchUpdate returns the given reply."""
    mock_service = Mock()
    mock_service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": [reply],
        "documentId": "doc-abc",
    }
    return mock_service


@pytest.mark.asyncio
async def test_create_tab_extracts_tab_id_from_add_document_tab_reply():
    """The fix - reply["addDocumentTab"] is where Google puts the new tab's properties."""
    service = _mock_service_with_reply(
        {
            "addDocumentTab": {
                "tabProperties": {
                    "tabId": "t.xyz123",
                    "title": "My Tab",
                    "index": 0,
                }
            }
        }
    )

    result = await _unwrap(docs_tools.manage_doc_tab)(
        service=service,
        user_google_email="test@example.com",
        document_id="doc-abc",
        action="create",
        title="My Tab",
        index=0,
    )

    assert result["tab_id"] == "t.xyz123"
    assert "Tab ID: t.xyz123" in result["message"]
    assert set(result) == COMMON_RESPONSE_KEYS
    assert result["success"] is True
    assert result["requests_applied"] == 1
    assert result["link"] == "https://docs.google.com/document/d/doc-abc/edit"


@pytest.mark.asyncio
async def test_create_tab_falls_back_to_create_document_tab_for_compat():
    """Backwards compat - if Google ever returns under createDocumentTab, still work."""
    service = _mock_service_with_reply(
        {
            "createDocumentTab": {
                "tabProperties": {
                    "tabId": "t.legacy",
                    "title": "Legacy Tab",
                }
            }
        }
    )

    result = await _unwrap(docs_tools.manage_doc_tab)(
        service=service,
        user_google_email="test@example.com",
        document_id="doc-abc",
        action="create",
        title="Legacy Tab",
        index=0,
    )

    assert result["tab_id"] == "t.legacy"


@pytest.mark.asyncio
async def test_create_tab_omits_tab_id_when_reply_is_empty():
    """Guard - if the reply has neither key, the tool must not crash."""
    service = _mock_service_with_reply({})

    result = await _unwrap(docs_tools.manage_doc_tab)(
        service=service,
        user_google_email="test@example.com",
        document_id="doc-abc",
        action="create",
        title="Orphan Tab",
        index=0,
    )

    assert result["tab_id"] is None
    assert "doc-abc" in result["message"]


@pytest.mark.asyncio
async def test_populate_tab_accepts_empty_markdown_to_clear_existing_content():
    """Empty markdown is valid when replace_existing is used to clear a tab."""
    service = Mock()
    docs = service.documents.return_value
    docs.get.return_value.execute.return_value = {
        "tabs": [
            {
                "tabProperties": {"tabId": "t.clear"},
                "documentTab": {
                    "body": {
                        "content": [
                            {"endIndex": 1},
                            {"endIndex": 12},
                        ]
                    }
                },
            }
        ]
    }
    docs.batchUpdate.return_value.execute.return_value = {"replies": []}

    result = await _unwrap(docs_tools.manage_doc_tab)(
        service=service,
        user_google_email="test@example.com",
        document_id="doc-abc",
        action="populate_from_markdown",
        tab_id="t.clear",
        markdown_text="",
    )

    request_body = docs.batchUpdate.call_args.kwargs["body"]
    assert result["success"] is True
    assert set(result) == COMMON_RESPONSE_KEYS
    assert result["action"] == "populate_from_markdown"
    assert result["link"] == "https://docs.google.com/document/d/doc-abc/edit"
    assert request_body["requests"] == [
        {
            "deleteContentRange": {
                "range": {"startIndex": 1, "endIndex": 11, "tabId": "t.clear"}
            }
        }
    ]


@pytest.mark.asyncio
async def test_populate_tab_rejects_missing_tab_before_batch_update():
    """Missing tabs should produce a user-facing error before request generation."""
    service = Mock()
    docs = service.documents.return_value
    docs.get.return_value.execute.return_value = {
        "tabs": [
            {
                "tabProperties": {"tabId": "t.exists"},
                "documentTab": {"body": {"content": [{"endIndex": 1}]}},
            }
        ]
    }

    with pytest.raises(UserInputError, match="'t.missing' not found in document"):
        await _unwrap(docs_tools.manage_doc_tab)(
            service=service,
            user_google_email="test@example.com",
            document_id="doc-abc",
            action="populate_from_markdown",
            tab_id="t.missing",
            markdown_text="Hello",
        )

    docs.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_populate_tab_rejects_missing_tab_when_appending():
    """Missing tabs are rejected even when replace_existing is false."""
    service = Mock()
    docs = service.documents.return_value
    docs.get.return_value.execute.return_value = {
        "tabs": [
            {
                "tabProperties": {"tabId": "t.exists"},
                "documentTab": {"body": {"content": [{"endIndex": 1}]}},
            }
        ]
    }

    with pytest.raises(UserInputError, match="'t.missing' not found in document"):
        await _unwrap(docs_tools.manage_doc_tab)(
            service=service,
            user_google_email="test@example.com",
            document_id="doc-abc",
            action="populate_from_markdown",
            tab_id="t.missing",
            markdown_text="Hello",
            replace_existing=False,
        )

    docs.batchUpdate.assert_not_called()


def test_find_tab_end_index_treats_non_document_tab_as_not_found():
    doc = {
        "tabs": [
            {"tabProperties": {"tabId": "t.container"}},
            {"tabProperties": {"tabId": "t.empty"}, "documentTab": {"body": {}}},
        ]
    }

    assert docs_tools._find_tab_end_index(doc, "t.container") is None
    assert docs_tools._find_tab_end_index(doc, "t.empty") == 1


class TestBatchOperationManagerExtractCreatedTabs:
    """Companion tests for BatchOperationManager._extract_created_tabs, which
    had the identical bug at gdocs/managers/batch_operation_manager.py line 883.
    """

    def _manager(self) -> BatchOperationManager:
        return BatchOperationManager(service=Mock())

    def test_extracts_from_add_document_tab(self):
        manager = self._manager()
        result = {
            "replies": [
                {
                    "addDocumentTab": {
                        "tabProperties": {"tabId": "t.new", "title": "New Tab"}
                    }
                }
            ]
        }

        tabs = manager._extract_created_tabs(result)

        assert tabs == [{"tab_id": "t.new", "title": "New Tab"}]

    def test_extracts_from_create_document_tab_for_compat(self):
        manager = self._manager()
        result = {
            "replies": [
                {
                    "createDocumentTab": {
                        "tabProperties": {"tabId": "t.legacy", "title": "Legacy"}
                    }
                }
            ]
        }

        tabs = manager._extract_created_tabs(result)

        assert tabs == [{"tab_id": "t.legacy", "title": "Legacy"}]

    def test_mixed_replies_extracts_only_tab_replies(self):
        manager = self._manager()
        result = {
            "replies": [
                {"insertText": {}},
                {"addDocumentTab": {"tabProperties": {"tabId": "t.a", "title": "A"}}},
                {},
                {"addDocumentTab": {"tabProperties": {"tabId": "t.b", "title": "B"}}},
            ]
        }

        tabs = manager._extract_created_tabs(result)

        assert tabs == [
            {"tab_id": "t.a", "title": "A"},
            {"tab_id": "t.b", "title": "B"},
        ]
