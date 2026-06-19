"""
Tests for advanced Google Docs formatting and structural batch operations.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from gdocs import docs_tools
from gdocs.docs_helpers import (
    build_text_style,
    create_named_range_request,
    create_replace_named_range_content_request,
    create_update_document_style_request,
    create_update_section_style_request,
)
from gdocs.docs_structure import parse_document_structure
from gdocs.managers.validation_manager import ValidationManager


def _unwrap(tool):
    """Unwrap the decorated tool function to the original implementation."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class TestAdvancedTextStyle:
    def test_weight_baseline_small_caps_and_clear_link(self):
        style, fields = build_text_style(
            font_family="Roboto",
            font_weight=700,
            baseline_offset="SUPERSCRIPT",
            small_caps=True,
            clear_link=True,
        )

        assert style["weightedFontFamily"] == {"fontFamily": "Roboto", "weight": 700}
        assert style["baselineOffset"] == "SUPERSCRIPT"
        assert style["smallCaps"] is True
        assert "weightedFontFamily" in fields
        assert "baselineOffset" in fields
        assert "smallCaps" in fields
        assert "link" in fields


class TestAdvancedRequestBuilders:
    def test_named_range_request_uses_segment_and_tab(self):
        request = create_named_range_request(
            "SUMMARY",
            5,
            15,
            tab_id="tab-123",
            segment_id="kix.header",
        )
        inner = request["createNamedRange"]
        assert inner["name"] == "SUMMARY"
        assert inner["range"] == {
            "startIndex": 5,
            "endIndex": 15,
            "segmentId": "kix.header",
            "tabId": "tab-123",
        }

    def test_replace_named_range_content_request_uses_name(self):
        request = create_replace_named_range_content_request(
            "Updated text",
            named_range_name="SUMMARY",
            tab_id="tab-123",
        )
        inner = request["replaceNamedRangeContent"]
        assert inner["namedRangeName"] == "SUMMARY"
        assert inner["text"] == "Updated text"
        assert inner["tabsCriteria"] == {"tabIds": ["tab-123"]}

    def test_update_document_style_request(self):
        request = create_update_document_style_request(
            tab_id="tab-123",
            background_color="#FFFFFF",
            margin_top=72,
            page_width=612,
            page_height=792,
            document_mode="PAGES",
            use_first_page_header_footer=True,
        )
        inner = request["updateDocumentStyle"]
        assert inner["tabId"] == "tab-123"
        assert inner["documentStyle"]["marginTop"] == {"magnitude": 72, "unit": "PT"}
        assert inner["documentStyle"]["pageSize"]["width"] == {
            "magnitude": 612,
            "unit": "PT",
        }
        assert "documentFormat" in inner["fields"]

    def test_update_section_style_request(self):
        request = create_update_section_style_request(
            10,
            50,
            margin_left=72,
            column_count=2,
            column_spacing=18,
            column_separator_style="BETWEEN_EACH_COLUMN",
            content_direction="LEFT_TO_RIGHT",
        )
        inner = request["updateSectionStyle"]
        assert inner["range"] == {"startIndex": 10, "endIndex": 50}
        assert inner["sectionStyle"]["marginLeft"] == {"magnitude": 72, "unit": "PT"}
        assert len(inner["sectionStyle"]["columnProperties"]) == 2
        assert inner["sectionStyle"]["columnSeparatorStyle"] == "BETWEEN_EACH_COLUMN"


class TestAdvancedStructureParsing:
    def test_parse_document_structure_includes_named_ranges_and_section_breaks(self):
        structure = parse_document_structure(
            {
                "body": {
                    "content": [
                        {
                            "startIndex": 0,
                            "endIndex": 1,
                            "sectionBreak": {"sectionStyle": {}},
                        },
                        {
                            "startIndex": 1,
                            "endIndex": 6,
                            "paragraph": {
                                "elements": [{"textRun": {"content": "Hello"}}]
                            },
                        },
                    ]
                },
                "namedRanges": {
                    "SUMMARY": {
                        "namedRanges": [
                            {
                                "namedRangeId": "nr-123",
                                "ranges": [{"startIndex": 1, "endIndex": 6}],
                            }
                        ]
                    }
                },
            }
        )

        assert len(structure["section_breaks"]) == 1
        assert structure["named_ranges"]["SUMMARY"][0]["named_range_id"] == "nr-123"


class TestAdvancedValidation:
    @pytest.fixture()
    def vm(self):
        return ValidationManager()

    def test_text_formatting_accepts_advanced_fields(self, vm):
        assert vm.validate_text_formatting_params(
            font_family="Roboto",
            font_weight=700,
            baseline_offset="SUBSCRIPT",
            small_caps=True,
        )[0]

    def test_document_style_validation_accepts_layout_fields(self, vm):
        assert vm.validate_document_style_params(
            margin_top=72,
            page_width=612,
            page_height=792,
            document_mode="PAGES",
        )[0]

    def test_section_style_validation_requires_column_count_for_spacing(self, vm):
        is_valid, message = vm.validate_section_style_params(column_spacing=18)
        assert not is_valid
        assert "column_count" in message


