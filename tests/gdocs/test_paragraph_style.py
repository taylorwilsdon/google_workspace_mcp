"""
Tests for update_paragraph_style batch operation support.

Covers the helpers, validation, and batch manager integration.
"""

import pytest
from unittest.mock import AsyncMock, Mock

from gdocs.docs_helpers import (
    build_paragraph_style,
    create_update_paragraph_style_request,
)
from gdocs.managers.validation_manager import ValidationManager


class TestBuildParagraphStyle:
    def test_no_params_returns_empty(self):
        style, fields = build_paragraph_style()
        assert style == {}
        assert fields == []

    def test_heading_zero_maps_to_normal_text(self):
        style, fields = build_paragraph_style(heading_level=0)
        assert style["namedStyleType"] == "NORMAL_TEXT"

    def test_heading_maps_to_named_style(self):
        style, _ = build_paragraph_style(heading_level=3)
        assert style["namedStyleType"] == "HEADING_3"

    def test_heading_out_of_range_raises(self):
        with pytest.raises(ValueError):
            build_paragraph_style(heading_level=7)

    def test_line_spacing_scaled_to_percentage(self):
        style, _ = build_paragraph_style(line_spacing=1.5)
        assert style["lineSpacing"] == 150.0

    def test_dimension_field_uses_pt_unit(self):
        style, _ = build_paragraph_style(indent_start=36.0)
        assert style["indentStart"] == {"magnitude": 36.0, "unit": "PT"}

    def test_multiple_params_combined(self):
        style, fields = build_paragraph_style(
            heading_level=2, alignment="CENTER", space_below=12.0
        )
        assert len(fields) == 3
        assert style["alignment"] == "CENTER"


class TestCreateUpdateParagraphStyleRequest:
    def test_returns_none_when_no_styles(self):
        assert create_update_paragraph_style_request(1, 10) is None

    def test_produces_correct_api_structure(self):
        result = create_update_paragraph_style_request(1, 10, heading_level=1)
        inner = result["updateParagraphStyle"]
        assert inner["range"] == {"startIndex": 1, "endIndex": 10}
        assert inner["paragraphStyle"]["namedStyleType"] == "HEADING_1"
        assert inner["fields"] == "namedStyleType"


class TestValidateParagraphStyleParams:
    @pytest.fixture()
    def vm(self):
        return ValidationManager()

    def test_all_none_rejected(self, vm):
        is_valid, _ = vm.validate_paragraph_style_params()
        assert not is_valid

    def test_wrong_types_rejected(self, vm):
        assert not vm.validate_paragraph_style_params(heading_level=1.5)[0]
        assert not vm.validate_paragraph_style_params(alignment=123)[0]
        assert not vm.validate_paragraph_style_params(line_spacing="double")[0]

    def test_negative_indent_start_rejected(self, vm):
        is_valid, msg = vm.validate_paragraph_style_params(indent_start=-5.0)
        assert not is_valid
        assert "non-negative" in msg

    def test_negative_indent_first_line_allowed(self, vm):
        """Hanging indent requires negative first-line indent."""
        assert vm.validate_paragraph_style_params(indent_first_line=-18.0)[0]

    def test_batch_validation_wired_up(self, vm):
        valid_ops = [
            {
                "type": "update_paragraph_style",
                "start_index": 1,
                "end_index": 20,
                "heading_level": 2,
            },
        ]
        assert vm.validate_batch_operations(valid_ops)[0]

        no_style_ops = [
            {"type": "update_paragraph_style", "start_index": 1, "end_index": 20},
        ]
        assert not vm.validate_batch_operations(no_style_ops)[0]


class TestBatchManagerIntegration:
    @pytest.fixture()
    def manager(self):
        from gdocs.managers.batch_operation_manager import BatchOperationManager

        return BatchOperationManager(Mock())

    def test_build_request_and_description(self, manager):
        op = {
            "type": "update_paragraph_style",
            "start_index": 1,
            "end_index": 50,
            "heading_level": 2,
            "alignment": "CENTER",
            "line_spacing": 1.5,
        }
        request, desc = manager._build_operation_request(op, "update_paragraph_style")
        assert "updateParagraphStyle" in request
        assert "heading: H2" in desc
        assert "1.5x" in desc

    @pytest.mark.asyncio
    async def test_end_to_end_execute(self, manager):
        manager._execute_batch_requests = AsyncMock(return_value={"replies": [{}]})
        success, message, meta = await manager.execute_batch_operations(
            "doc-123",
            [
                {
                    "type": "update_paragraph_style",
                    "start_index": 1,
                    "end_index": 20,
                    "heading_level": 1,
                }
            ],
        )
        assert success
        assert meta["operations_count"] == 1
