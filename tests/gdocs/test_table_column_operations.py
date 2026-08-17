"""
Tests for insert_table_column and delete_table_column operations in batch_update_doc.

Covers helper construction, validate_operation, batch manager integration.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from gdocs.docs_helpers import (
    create_insert_table_column_request,
    create_delete_table_column_request,
    validate_operation,
)


class TestCreateInsertTableColumnRequest:
    def test_insert_right(self):
        result = create_insert_table_column_request(
            table_start_index=10,
            column_index=2,
            insert_right=True,
        )
        inner = result["insertTableColumn"]
        assert inner["insertRight"] is True
        assert inner["tableCellLocation"]["tableStartLocation"] == {"index": 10}
        assert inner["tableCellLocation"]["columnIndex"] == 2
        assert inner["tableCellLocation"]["rowIndex"] == 0

    def test_insert_left(self):
        result = create_insert_table_column_request(
            table_start_index=10,
            column_index=1,
            insert_right=False,
        )
        inner = result["insertTableColumn"]
        assert inner["insertRight"] is False

    def test_with_tab_id(self):
        result = create_insert_table_column_request(
            table_start_index=10,
            column_index=0,
            tab_id="t.abc123",
        )
        location = result["insertTableColumn"]["tableCellLocation"][
            "tableStartLocation"
        ]
        assert location == {"index": 10, "tabId": "t.abc123"}

    def test_default_insert_right(self):
        result = create_insert_table_column_request(
            table_start_index=5,
            column_index=0,
        )
        assert result["insertTableColumn"]["insertRight"] is True


class TestCreateDeleteTableColumnRequest:
    def test_basic(self):
        result = create_delete_table_column_request(
            table_start_index=10,
            column_index=3,
        )
        inner = result["deleteTableColumn"]
        assert inner["tableCellLocation"]["tableStartLocation"] == {"index": 10}
        assert inner["tableCellLocation"]["columnIndex"] == 3
        assert inner["tableCellLocation"]["rowIndex"] == 0

    def test_with_tab_id(self):
        result = create_delete_table_column_request(
            table_start_index=10,
            column_index=2,
            tab_id="t.xyz",
        )
        location = result["deleteTableColumn"]["tableCellLocation"][
            "tableStartLocation"
        ]
        assert location == {"index": 10, "tabId": "t.xyz"}


class TestValidateOperation:
    def test_valid_insert_table_column(self):
        is_valid, msg = validate_operation(
            {"type": "insert_table_column", "table_start_index": 10, "column_index": 2}
        )
        assert is_valid, msg

    def test_insert_table_column_missing_column_index(self):
        is_valid, msg = validate_operation(
            {"type": "insert_table_column", "table_start_index": 10}
        )
        assert not is_valid
        assert "column_index" in msg

    def test_insert_table_column_missing_table_start_index(self):
        is_valid, msg = validate_operation(
            {"type": "insert_table_column", "column_index": 2}
        )
        assert not is_valid
        assert "table_start_index" in msg

    def test_valid_delete_table_column(self):
        is_valid, msg = validate_operation(
            {"type": "delete_table_column", "table_start_index": 10, "column_index": 1}
        )
        assert is_valid, msg

    def test_delete_table_column_missing_column_index(self):
        is_valid, msg = validate_operation(
            {"type": "delete_table_column", "table_start_index": 10}
        )
        assert not is_valid
        assert "column_index" in msg


class TestBatchManagerIntegration:
    @pytest.fixture()
    def manager(self):
        from gdocs.managers.batch_operation_manager import BatchOperationManager

        return BatchOperationManager(Mock())

    def test_build_insert_table_column_request(self, manager):
        request, desc = manager._build_operation_request(
            {
                "type": "insert_table_column",
                "table_start_index": 10,
                "column_index": 2,
                "insert_right": True,
            },
            "insert_table_column",
        )
        inner = request["insertTableColumn"]
        assert inner["insertRight"] is True
        assert inner["tableCellLocation"]["columnIndex"] == 2
        assert "right of" in desc
        assert "column 2" in desc
        assert "10" in desc

    def test_build_insert_table_column_left(self, manager):
        request, desc = manager._build_operation_request(
            {
                "type": "insert_table_column",
                "table_start_index": 10,
                "column_index": 1,
                "insert_right": False,
            },
            "insert_table_column",
        )
        inner = request["insertTableColumn"]
        assert inner["insertRight"] is False
        assert "left of" in desc

    def test_build_delete_table_column_request(self, manager):
        request, desc = manager._build_operation_request(
            {
                "type": "delete_table_column",
                "table_start_index": 10,
                "column_index": 3,
            },
            "delete_table_column",
        )
        inner = request["deleteTableColumn"]
        assert inner["tableCellLocation"]["columnIndex"] == 3
        assert "delete" in desc
        assert "column 3" in desc
        assert "10" in desc

    @pytest.mark.asyncio
    async def test_end_to_end_insert_table_column(self, manager):
        manager._execute_batch_requests = AsyncMock(return_value={"replies": [{}]})
        success, _, meta = await manager.execute_batch_operations(
            "doc-123",
            [
                {
                    "type": "insert_table_column",
                    "table_start_index": 10,
                    "column_index": 2,
                }
            ],
        )
        assert success
        assert meta["operations_count"] == 1

    @pytest.mark.asyncio
    async def test_end_to_end_delete_table_column(self, manager):
        manager._execute_batch_requests = AsyncMock(return_value={"replies": [{}]})
        success, _, meta = await manager.execute_batch_operations(
            "doc-123",
            [
                {
                    "type": "delete_table_column",
                    "table_start_index": 10,
                    "column_index": 1,
                }
            ],
        )
        assert success
        assert meta["operations_count"] == 1

    def test_supported_operations_include_insert_table_column(self, manager):
        supported = manager.get_supported_operations()["supported_operations"]
        assert "insert_table_column" in supported
        assert supported["insert_table_column"]["required"] == [
            "table_start_index",
            "column_index",
        ]
        assert "insert_right" in supported["insert_table_column"]["optional"]

    def test_supported_operations_include_delete_table_column(self, manager):
        supported = manager.get_supported_operations()["supported_operations"]
        assert "delete_table_column" in supported
        assert supported["delete_table_column"]["required"] == [
            "table_start_index",
            "column_index",
        ]
