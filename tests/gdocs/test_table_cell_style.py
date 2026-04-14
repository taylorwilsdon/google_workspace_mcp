"""
Tests for table cell style support in batch_update_doc.

Covers helper construction, validation, batch manager integration, and public
tool wiring for updateTableCellStyle requests.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from gdocs import docs_tools
from gdocs.docs_helpers import (
    build_table_cell_style,
    create_update_table_cell_style_request,
)
from gdocs.managers.validation_manager import ValidationManager


def _unwrap(tool):
    """Unwrap the decorated tool function to the original implementation."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class TestBuildTableCellStyle:
    def test_background_color_only(self):
        style, fields = build_table_cell_style(background_color="#D9EAD3")
        assert style["backgroundColor"]["color"]["rgbColor"] == {
            "red": 217 / 255,
            "green": 234 / 255,
            "blue": 211 / 255,
        }
        assert fields == ["backgroundColor"]

    def test_border_color_and_width_apply_to_all_sides(self):
        style, fields = build_table_cell_style(
            border_color="#FF0000",
            border_width=1.5,
        )
        for border_name in ("borderTop", "borderBottom", "borderLeft", "borderRight"):
            assert style[border_name]["width"] == {"magnitude": 1.5, "unit": "PT"}
            assert style[border_name]["color"]["color"]["rgbColor"] == {
                "red": 1.0,
                "green": 0.0,
                "blue": 0.0,
            }
        assert fields == ["borderTop", "borderBottom", "borderLeft", "borderRight"]


class TestCreateUpdateTableCellStyleRequest:
    def test_entire_table_request(self):
        result = create_update_table_cell_style_request(
            table_start_index=42,
            background_color="#D9EAD3",
            tab_id="t.abc",
        )
        inner = result["updateTableCellStyle"]
        assert inner["tableStartLocation"] == {"index": 42, "tabId": "t.abc"}
        assert inner["fields"] == "backgroundColor"

    def test_range_request_defaults_spans_to_one(self):
        result = create_update_table_cell_style_request(
            table_start_index=42,
            background_color="#D9EAD3",
            row_index=1,
            column_index=2,
        )
        inner = result["updateTableCellStyle"]
        assert inner["tableRange"] == {
            "tableCellLocation": {
                "tableStartLocation": {"index": 42},
                "rowIndex": 1,
                "columnIndex": 2,
            },
            "rowSpan": 1,
            "columnSpan": 1,
        }


class TestValidateTableCellStyle:
    @pytest.fixture()
    def vm(self):
        return ValidationManager()

    def test_valid_background_color_for_entire_table(self, vm):
        assert vm.validate_table_cell_style_params(background_color="#D9EAD3")[0]

    def test_range_requires_both_coordinates(self, vm):
        is_valid, msg = vm.validate_table_cell_style_params(
            background_color="#D9EAD3",
            row_index=0,
        )
        assert not is_valid
        assert "row_index and column_index" in msg

    def test_batch_validation_accepts_table_cell_style_operation(self, vm):
        ops = [
            {
                "type": "update_table_cell_style",
                "table_start_index": 42,
                "row_index": 0,
                "column_index": 0,
                "column_span": 3,
                "background_color": "#D9EAD3",
            }
        ]
        assert vm.validate_batch_operations(ops)[0]


class TestBuildTableCellStylePadding:
    def test_padding_all_sides(self):
        style, fields = build_table_cell_style(
            padding_top=4.0,
            padding_bottom=4.0,
            padding_left=8.0,
            padding_right=8.0,
        )
        assert style["paddingTop"] == {"magnitude": 4.0, "unit": "PT"}
        assert style["paddingBottom"] == {"magnitude": 4.0, "unit": "PT"}
        assert style["paddingLeft"] == {"magnitude": 8.0, "unit": "PT"}
        assert style["paddingRight"] == {"magnitude": 8.0, "unit": "PT"}
        assert fields == ["paddingTop", "paddingBottom", "paddingLeft", "paddingRight"]

    def test_content_alignment(self):
        style, fields = build_table_cell_style(content_alignment="MIDDLE")
        assert style["contentAlignment"] == "MIDDLE"
        assert "contentAlignment" in fields

    def test_combined_background_and_padding(self):
        style, fields = build_table_cell_style(
            background_color="#FF0000",
            padding_top=2.0,
            content_alignment="TOP",
        )
        assert "backgroundColor" in style
        assert style["paddingTop"] == {"magnitude": 2.0, "unit": "PT"}
        assert style["contentAlignment"] == "TOP"
        assert "backgroundColor" in fields
        assert "paddingTop" in fields
        assert "contentAlignment" in fields