class TestAdvancedBatchManagerIntegration:
    @pytest.fixture()
    def manager(self):
        from gdocs.managers.batch_operation_manager import BatchOperationManager

        return BatchOperationManager(Mock())

    def test_build_named_range_request(self, manager):
        request, desc = manager._build_operation_request(
            {
                "type": "create_named_range",
                "name": "SUMMARY",
                "start_index": 5,
                "end_index": 15,
            },
            "create_named_range",
        )
        assert "createNamedRange" in request
        assert "SUMMARY" in desc

    def test_build_document_style_request(self, manager):
        request, desc = manager._build_operation_request(
            {
                "type": "update_document_style",
                "margin_top": 72,
                "margin_bottom": 72,
                "document_mode": "PAGES",
            },
            "update_document_style",
        )
        assert "updateDocumentStyle" in request
        assert desc == "update document style"

    @pytest.mark.asyncio
    async def test_end_to_end_execute_named_range_and_section_style(self, manager):
        manager._execute_batch_requests = AsyncMock(return_value={"replies": [{}, {}]})
        success, _, meta = await manager.execute_batch_operations(
            "doc-123",
            [
                {
                    "type": "create_named_range",
                    "name": "SUMMARY",
                    "start_index": 5,
                    "end_index": 15,
                },
                {
                    "type": "update_section_style",
                    "start_index": 1,
                    "end_index": 20,
                    "margin_left": 72,
                },
            ],
        )
        assert success
        assert meta["operations_count"] == 2


class TestAdvancedPublicToolWiring:
    @pytest.fixture()
    def service(self):
        mock_service = Mock()
        mock_service.documents().batchUpdate().execute.return_value = {"replies": [{}]}
        return mock_service

    @pytest.mark.asyncio
    async def test_modify_doc_text_segment_insert_uses_end_of_segment(self, service):
        await _unwrap(docs_tools.modify_doc_text)(
            service=service,
            user_google_email="user@example.com",
            document_id="a" * 25,
            start_index=1,
            text="Header text",
            segment_id="kix.header",
            end_of_segment=True,
        )

        call_kwargs = service.documents.return_value.batchUpdate.call_args.kwargs
        request = call_kwargs["body"]["requests"][0]["insertText"]

        assert request["endOfSegmentLocation"] == {"segmentId": "kix.header"}
        assert request["text"] == "Header text"

    @pytest.mark.asyncio
    async def test_update_paragraph_style_allows_zero_start_at_body_beginning(
        self, service
    ):
        await _unwrap(docs_tools.update_paragraph_style)(
            service=service,
            user_google_email="user@example.com",
            document_id="b" * 25,
            start_index=0,
            end_index=20,
            heading_level=1,
        )

        call_kwargs = service.documents.return_value.batchUpdate.call_args.kwargs
        request = call_kwargs["body"]["requests"][0]["updateParagraphStyle"]

        assert request["range"] == {"startIndex": 1, "endIndex": 20}
        assert request["paragraphStyle"]["namedStyleType"] == "HEADING_1"

    @pytest.mark.asyncio
    async def test_update_paragraph_style_list_allows_zero_start_at_body_beginning(
        self, service
    ):
        await _unwrap(docs_tools.update_paragraph_style)(
            service=service,
            user_google_email="user@example.com",
            document_id="c" * 25,
            start_index=0,
            end_index=20,
            list_type="UNORDERED",
        )

        call_kwargs = service.documents.return_value.batchUpdate.call_args.kwargs
        request = call_kwargs["body"]["requests"][0]["createParagraphBullets"]

        assert request["range"] == {"startIndex": 1, "endIndex": 20}
        assert request["bulletPreset"] == "BULLET_DISC_CIRCLE_SQUARE"

    @pytest.mark.asyncio
    async def test_batch_update_doc_supports_named_range_and_document_style(
        self, service
    ):
        await _unwrap(docs_tools.batch_update_doc)(
            service=service,
            user_google_email="user@example.com",
            document_id="b" * 25,
            operations=[
                {
                    "type": "create_named_range",
                    "name": "SUMMARY",
                    "start_index": 5,
                    "end_index": 15,
                },
                {
                    "type": "update_document_style",
                    "margin_top": 72,
                    "document_mode": "PAGES",
                },
            ],
        )

        call_kwargs = service.documents.return_value.batchUpdate.call_args.kwargs
        requests = call_kwargs["body"]["requests"]

        assert "createNamedRange" in requests[0]
        assert "updateDocumentStyle" in requests[1]
