"""
Tests for insert_table_row and delete_table_row operations in batch_update_doc.

Covers helper construction, validate_operation, batch manager integration.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from gdocs.docs_helpers import (
    create_insert_table_row_request,
    create_delete_table_row_request,
    validate_operation,
)


class TestCreateInsertTableRowRequest:
    def test_insert_below(self):
        result = create_insert_table_row_request(
            table_start_index=10,
            row_index=2,
            insert_below=True,
        )
        inner = result["insertTableRow"]
        assert inner["insertBelow"] is True
        assert inner["tableCellLocation"]["tableStartLocation"] == {"index": 10}
        assert inner["tableCellLocation"]["rowIndex"] == 2
        assert inner["tableCellLocation"]["columnIndex"] == 0

    def test_insert_above(self):
        result = create_insert_table_row_request(
            table_start_index=10,
            row_index=1,
            insert_below=False,
        )
        inner = result["insertTableRow"]
        assert inner["insertBelow"] is False

    def test_with_tab_id(self):
        result = create_insert_table_row_request(
            table_start_index=10,
            row_index=0,
            tab_id="t.abc123",
        )
        location = result["insertTableRow"]["tableCellLocation"]["tableStartLocation"]
        assert location == {"index": 10, "tabId": "t.abc123"}

    def test_default_insert_below(self):
        result = create_insert_table_row_request(
            table_start_index=5,
            row_index=0,
        )
        assert result["insertTableRow"]["insertBelow"] is True


class TestCreateDeleteTableRowRequest:
    def test_basic(self):
        result = create_delete_table_row_request(
            table_start_index=10,
            row_index=3,
        )
        inner = result["deleteTableRow"]
        assert inner["tableCellLocation"]["tableStartLocation"] == {"index": 10}
        assert inner["tableCellLocation"]["rowIndex"] == 3
        assert inner["tableCellLocation"]["columnIndex"] == 0

    def test_with_tab_id(self):
        result = create_delete_table_row_request(
            table_start_index=10,
            row_index=2,
            tab_id="t.xyz",
        )
        location = result["deleteTableRow"]["tableCellLocation"]["tableStartLocation"]
        assert location == {"index": 10, "tabId": "t.xyz"}


class TestValidateOperation:
    def test_valid_insert_table_row(self):
        is_valid, msg = validate_operation(
            {"type": "insert_table_row", "table_start_index": 10, "row_index": 2}
        )
        assert is_valid, msg

    def test_insert_table_row_missing_row_index(self):
        is_valid, msg = validate_operation(
            {"type": "insert_table_row", "table_start_index": 10}
        )
        assert not is_valid
        assert "row_index" in msg

    def test_insert_table_row_missing_table_start_index(self):
        is_valid, msg = validate_operation({"type": "insert_table_row", "row_index": 2})
        assert not is_valid
        assert "table_start_index" in msg

    def test_valid_delete_table_row(self):
        is_valid, msg = validate_operation(
            {"type": "delete_table_row", "table_start_index": 10, "row_index": 1}
        )
        assert is_valid, msg

    def test_delete_table_row_missing_row_index(self):
        is_valid, msg = validate_operation(
            {"type": "delete_table_row", "table_start_index": 10}
        )
        assert not is_valid
        assert "row_index" in msg


class TestBatchManagerIntegration:
    @pytest.fixture()
    def manager(self):
        from gdocs.managers.batch_operation_manager import BatchOperationManager

        return BatchOperationManager(Mock())

    def test_build_insert_table_row_request(self, manager):
        request, desc = manager._build_operation_request(
            {
                "type": "insert_table_row",
                "table_start_index": 10,
                "row_index": 2,
                "insert_below": True,
            },
            "insert_table_row",
        )
        inner = request["insertTableRow"]
        assert inner["insertBelow"] is True
        assert inner["tableCellLocation"]["rowIndex"] == 2
        assert "below" in desc
        assert "row 2" in desc
        assert "10" in desc

    def test_build_insert_table_row_above(self, manager):
        request, desc = manager._build_operation_request(
            {
                "type": "insert_table_row",
                "table_start_index": 10,
                "row_index": 1,
                "insert_below": False,
            },
            "insert_table_row",
        )
        inner = request["insertTableRow"]
        assert inner["insertBelow"] is False
        assert "above" in desc

    def test_build_delete_table_row_request(self, manager):
        request, desc = manager._build_operation_request(
            {
                "type": "delete_table_row",
                "table_start_index": 10,
                "row_index": 3,
            },
            "delete_table_row",
        )
        inner = request["deleteTableRow"]
        assert inner["tableCellLocation"]["rowIndex"] == 3
        assert "delete" in desc
        assert "row 3" in desc
        assert "10" in desc

    @pytest.mark.asyncio
    async def test_end_to_end_insert_table_row(self, manager):
        manager._execute_batch_requests = AsyncMock(return_value={"replies": [{}]})
        success, _, meta = await manager.execute_batch_operations(
            "doc-123",
            [
                {
                    "type": "insert_table_row",
                    "table_start_index": 10,
                    "row_index": 2,
                }
            ],
        )
        assert success
        assert meta["operations_count"] == 1

    @pytest.mark.asyncio
    async def test_end_to_end_delete_table_row(self, manager):
        manager._execute_batch_requests = AsyncMock(return_value={"replies": [{}]})
        success, _, meta = await manager.execute_batch_operations(
            "doc-123",
            [
                {
                    "type": "delete_table_row",
                    "table_start_index": 10,
                    "row_index": 1,
                }
            ],
        )
        assert success
        assert meta["operations_count"] == 1

    def test_supported_operations_include_insert_table_row(self, manager):
        supported = manager.get_supported_operations()["supported_operations"]
        assert "insert_table_row" in supported
        assert supported["insert_table_row"]["required"] == [
            "table_start_index",
            "row_index",
        ]
        assert "insert_below" in supported["insert_table_row"]["optional"]

    def test_supported_operations_include_delete_table_row(self, manager):
        supported = manager.get_supported_operations()["supported_operations"]
        assert "delete_table_row" in supported
        assert supported["delete_table_row"]["required"] == [
            "table_start_index",
            "row_index",
        ]
