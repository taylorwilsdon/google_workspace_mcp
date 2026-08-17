"""
Tests for update_table_column_properties operation in batch_update_doc.

Covers helper construction, validate_operation, batch manager integration.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from gdocs.docs_helpers import (
    create_update_table_column_properties_request,
    validate_operation,
)


class TestCreateUpdateTableColumnPropertiesRequest:
    def test_fixed_width(self):
        result = create_update_table_column_properties_request(
            table_start_index=10,
            column_indices=[0, 1],
            width=72.0,
            width_type="FIXED_WIDTH",
        )
        inner = result["updateTableColumnProperties"]
        assert inner["tableStartLocation"] == {"index": 10}
        assert inner["columnIndices"] == [0, 1]
        assert inner["tableColumnProperties"]["width"] == {
            "magnitude": 72.0,
            "unit": "PT",
        }
        assert inner["tableColumnProperties"]["widthType"] == "FIXED_WIDTH"
        assert "width" in inner["fields"]
        assert "widthType" in inner["fields"]

    def test_evenly_distributed(self):
        result = create_update_table_column_properties_request(
            table_start_index=5,
            column_indices=[2],
            width_type="EVENLY_DISTRIBUTED",
        )
        inner = result["updateTableColumnProperties"]
        assert inner["tableColumnProperties"]["widthType"] == "EVENLY_DISTRIBUTED"
        assert "widthType" in inner["fields"]
        assert "width" not in inner["tableColumnProperties"]

    def test_with_tab_id(self):
        result = create_update_table_column_properties_request(
            table_start_index=8,
            column_indices=[0],
            width=100.0,
            tab_id="t.abc123",
        )
        location = result["updateTableColumnProperties"]["tableStartLocation"]
        assert location == {"index": 8, "tabId": "t.abc123"}

    def test_width_only(self):
        result = create_update_table_column_properties_request(
            table_start_index=3,
            column_indices=[1, 2, 3],
            width=50.5,
        )
        inner = result["updateTableColumnProperties"]
        assert inner["tableColumnProperties"]["width"] == {
            "magnitude": 50.5,
            "unit": "PT",
        }
        assert inner["fields"] == "width"
        assert "widthType" not in inner["tableColumnProperties"]

    def test_no_tab_id_excluded(self):
        result = create_update_table_column_properties_request(
            table_start_index=10,
            column_indices=[0],
            width=60.0,
        )
        location = result["updateTableColumnProperties"]["tableStartLocation"]
        assert "tabId" not in location

    def test_structure_keys(self):
        result = create_update_table_column_properties_request(
            table_start_index=10,
            column_indices=[0],
            width=60.0,
        )
        assert "updateTableColumnProperties" in result
        inner = result["updateTableColumnProperties"]
        assert "tableStartLocation" in inner
        assert "columnIndices" in inner
        assert "tableColumnProperties" in inner
        assert "fields" in inner

    def test_returns_none_when_no_properties(self):
        result = create_update_table_column_properties_request(
            table_start_index=10,
            column_indices=[0],
        )
        assert result is None


class TestValidateOperation:
    def test_valid_update_table_column_properties(self):
        is_valid, msg = validate_operation(
            {
                "type": "update_table_column_properties",
                "table_start_index": 10,
                "column_indices": [0, 1],
            }
        )
        assert is_valid, msg

    def test_missing_column_indices(self):
        is_valid, msg = validate_operation(
            {
                "type": "update_table_column_properties",
                "table_start_index": 10,
            }
        )
        assert not is_valid
        assert "column_indices" in msg

    def test_missing_table_start_index(self):
        is_valid, msg = validate_operation(
            {
                "type": "update_table_column_properties",
                "column_indices": [0],
            }
        )
        assert not is_valid
        assert "table_start_index" in msg


class TestBatchManagerIntegration:
    @pytest.fixture()
    def manager(self):
        from gdocs.managers.batch_operation_manager import BatchOperationManager

        return BatchOperationManager(Mock())

    def test_build_update_table_column_properties_request(self, manager):
        request, desc = manager._build_operation_request(
            {
                "type": "update_table_column_properties",
                "table_start_index": 10,
                "column_indices": [0, 2],
                "width": 80.0,
                "width_type": "FIXED_WIDTH",
            },
            "update_table_column_properties",
        )
        inner = request["updateTableColumnProperties"]
        assert inner["tableStartLocation"] == {"index": 10}
        assert inner["columnIndices"] == [0, 2]
        assert inner["tableColumnProperties"]["width"]["magnitude"] == 80.0
        assert "[0, 2]" in desc
        assert "10" in desc

    @pytest.mark.asyncio
    async def test_end_to_end_update_table_column_properties(self, manager):
        manager._execute_batch_requests = AsyncMock(return_value={"replies": [{}]})
        success, _, meta = await manager.execute_batch_operations(
            "doc-123",
            [
                {
                    "type": "update_table_column_properties",
                    "table_start_index": 10,
                    "column_indices": [1],
                    "width": 72.0,
                    "width_type": "FIXED_WIDTH",
                }
            ],
        )
        assert success
        assert meta["operations_count"] == 1

    def test_no_properties_raises_value_error(self, manager):
        with pytest.raises(ValueError, match="at least one of"):
            manager._build_operation_request(
                {
                    "type": "update_table_column_properties",
                    "table_start_index": 10,
                    "column_indices": [0],
                },
                "update_table_column_properties",
            )

    def test_supported_operations_include_update_table_column_properties(self, manager):
        supported = manager.get_supported_operations()["supported_operations"]
        assert "update_table_column_properties" in supported
        op = supported["update_table_column_properties"]
        assert "table_start_index" in op["required"]
        assert "column_indices" in op["required"]
        assert "width" in op["optional"]
        assert "width_type" in op["optional"]
        assert "tab_id" in op["optional"]
