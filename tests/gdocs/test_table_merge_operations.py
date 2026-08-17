"""
Tests for merge_table_cells and unmerge_table_cells operations in batch_update_doc.

Covers helper construction, validate_operation, batch manager integration.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from gdocs.docs_helpers import (
    create_merge_table_cells_request,
    create_unmerge_table_cells_request,
    validate_operation,
)


class TestCreateMergeTableCellsRequest:
    def test_basic_merge(self):
        result = create_merge_table_cells_request(
            table_start_index=10,
            row_index=0,
            column_index=1,
            row_span=2,
            column_span=3,
        )
        inner = result["mergeTableCells"]["tableRange"]
        assert inner["tableCellLocation"]["tableStartLocation"] == {"index": 10}
        assert inner["tableCellLocation"]["rowIndex"] == 0
        assert inner["tableCellLocation"]["columnIndex"] == 1
        assert inner["rowSpan"] == 2
        assert inner["columnSpan"] == 3

    def test_with_tab_id(self):
        result = create_merge_table_cells_request(
            table_start_index=5,
            row_index=1,
            column_index=0,
            row_span=1,
            column_span=2,
            tab_id="t.abc123",
        )
        location = result["mergeTableCells"]["tableRange"]["tableCellLocation"][
            "tableStartLocation"
        ]
        assert location == {"index": 5, "tabId": "t.abc123"}

    def test_no_tab_id_excluded(self):
        result = create_merge_table_cells_request(
            table_start_index=10,
            row_index=0,
            column_index=0,
            row_span=2,
            column_span=2,
        )
        location = result["mergeTableCells"]["tableRange"]["tableCellLocation"][
            "tableStartLocation"
        ]
        assert "tabId" not in location

    def test_structure_keys(self):
        result = create_merge_table_cells_request(
            table_start_index=10,
            row_index=0,
            column_index=0,
            row_span=1,
            column_span=1,
        )
        assert "mergeTableCells" in result
        assert "tableRange" in result["mergeTableCells"]


class TestCreateUnmergeTableCellsRequest:
    def test_basic_unmerge(self):
        result = create_unmerge_table_cells_request(
            table_start_index=10,
            row_index=2,
            column_index=0,
            row_span=3,
            column_span=2,
        )
        inner = result["unmergeTableCells"]["tableRange"]
        assert inner["tableCellLocation"]["tableStartLocation"] == {"index": 10}
        assert inner["tableCellLocation"]["rowIndex"] == 2
        assert inner["tableCellLocation"]["columnIndex"] == 0
        assert inner["rowSpan"] == 3
        assert inner["columnSpan"] == 2

    def test_with_tab_id(self):
        result = create_unmerge_table_cells_request(
            table_start_index=15,
            row_index=0,
            column_index=1,
            row_span=2,
            column_span=2,
            tab_id="t.xyz",
        )
        location = result["unmergeTableCells"]["tableRange"]["tableCellLocation"][
            "tableStartLocation"
        ]
        assert location == {"index": 15, "tabId": "t.xyz"}

    def test_structure_keys(self):
        result = create_unmerge_table_cells_request(
            table_start_index=10,
            row_index=0,
            column_index=0,
            row_span=1,
            column_span=1,
        )
        assert "unmergeTableCells" in result
        assert "tableRange" in result["unmergeTableCells"]


class TestValidateOperation:
    def test_valid_merge_table_cells(self):
        is_valid, msg = validate_operation(
            {
                "type": "merge_table_cells",
                "table_start_index": 10,
                "row_index": 0,
                "column_index": 1,
                "row_span": 2,
                "column_span": 3,
            }
        )
        assert is_valid, msg

    def test_merge_missing_row_span(self):
        is_valid, msg = validate_operation(
            {
                "type": "merge_table_cells",
                "table_start_index": 10,
                "row_index": 0,
                "column_index": 1,
                "column_span": 3,
            }
        )
        assert not is_valid
        assert "row_span" in msg

    def test_merge_missing_column_index(self):
        is_valid, msg = validate_operation(
            {
                "type": "merge_table_cells",
                "table_start_index": 10,
                "row_index": 0,
                "row_span": 2,
                "column_span": 3,
            }
        )
        assert not is_valid
        assert "column_index" in msg

    def test_valid_unmerge_table_cells(self):
        is_valid, msg = validate_operation(
            {
                "type": "unmerge_table_cells",
                "table_start_index": 10,
                "row_index": 0,
                "column_index": 1,
                "row_span": 2,
                "column_span": 3,
            }
        )
        assert is_valid, msg

    def test_unmerge_missing_table_start_index(self):
        is_valid, msg = validate_operation(
            {
                "type": "unmerge_table_cells",
                "row_index": 0,
                "column_index": 1,
                "row_span": 2,
                "column_span": 3,
            }
        )
        assert not is_valid
        assert "table_start_index" in msg

    def test_unmerge_missing_column_span(self):
        is_valid, msg = validate_operation(
            {
                "type": "unmerge_table_cells",
                "table_start_index": 10,
                "row_index": 0,
                "column_index": 1,
                "row_span": 2,
            }
        )
        assert not is_valid
        assert "column_span" in msg


class TestBatchManagerIntegration:
    @pytest.fixture()
    def manager(self):
        from gdocs.managers.batch_operation_manager import BatchOperationManager

        return BatchOperationManager(Mock())

    def test_build_merge_table_cells_request(self, manager):
        request, desc = manager._build_operation_request(
            {
                "type": "merge_table_cells",
                "table_start_index": 10,
                "row_index": 0,
                "column_index": 1,
                "row_span": 2,
                "column_span": 3,
            },
            "merge_table_cells",
        )
        inner = request["mergeTableCells"]["tableRange"]
        assert inner["tableCellLocation"]["rowIndex"] == 0
        assert inner["tableCellLocation"]["columnIndex"] == 1
        assert inner["rowSpan"] == 2
        assert inner["columnSpan"] == 3
        assert "merge" in desc
        assert "(0,1)" in desc
        assert "2x3" in desc
        assert "10" in desc

    def test_build_unmerge_table_cells_request(self, manager):
        request, desc = manager._build_operation_request(
            {
                "type": "unmerge_table_cells",
                "table_start_index": 15,
                "row_index": 2,
                "column_index": 0,
                "row_span": 3,
                "column_span": 2,
            },
            "unmerge_table_cells",
        )
        inner = request["unmergeTableCells"]["tableRange"]
        assert inner["tableCellLocation"]["rowIndex"] == 2
        assert inner["rowSpan"] == 3
        assert inner["columnSpan"] == 2
        assert "unmerge" in desc
        assert "(2,0)" in desc
        assert "3x2" in desc
        assert "15" in desc

    @pytest.mark.asyncio
    async def test_end_to_end_merge_table_cells(self, manager):
        manager._execute_batch_requests = AsyncMock(return_value={"replies": [{}]})
        success, _, meta = await manager.execute_batch_operations(
            "doc-123",
            [
                {
                    "type": "merge_table_cells",
                    "table_start_index": 10,
                    "row_index": 0,
                    "column_index": 1,
                    "row_span": 2,
                    "column_span": 3,
                }
            ],
        )
        assert success
        assert meta["operations_count"] == 1

    @pytest.mark.asyncio
    async def test_end_to_end_unmerge_table_cells(self, manager):
        manager._execute_batch_requests = AsyncMock(return_value={"replies": [{}]})
        success, _, meta = await manager.execute_batch_operations(
            "doc-123",
            [
                {
                    "type": "unmerge_table_cells",
                    "table_start_index": 10,
                    "row_index": 0,
                    "column_index": 1,
                    "row_span": 2,
                    "column_span": 3,
                }
            ],
        )
        assert success
        assert meta["operations_count"] == 1

    def test_supported_operations_include_merge_table_cells(self, manager):
        supported = manager.get_supported_operations()["supported_operations"]
        assert "merge_table_cells" in supported
        required = supported["merge_table_cells"]["required"]
        assert "table_start_index" in required
        assert "row_index" in required
        assert "column_index" in required
        assert "row_span" in required
        assert "column_span" in required

    def test_supported_operations_include_unmerge_table_cells(self, manager):
        supported = manager.get_supported_operations()["supported_operations"]
        assert "unmerge_table_cells" in supported
        required = supported["unmerge_table_cells"]["required"]
        assert "table_start_index" in required
        assert "row_span" in required
        assert "column_span" in required