class TestValidateTableCellStylePaddingAndAlignment:
    @pytest.fixture()
    def vm(self):
        return ValidationManager()

    def test_padding_only_is_valid(self, vm):
        is_valid, msg = vm.validate_table_cell_style_params(padding_left=5.0)
        assert is_valid, msg

    def test_content_alignment_only_is_valid(self, vm):
        is_valid, msg = vm.validate_table_cell_style_params(content_alignment="BOTTOM")
        assert is_valid, msg

    def test_invalid_content_alignment(self, vm):
        is_valid, msg = vm.validate_table_cell_style_params(content_alignment="CENTER")
        assert not is_valid
        assert "TOP" in msg or "MIDDLE" in msg or "BOTTOM" in msg

    def test_negative_padding_rejected(self, vm):
        is_valid, msg = vm.validate_table_cell_style_params(padding_top=-1.0)
        assert not is_valid
        assert "non-negative" in msg

    def test_zero_padding_is_valid(self, vm):
        is_valid, msg = vm.validate_table_cell_style_params(padding_top=0.0)
        assert is_valid, msg

    def test_bool_padding_rejected(self, vm):
        is_valid, msg = vm.validate_table_cell_style_params(padding_top=True)
        assert not is_valid
        assert "must be a number" in msg

    def test_no_style_params_rejected(self, vm):
        is_valid, msg = vm.validate_table_cell_style_params()
        assert not is_valid
        assert "At least one" in msg


class TestBatchManagerIntegration:
    @pytest.fixture()
    def manager(self):
        from gdocs.managers.batch_operation_manager import BatchOperationManager

        return BatchOperationManager(Mock())

    def test_build_request_for_table_cell_background(self, manager):
        request, desc = manager._build_operation_request(
            {
                "type": "update_table_cell_style",
                "table_start_index": 42,
                "row_index": 0,
                "column_index": 0,
                "column_span": 3,
                "background_color": "#D9EAD3",
            },
            "update_table_cell_style",
        )
        inner = request["updateTableCellStyle"]
        assert inner["tableRange"]["columnSpan"] == 3
        assert "backgroundColor" in inner["fields"]
        assert "table cell style at 42" in desc

    def test_build_request_rejects_invalid_table_cell_style_params(self, manager):
        with pytest.raises(ValueError, match="border_width must be positive"):
            manager._build_operation_request(
                {
                    "type": "update_table_cell_style",
                    "table_start_index": 42,
                    "border_width": -1,
                },
                "update_table_cell_style",
            )

    @pytest.mark.asyncio
    async def test_end_to_end_execute_table_cell_style(self, manager):
        manager._execute_batch_requests = AsyncMock(return_value={"replies": [{}]})
        success, _, meta = await manager.execute_batch_operations(
            "doc-123",
            [
                {
                    "type": "update_table_cell_style",
                    "table_start_index": 42,
                    "background_color": "#D9EAD3",
                }
            ],
        )
        assert success
        assert meta["operations_count"] == 1

    @pytest.mark.asyncio
    async def test_end_to_end_surfaces_invalid_table_cell_style_params(self, manager):
        success, message, meta = await manager.execute_batch_operations(
            "doc-123",
            [
                {
                    "type": "update_table_cell_style",
                    "table_start_index": 42,
                    "border_width": -1,
                }
            ],
        )
        assert not success
        assert "border_width must be positive" in message
        assert meta == {}

    def test_supported_operations_include_table_cell_style(self, manager):
        supported = manager.get_supported_operations()["supported_operations"]
        assert "update_table_cell_style" in supported
        assert supported["update_table_cell_style"]["required"] == ["table_start_index"]


class TestPublicToolWiring:
    @pytest.fixture()
    def service(self):
        mock_service = Mock()
        mock_service.documents().batchUpdate().execute.return_value = {"replies": [{}]}
        return mock_service

    @pytest.mark.asyncio
    async def test_batch_update_doc_public_tool_includes_table_cell_style_request(
        self, service
    ):
        await _unwrap(docs_tools.batch_update_doc)(
            service=service,
            user_google_email="user@example.com",
            document_id="b" * 25,
            operations=[
                {
                    "type": "update_table_cell_style",
                    "table_start_index": 42,
                    "row_index": 0,
                    "column_index": 0,
                    "column_span": 3,
                    "background_color": "#D9EAD3",
                }
            ],
        )

        call_kwargs = service.documents.return_value.batchUpdate.call_args.kwargs
        request = call_kwargs["body"]["requests"][0]["updateTableCellStyle"]

        assert call_kwargs["documentId"] == "b" * 25
        assert request["tableRange"]["columnSpan"] == 3
        assert request["tableCellStyle"]["backgroundColor"]["color"]["rgbColor"] == {
            "red": 217 / 255,
            "green": 234 / 255,
            "blue": 211 / 255,
        }
