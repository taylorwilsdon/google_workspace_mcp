"""
Tests for update_table_row_style / pin_table_header_rows operations in
batch_update_doc, and the header_rows convenience on create_table_with_data.

Covers helper construction, validate_operation, batch manager integration,
and the create_table_with_data one-shot header path.

TableRowStyle reports whether a row is a header, but the Docs API rejects a
"tableHeader" field on updateTableRowStyle with a 400 error ("Unallowed field:
tableHeader"), confirmed against the live API. The writable mechanism is the
dedicated pinTableHeaderRows request (pinnedHeaderRowsCount).
"""

from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from gdocs import docs_tools
from gdocs.docs_helpers import (
    create_pin_table_header_rows_request,
    create_update_table_row_style_request,
    validate_operation,
)
from gdocs.operation_schemas import PinTableHeaderRowsOperation


def _unwrap(tool):
    """Unwrap the decorated tool function to the original implementation."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class TestCreateUpdateTableRowStyleRequest:
    def test_min_row_height_only(self):
        result = create_update_table_row_style_request(
            table_start_index=5,
            row_indices=[1, 2],
            min_row_height=24.0,
        )
        inner = result["updateTableRowStyle"]
        assert inner["tableStartLocation"] == {"index": 5}
        assert inner["rowIndices"] == [1, 2]
        assert inner["tableRowStyle"]["minRowHeight"] == {
            "magnitude": 24.0,
            "unit": "PT",
        }
        assert inner["fields"] == "minRowHeight"

    def test_with_tab_id(self):
        result = create_update_table_row_style_request(
            table_start_index=8,
            row_indices=[0],
            min_row_height=30.5,
            tab_id="t.abc123",
        )
        location = result["updateTableRowStyle"]["tableStartLocation"]
        assert location == {"index": 8, "tabId": "t.abc123"}

    def test_no_tab_id_excluded(self):
        result = create_update_table_row_style_request(
            table_start_index=10,
            row_indices=[0],
            min_row_height=20.0,
        )
        location = result["updateTableRowStyle"]["tableStartLocation"]
        assert "tabId" not in location

    def test_returns_none_when_no_properties(self):
        result = create_update_table_row_style_request(
            table_start_index=10,
            row_indices=[0],
        )
        assert result is None


class TestCreatePinTableHeaderRowsRequest:
    def test_basic_construction(self):
        result = create_pin_table_header_rows_request(
            table_start_index=10,
            pinned_header_rows_count=1,
        )
        inner = result["pinTableHeaderRows"]
        assert inner["tableStartLocation"] == {"index": 10}
        assert inner["pinnedHeaderRowsCount"] == 1

    def test_zero_unpins_all_rows(self):
        result = create_pin_table_header_rows_request(
            table_start_index=10,
            pinned_header_rows_count=0,
        )
        assert result["pinTableHeaderRows"]["pinnedHeaderRowsCount"] == 0

    def test_with_tab_id(self):
        result = create_pin_table_header_rows_request(
            table_start_index=8,
            pinned_header_rows_count=1,
            tab_id="t.abc123",
        )
        location = result["pinTableHeaderRows"]["tableStartLocation"]
        assert location == {"index": 8, "tabId": "t.abc123"}

    def test_no_tab_id_excluded(self):
        result = create_pin_table_header_rows_request(
            table_start_index=10,
            pinned_header_rows_count=1,
        )
        location = result["pinTableHeaderRows"]["tableStartLocation"]
        assert "tabId" not in location

    def test_negative_count_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            create_pin_table_header_rows_request(
                table_start_index=10,
                pinned_header_rows_count=-1,
            )


class TestValidateOperation:
    def test_valid_update_table_row_style(self):
        is_valid, msg = validate_operation(
            {
                "type": "update_table_row_style",
                "table_start_index": 10,
                "row_indices": [0],
            }
        )
        assert is_valid, msg

    def test_missing_row_indices(self):
        is_valid, msg = validate_operation(
            {
                "type": "update_table_row_style",
                "table_start_index": 10,
            }
        )
        assert not is_valid
        assert "row_indices" in msg

    def test_missing_table_start_index(self):
        is_valid, msg = validate_operation(
            {
                "type": "update_table_row_style",
                "row_indices": [0],
            }
        )
        assert not is_valid
        assert "table_start_index" in msg

    def test_valid_pin_table_header_rows(self):
        is_valid, msg = validate_operation(
            {
                "type": "pin_table_header_rows",
                "table_start_index": 10,
                "pinned_header_rows_count": 1,
            }
        )
        assert is_valid, msg

    def test_missing_pinned_header_rows_count(self):
        is_valid, msg = validate_operation(
            {
                "type": "pin_table_header_rows",
                "table_start_index": 10,
            }
        )
        assert not is_valid
        assert "pinned_header_rows_count" in msg

    def test_negative_pinned_header_rows_count_rejected_by_schema(self):
        with pytest.raises(ValidationError):
            PinTableHeaderRowsOperation(
                type="pin_table_header_rows",
                table_start_index=10,
                pinned_header_rows_count=-1,
            )


class TestBatchManagerIntegration:
    @pytest.fixture()
    def manager(self):
        from gdocs.managers.batch_operation_manager import BatchOperationManager

        return BatchOperationManager(Mock())

    def test_build_update_table_row_style_request(self, manager):
        request, desc = manager._build_operation_request(
            {
                "type": "update_table_row_style",
                "table_start_index": 10,
                "row_indices": [0],
                "min_row_height": 24.0,
            },
            "update_table_row_style",
        )
        inner = request["updateTableRowStyle"]
        assert inner["tableStartLocation"] == {"index": 10}
        assert inner["rowIndices"] == [0]
        assert inner["tableRowStyle"]["minRowHeight"] == {
            "magnitude": 24.0,
            "unit": "PT",
        }
        assert "[0]" in desc
        assert "10" in desc

    def test_build_pin_table_header_rows_request(self, manager):
        request, desc = manager._build_operation_request(
            {
                "type": "pin_table_header_rows",
                "table_start_index": 10,
                "pinned_header_rows_count": 1,
            },
            "pin_table_header_rows",
        )
        inner = request["pinTableHeaderRows"]
        assert inner["tableStartLocation"] == {"index": 10}
        assert inner["pinnedHeaderRowsCount"] == 1
        assert "10" in desc

    @pytest.mark.asyncio
    async def test_end_to_end_update_table_row_style(self, manager):
        manager._execute_batch_requests = AsyncMock(return_value={"replies": [{}]})
        success, _, meta = await manager.execute_batch_operations(
            "doc-123",
            [
                {
                    "type": "update_table_row_style",
                    "table_start_index": 10,
                    "row_indices": [0],
                    "min_row_height": 24.0,
                }
            ],
        )
        assert success
        assert meta["operations_count"] == 1

    @pytest.mark.asyncio
    async def test_end_to_end_pin_table_header_rows(self, manager):
        manager._execute_batch_requests = AsyncMock(return_value={"replies": [{}]})
        success, _, meta = await manager.execute_batch_operations(
            "doc-123",
            [
                {
                    "type": "pin_table_header_rows",
                    "table_start_index": 10,
                    "pinned_header_rows_count": 1,
                }
            ],
        )
        assert success
        assert meta["operations_count"] == 1

    def test_no_properties_raises_value_error(self, manager):
        with pytest.raises(ValueError, match="at least one of"):
            manager._build_operation_request(
                {
                    "type": "update_table_row_style",
                    "table_start_index": 10,
                    "row_indices": [0],
                },
                "update_table_row_style",
            )

    def test_supported_operations_include_update_table_row_style(self, manager):
        supported = manager.get_supported_operations()["supported_operations"]
        assert "update_table_row_style" in supported
        op = supported["update_table_row_style"]
        assert "table_start_index" in op["required"]
        assert "row_indices" in op["required"]
        assert "min_row_height" in op["optional"]
        assert "tab_id" in op["optional"]
        assert "table_header" not in op["optional"]

    def test_supported_operations_include_pin_table_header_rows(self, manager):
        supported = manager.get_supported_operations()["supported_operations"]
        assert "pin_table_header_rows" in supported
        op = supported["pin_table_header_rows"]
        assert "table_start_index" in op["required"]
        assert "pinned_header_rows_count" in op["required"]
        assert "tab_id" in op["optional"]


class TestCreateTableWithDataHeaderRows:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("results", "expected_index"),
        [
            ([(True, "created", {"rows": 1, "columns": 1})], 10),
            (
                [
                    (False, "must be less than the end index", {}),
                    (True, "created", {"rows": 1, "columns": 1}),
                ],
                9,
            ),
        ],
    )
    async def test_success_reports_effective_index(
        self, monkeypatch, results, expected_index
    ):
        create_and_populate = AsyncMock(side_effect=results)
        manager = Mock(create_and_populate_table=create_and_populate)
        monkeypatch.setattr(
            docs_tools, "TableOperationManager", Mock(return_value=manager)
        )

        result = await _unwrap(docs_tools.create_table_with_data)(
            service=Mock(),
            user_google_email="user@example.com",
            document_id="d" * 25,
            table_data=[["value"]],
            index=10,
        )

        assert f"Index: {expected_index}." in result

    @pytest.mark.asyncio
    @pytest.mark.parametrize("header_rows", [-1, 3])
    async def test_invalid_header_rows_rejected_before_document_mutation(
        self, header_rows
    ):
        service = Mock()

        result = await _unwrap(docs_tools.create_table_with_data)(
            service=service,
            user_google_email="user@example.com",
            document_id="d" * 25,
            table_data=[["H1"], ["value"]],
            index=1,
            header_rows=header_rows,
        )

        assert result == (
            "ERROR: header_rows must be between 0 and the table row count (2)"
        )
        service.documents.assert_not_called()

    @pytest.mark.asyncio
    async def test_header_rows_applies_repeating_header(self):
        """create_table_with_data(header_rows=1) sends a pinTableHeaderRows request."""
        service = Mock()
        docs = service.documents.return_value

        # insertTable + populate batches succeed; the doc.get() returns one table
        # whose start_index matches the requested insertion index.
        docs.batchUpdate.return_value.execute.return_value = {"replies": [{}]}
        docs.get.return_value.execute.return_value = {
            "body": {
                "content": [
                    {
                        "startIndex": 1,
                        "endIndex": 40,
                        "table": {
                            "rows": 2,
                            "columns": 2,
                            "tableRows": [
                                {
                                    "tableCells": [
                                        {
                                            "startIndex": 2,
                                            "endIndex": 5,
                                            "content": [
                                                {"startIndex": 3, "paragraph": {}}
                                            ],
                                        },
                                        {
                                            "startIndex": 5,
                                            "endIndex": 8,
                                            "content": [
                                                {"startIndex": 6, "paragraph": {}}
                                            ],
                                        },
                                    ]
                                },
                                {
                                    "tableCells": [
                                        {
                                            "startIndex": 8,
                                            "endIndex": 11,
                                            "content": [
                                                {"startIndex": 9, "paragraph": {}}
                                            ],
                                        },
                                        {
                                            "startIndex": 11,
                                            "endIndex": 14,
                                            "content": [
                                                {"startIndex": 12, "paragraph": {}}
                                            ],
                                        },
                                    ]
                                },
                            ],
                        },
                    }
                ]
            }
        }

        result = await _unwrap(docs_tools.create_table_with_data)(
            service=service,
            user_google_email="user@example.com",
            document_id="d" * 25,
            table_data=[["H1", "H2"], ["a", "b"]],
            index=1,
            header_rows=1,
        )

        assert result.startswith("SUCCESS")

        # Collect every batchUpdate request body sent to the service.
        sent_requests = []
        for call in docs.batchUpdate.call_args_list:
            sent_requests.extend(call.kwargs["body"]["requests"])

        header_requests = [r for r in sent_requests if "pinTableHeaderRows" in r]
        assert len(header_requests) == 1
        inner = header_requests[0]["pinTableHeaderRows"]
        assert inner["pinnedHeaderRowsCount"] == 1
        assert docs.batchUpdate.call_count == 2
        population_requests = docs.batchUpdate.call_args_list[1].kwargs["body"][
            "requests"
        ]
        assert any("insertText" in request for request in population_requests)
        assert any("pinTableHeaderRows" in request for request in population_requests)

    @pytest.mark.asyncio
    async def test_header_pinning_failure_returns_partial_success(self):
        """A failed combined batch must not invite callers to recreate the table."""
        service = Mock()
        docs = service.documents.return_value
        docs.batchUpdate.return_value.execute.side_effect = [
            {"replies": [{}]},
            RuntimeError("pinTableHeaderRows rejected"),
        ]
        docs.get.return_value.execute.return_value = {
            "body": {
                "content": [
                    {
                        "startIndex": 1,
                        "endIndex": 20,
                        "table": {
                            "rows": 1,
                            "columns": 1,
                            "tableRows": [
                                {
                                    "tableCells": [
                                        {
                                            "startIndex": 2,
                                            "endIndex": 5,
                                            "content": [
                                                {"startIndex": 3, "paragraph": {}}
                                            ],
                                        }
                                    ]
                                }
                            ],
                        },
                    }
                ]
            }
        }

        result = await _unwrap(docs_tools.create_table_with_data)(
            service=service,
            user_google_email="user@example.com",
            document_id="d" * 25,
            table_data=[["Header"]],
            index=1,
            header_rows=1,
        )

        assert result.startswith("PARTIAL SUCCESS:")
        assert "Table was created" in result
        assert "Do not retry table creation" in result
        assert docs.batchUpdate.call_count == 2
        failed_batch = docs.batchUpdate.call_args_list[1].kwargs["body"]["requests"]
        assert any("insertText" in request for request in failed_batch)
        assert any("pinTableHeaderRows" in request for request in failed_batch)

    @pytest.mark.asyncio
    async def test_no_header_rows_by_default(self):
        """Without header_rows, no pinTableHeaderRows request is sent."""
        service = Mock()
        docs = service.documents.return_value
        docs.batchUpdate.return_value.execute.return_value = {"replies": [{}]}
        docs.get.return_value.execute.return_value = {
            "body": {
                "content": [
                    {
                        "startIndex": 1,
                        "endIndex": 20,
                        "table": {
                            "rows": 1,
                            "columns": 1,
                            "tableRows": [
                                {
                                    "tableCells": [
                                        {
                                            "startIndex": 2,
                                            "endIndex": 5,
                                            "content": [
                                                {"startIndex": 3, "paragraph": {}}
                                            ],
                                        }
                                    ]
                                }
                            ],
                        },
                    }
                ]
            }
        }

        await _unwrap(docs_tools.create_table_with_data)(
            service=service,
            user_google_email="user@example.com",
            document_id="d" * 25,
            table_data=[["only"]],
            index=1,
        )

        sent_requests = []
        for call in docs.batchUpdate.call_args_list:
            sent_requests.extend(call.kwargs["body"]["requests"])
        assert not any("pinTableHeaderRows" in r for r in sent_requests)
