"""
Batch Operation Manager

This module provides high-level batch operation management for Google Docs,
extracting complex validation and request building logic.
"""

import logging
import asyncio
from typing import Any, Union, Dict, List, Tuple

from gdocs.docs_helpers import (
    create_insert_text_request,
    create_delete_range_request,
    create_format_text_request,
    create_update_paragraph_style_request,
    create_update_table_cell_style_request,
    create_find_replace_request,
    create_insert_table_request,
    create_insert_page_break_request,
    create_insert_section_break_request,
    create_insert_image_request,
    create_bullet_list_request,
    create_delete_bullet_list_request,
    create_named_range_request,
    create_delete_named_range_request,
    create_replace_named_range_content_request,
    create_update_document_style_request,
    create_update_section_style_request,
    create_create_header_footer_request,
    create_insert_doc_tab_request,
    create_delete_doc_tab_request,
    create_update_doc_tab_request,
    create_insert_table_row_request,
    create_delete_table_row_request,
    create_insert_table_column_request,
    create_delete_table_column_request,
    create_merge_table_cells_request,
    create_unmerge_table_cells_request,
    create_update_table_column_properties_request,
    validate_operation,
)
from gdocs.managers.validation_manager import ValidationManager

logger = logging.getLogger(__name__)


class BatchOperationManager:
    """
    High-level manager for Google Docs batch operations.

    Handles complex multi-operation requests including:
    - Operation validation and request building
    - Batch execution with proper error handling
    - Operation result processing and reporting
    """

    def __init__(self, service):
        """
        Initialize the batch operation manager.

        Args:
            service: Google Docs API service instance
        """
        self.service = service
        self.validation_manager = ValidationManager()

    async def execute_batch_operations(
        self, document_id: str, operations: list[dict[str, Any]]
    ) -> tuple[bool, str, dict[str, Any]]:
        """
        Execute multiple document operations in a single atomic batch.

        This method extracts the complex logic from batch_update_doc tool function.

        Args:
            document_id: ID of the document to update
            operations: List of operation dictionaries

        Returns:
            Tuple of (success, message, metadata)
        """
        logger.info(f"Executing batch operations on document {document_id}")
        logger.info(f"Operations count: {len(operations)}")

        if not operations:
            return (
                False,
                "No operations provided. Please provide at least one operation.",
                {},
            )

        try:
            preflight_error = await self._preflight_create_header_footer_operations(
                document_id, operations
            )
            if preflight_error:
                return False, preflight_error, {}

            # Validate and build requests
            requests, operation_descriptions = await self._validate_and_build_requests(
                operations
            )

            if not requests:
                return False, "No valid requests could be built from operations", {}

            # Execute the batch
            result = await self._execute_batch_requests(document_id, requests)

            # Process results
            metadata = {
                "operations_count": len(operations),
                "requests_count": len(requests),
                "replies_count": len(result.get("replies", [])),
                "operation_summary": operation_descriptions[:5],  # First 5 operations
            }

            # Fetch document length after batch for downstream chaining
            try:
                doc = await asyncio.to_thread(
                    self.service.documents()
                    .get(documentId=document_id, fields="body/content(endIndex)")
                    .execute
                )
                body_content = doc.get("body", {}).get("content", [])
                metadata["document_length"] = (
                    body_content[-1].get("endIndex", 0) if body_content else 1
                )
            except Exception:
                metadata["document_length"] = None

            # Extract new tab IDs from insert_doc_tab replies
            created_tabs = self._extract_created_tabs(result)
            if created_tabs:
                metadata["created_tabs"] = created_tabs

            summary = self._build_operation_summary(operation_descriptions)
            msg = f"Successfully executed {len(operations)} operations ({summary})"
            if created_tabs:
                tab_info = ", ".join(
                    f"'{t['title']}' (tab_id: {t['tab_id']})" for t in created_tabs
                )
                msg += f". Created tabs: {tab_info}"

            return True, msg, metadata

        except Exception as e:
            error_msg = self._rewrite_execution_error(str(e), operations)
            logger.error(f"Failed to execute batch operations: {error_msg}")
            return False, error_msg, {}

    async def _preflight_create_header_footer_operations(
        self, document_id: str, operations: list[dict[str, Any]]
    ) -> str | None:
        """
        Validate create_header_footer operations against the live document before
        sending low-level Docs API requests that often fail opaquely.
        """
        create_ops = [
            op for op in operations if op.get("type") == "create_header_footer"
        ]
        if not create_ops:
            return None

        doc = await asyncio.to_thread(
            self.service.documents().get(documentId=document_id).execute
        )

        style_field_map = {
            "header": {
                "DEFAULT": "defaultHeaderId",
                "FIRST_PAGE_ONLY": "firstPageHeaderId",
                "EVEN_PAGE": "evenPageHeaderId",
            },
            "footer": {
                "DEFAULT": "defaultFooterId",
                "FIRST_PAGE_ONLY": "firstPageFooterId",
                "EVEN_PAGE": "evenPageFooterId",
            },
        }

        section_breaks: dict[int, dict[str, Any]] = {}
        for element in doc.get("body", {}).get("content", []):
            if "sectionBreak" in element:
                section_breaks[element.get("startIndex", 0)] = element["sectionBreak"]

        for op in create_ops:
            section_type = op["section_type"]
            header_footer_type = op.get("header_footer_type", "DEFAULT")
            style_field = style_field_map[section_type][header_footer_type]
            section_break_index = op.get("section_break_index")

            if section_break_index is None:
                if doc.get("documentStyle", {}).get(style_field):
                    return (
                        "Batch operation failed: the requested header/footer already exists. "
                        "For normal header or footer text, use update_doc_headers_footers "
                        "instead of batch_update_doc with create_header_footer. "
                        "Reserve create_header_footer for advanced section-break layouts."
                    )
                continue

            if section_break_index not in section_breaks:
                available = sorted(section_breaks.keys())
                return (
                    "Batch operation failed: section_break_index must match an existing "
                    "section break start index from inspect_doc_structure.section_breaks[]. "
                    f"Got {section_break_index}. Available section break indices: {available or 'none'}. "
                    "For normal header/footer text, use update_doc_headers_footers instead."
                )

            if (
                section_breaks[section_break_index]
                .get("sectionStyle", {})
                .get(style_field)
            ):
                return (
                    "Batch operation failed: the requested section-scoped header/footer "
                    "already exists at that section break. Use inspect_doc_structure to "
                    "find real section breaks, or use update_doc_headers_footers for "
                    "ordinary header/footer text."
                )

        return None

    async def _validate_and_build_requests(
        self, operations: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Validate operations and build API requests.

        Args:
            operations: List of operation dictionaries

        Returns:
            Tuple of (requests, operation_descriptions)
        """
        requests = []
        operation_descriptions = []

        for i, op in enumerate(operations):
            # Validate operation structure
            is_valid, error_msg = validate_operation(op)
            if not is_valid:
                raise ValueError(f"Operation {i + 1}: {error_msg}")

            op_type = op.get("type")

            try:
                # Build request based on operation type
                result = self._build_operation_request(op, op_type)

                # Handle both single request and list of requests
                if isinstance(result[0], list):
                    # Multiple requests (e.g., replace_text)
                    for req in result[0]:
                        requests.append(req)
                    operation_descriptions.append(result[1])
                elif result[0]:
                    # Single request
                    requests.append(result[0])
                    operation_descriptions.append(result[1])

            except KeyError as e:
                raise ValueError(
                    f"Operation {i + 1} ({op_type}) missing required field: {e}"
                )
            except Exception as e:
                raise ValueError(
                    f"Operation {i + 1} ({op_type}) failed validation: {str(e)}"
                )

        return requests, operation_descriptions

    def _build_operation_request(
        self, op: dict[str, Any], op_type: str
    ) -> Tuple[Union[Dict[str, Any], List[Dict[str, Any]]], str]:
        """
        Build a single operation request.

        Args:
            op: Operation dictionary
            op_type: Operation type

        Returns:
            Tuple of (request, description)
        """
        tab_id = op.get("tab_id")
        segment_id = op.get("segment_id")
        end_of_segment = op.get("end_of_segment", False)

        if op_type == "insert_text":
            request = create_insert_text_request(
                op.get("index"),
                op["text"],
                tab_id,
                segment_id=segment_id,
                end_of_segment=end_of_segment,
            )
            description = (
                f"insert text at end of segment '{segment_id or 'body'}'"
                if end_of_segment
                else f"insert text at {op['index']}"
            )

        elif op_type == "delete_text":
            request = create_delete_range_request(
                op["start_index"],
                op["end_index"],
                tab_id,
                segment_id=segment_id,
            )
            description = f"delete text {op['start_index']}-{op['end_index']}"

        elif op_type == "replace_text":
            delete_request = create_delete_range_request(
                op["start_index"],
                op["end_index"],
                tab_id,
                segment_id=segment_id,
            )
            insert_request = create_insert_text_request(
                op["start_index"],
                op["text"],
                tab_id,
                segment_id=segment_id,
            )
            request = [delete_request, insert_request]
            description = f"replace text {op['start_index']}-{op['end_index']} with '{op['text'][:20]}{'...' if len(op['text']) > 20 else ''}'"

        elif op_type == "format_text":
            request = create_format_text_request(
                op["start_index"],
                op["end_index"],
                op.get("bold"),
                op.get("italic"),
                op.get("underline"),
                op.get("strikethrough"),
                op.get("font_size"),
                op.get("font_family"),
                op.get("font_weight"),
                op.get("text_color"),
                op.get("background_color"),
                op.get("link_url"),
                op.get("clear_link"),
                op.get("baseline_offset"),
                op.get("small_caps"),
                tab_id,
                segment_id,
            )

            if not request:
                raise ValueError("No formatting options provided")

            format_changes = []
            for param, name in [
                ("bold", "bold"),
                ("italic", "italic"),
                ("underline", "underline"),
                ("strikethrough", "strikethrough"),
                ("font_size", "font size"),
                ("font_family", "font family"),
                ("font_weight", "font weight"),
                ("text_color", "text color"),
                ("background_color", "background color"),
                ("link_url", "link"),
                ("clear_link", "clear link"),
                ("baseline_offset", "baseline offset"),
                ("small_caps", "small caps"),
            ]:
                if op.get(param) is not None:
                    if param == "font_size":
                        value = f"{op[param]}pt"
                    elif param == "font_weight":
                        value = f"{op[param]}w"
                    else:
                        value = op[param]
                    format_changes.append(f"{name}: {value}")

            description = f"format text {op['start_index']}-{op['end_index']} ({', '.join(format_changes)})"

        elif op_type == "update_paragraph_style":
            request = create_update_paragraph_style_request(
                op["start_index"],
                op["end_index"],
                op.get("heading_level"),
                op.get("alignment"),
                op.get("line_spacing"),
                op.get("indent_first_line"),
                op.get("indent_start"),
                op.get("indent_end"),
                op.get("space_above"),
                op.get("space_below"),
                tab_id,
                op.get("named_style_type"),
                segment_id,
                op.get("direction"),
                op.get("keep_lines_together"),
                op.get("keep_with_next"),
                op.get("avoid_widow_and_orphan"),
                op.get("page_break_before"),
                op.get("spacing_mode"),
                op.get("shading_color"),
            )

            if not request:
                raise ValueError("No paragraph style options provided")

            _PT_PARAMS = {
                "indent_first_line",
                "indent_start",
                "indent_end",
                "space_above",
                "space_below",
            }
            _SUFFIX = {
                "heading_level": lambda v: f"H{v}",
                "line_spacing": lambda v: f"{v}x",
            }

            style_changes = []
            for param, name in [
                ("heading_level", "heading"),
                ("alignment", "alignment"),
                ("line_spacing", "line spacing"),
                ("indent_first_line", "first line indent"),
                ("indent_start", "start indent"),
                ("indent_end", "end indent"),
                ("space_above", "space above"),
                ("space_below", "space below"),
                ("named_style_type", "named style"),
                ("direction", "direction"),
                ("keep_lines_together", "keep lines together"),
                ("keep_with_next", "keep with next"),
                ("avoid_widow_and_orphan", "avoid widow/orphan"),
                ("page_break_before", "page break before"),
                ("spacing_mode", "spacing mode"),
                ("shading_color", "shading"),
            ]:
                if op.get(param) is not None:
                    raw = op[param]
                    fmt = _SUFFIX.get(param)
                    if fmt:
                        value = fmt(raw)
                    elif param in _PT_PARAMS:
                        value = f"{raw}pt"
                    else:
                        value = raw
                    style_changes.append(f"{name}: {value}")

            description = f"paragraph style {op['start_index']}-{op['end_index']} ({', '.join(style_changes)})"

        elif op_type == "update_table_cell_style":
            is_valid, error_msg = (
                self.validation_manager.validate_table_cell_style_params(
                    background_color=op.get("background_color"),
                    border_color=op.get("border_color"),
                    border_width=op.get("border_width"),
                    padding_top=op.get("padding_top"),
                    padding_bottom=op.get("padding_bottom"),
                    padding_left=op.get("padding_left"),
                    padding_right=op.get("padding_right"),
                    content_alignment=op.get("content_alignment"),
                    row_index=op.get("row_index"),
                    column_index=op.get("column_index"),
                    row_span=op.get("row_span"),
                    column_span=op.get("column_span"),
                )
            )
            if not is_valid:
                raise ValueError(error_msg)

            request = create_update_table_cell_style_request(
                table_start_index=op["table_start_index"],
                background_color=op.get("background_color"),
                border_color=op.get("border_color"),
                border_width=op.get("border_width"),
                padding_top=op.get("padding_top"),
                padding_bottom=op.get("padding_bottom"),
                padding_left=op.get("padding_left"),
                padding_right=op.get("padding_right"),
                content_alignment=op.get("content_alignment"),
                row_index=op.get("row_index"),
                column_index=op.get("column_index"),
                row_span=op.get("row_span"),
                column_span=op.get("column_span"),
                tab_id=tab_id,
            )

            if not request:
                raise ValueError("No table cell style options provided")

            style_changes = []
            for param, name in [
                ("background_color", "background"),
                ("border_color", "border color"),
                ("border_width", "border width"),
                ("padding_top", "padding top"),
                ("padding_bottom", "padding bottom"),
                ("padding_left", "padding left"),
                ("padding_right", "padding right"),
                ("content_alignment", "content alignment"),
            ]:
                if op.get(param) is not None:
                    value = (
                        f"{op[param]}pt"
                        if param
                        in (
                            "border_width",
                            "padding_top",
                            "padding_bottom",
                            "padding_left",
                            "padding_right",
                        )
                        else op[param]
                    )
                    style_changes.append(f"{name}: {value}")

            if op.get("row_index") is not None:
                row_span = op.get("row_span", 1)
                column_span = op.get("column_span", 1)
                target = (
                    f"row {op['row_index']}, column {op['column_index']}, "
                    f"span {row_span}x{column_span}"
                )
            else:
                target = "entire table"

            description = (
                f"table cell style at {op['table_start_index']} "
                f"({target}; {', '.join(style_changes)})"
            )

        elif op_type == "insert_table":
            request = create_insert_table_request(
                op.get("index"),
                op["rows"],
                op["columns"],
                tab_id,
                segment_id=segment_id,
                end_of_segment=end_of_segment,
            )
            description = (
                f"insert {op['rows']}x{op['columns']} table at end of segment '{segment_id or 'body'}'"
                if end_of_segment
                else f"insert {op['rows']}x{op['columns']} table at {op['index']}"
            )

        elif op_type == "insert_page_break":
            request = create_insert_page_break_request(
                op.get("index"), tab_id, end_of_segment=end_of_segment
            )
            description = (
                "insert page break at end of body"
                if end_of_segment
                else f"insert page break at {op['index']}"
            )

        elif op_type == "insert_section_break":
            request = create_insert_section_break_request(
                op.get("index"),
                op.get("section_type", "NEXT_PAGE"),
                end_of_segment=end_of_segment,
            )
            description = (
                f"insert {op.get('section_type', 'NEXT_PAGE')} section break at end of body"
                if end_of_segment
                else f"insert {op.get('section_type', 'NEXT_PAGE')} section break at {op.get('index')}"
            )

        elif op_type == "find_replace":
            request = create_find_replace_request(
                op["find_text"], op["replace_text"], op.get("match_case", False), tab_id
            )
            description = f"find/replace '{op['find_text']}' → '{op['replace_text']}'"

        elif op_type == "create_bullet_list":
            list_type = op.get("list_type", "UNORDERED")
            if list_type not in ("UNORDERED", "ORDERED", "CHECKBOX", "NONE"):
                raise ValueError(
                    f"Invalid list_type '{list_type}'. Must be 'UNORDERED', 'ORDERED', 'CHECKBOX', or 'NONE'"
                )
            if list_type == "NONE":
                request = create_delete_bullet_list_request(
                    op["start_index"], op["end_index"], tab_id, segment_id=segment_id
                )
                description = f"remove bullets {op['start_index']}-{op['end_index']}"
            else:
                request = create_bullet_list_request(
                    op["start_index"],
                    op["end_index"],
                    list_type,
                    op.get("nesting_level"),
                    op.get("paragraph_start_indices"),
                    tab_id,
                    op.get("bullet_preset"),
                    segment_id=segment_id,
                )
                if list_type == "UNORDERED":
                    style = "bulleted"
                elif list_type == "CHECKBOX":
                    style = "checkbox"
                else:
                    style = "numbered"
                description = (
                    f"create {style} list {op['start_index']}-{op['end_index']}"
                )
                if op.get("nesting_level"):
                    description += f" (nesting level {op['nesting_level']})"
                if op.get("bullet_preset"):
                    description += f" using {op['bullet_preset']}"

        elif op_type == "create_named_range":
            request = create_named_range_request(
                op["name"],
                op["start_index"],
                op["end_index"],
                tab_id=tab_id,
                segment_id=segment_id,
            )
            description = (
                f"create named range '{op['name']}' "
                f"{op['start_index']}-{op['end_index']}"
            )

        elif op_type == "replace_named_range_content":
            request = create_replace_named_range_content_request(
                op["text"],
                named_range_id=op.get("named_range_id"),
                named_range_name=op.get("named_range_name"),
                tab_id=tab_id,
            )
            target = op.get("named_range_id") or op.get("named_range_name")
            description = f"replace named range content for '{target}'"

        elif op_type == "delete_named_range":
            request = create_delete_named_range_request(
                named_range_id=op.get("named_range_id"),
                named_range_name=op.get("named_range_name"),
                tab_id=tab_id,
            )
            target = op.get("named_range_id") or op.get("named_range_name")
            description = f"delete named range '{target}'"

        elif op_type == "update_document_style":
            request = create_update_document_style_request(
                tab_id=tab_id,
                background_color=op.get("background_color"),
                margin_top=op.get("margin_top"),
                margin_bottom=op.get("margin_bottom"),
                margin_left=op.get("margin_left"),
                margin_right=op.get("margin_right"),
                margin_header=op.get("margin_header"),
                margin_footer=op.get("margin_footer"),
                page_width=op.get("page_width"),
                page_height=op.get("page_height"),
                page_number_start=op.get("page_number_start"),
                use_even_page_header_footer=op.get("use_even_page_header_footer"),
                use_first_page_header_footer=op.get("use_first_page_header_footer"),
                flip_page_orientation=op.get("flip_page_orientation"),
                document_mode=op.get("document_mode"),
            )
            if not request:
                raise ValueError("No document style options provided")
            description = "update document style"

        elif op_type == "update_section_style":
            request = create_update_section_style_request(
                op["start_index"],
                op["end_index"],
                margin_top=op.get("margin_top"),
                margin_bottom=op.get("margin_bottom"),
                margin_left=op.get("margin_left"),
                margin_right=op.get("margin_right"),
                margin_header=op.get("margin_header"),
                margin_footer=op.get("margin_footer"),
                page_number_start=op.get("page_number_start"),
                use_first_page_header_footer=op.get("use_first_page_header_footer"),
                flip_page_orientation=op.get("flip_page_orientation"),
                content_direction=op.get("content_direction"),
                column_count=op.get("column_count"),
                column_spacing=op.get("column_spacing"),
                column_separator_style=op.get("column_separator_style"),
            )
            if not request:
                raise ValueError("No section style options provided")
            description = f"update section style {op['start_index']}-{op['end_index']}"

        elif op_type == "create_header_footer":
            request = create_create_header_footer_request(
                op["section_type"],
                op.get("header_footer_type", "DEFAULT"),
                op.get("section_break_index"),
            )
            description = f"create {op['section_type']} ({op.get('header_footer_type', 'DEFAULT')})"

        elif op_type == "insert_image":
            request = create_insert_image_request(
                op.get("index"),
                op["image_uri"],
                op.get("width"),
                op.get("height"),
                tab_id,
                segment_id=segment_id,
                end_of_segment=end_of_segment,
            )
            description = (
                f"insert image at end of segment '{segment_id or 'body'}'"
                if end_of_segment
                else f"insert image at {op.get('index')}"
            )

        elif op_type == "insert_doc_tab":
            request = create_insert_doc_tab_request(
                op["title"], op["index"], op.get("parent_tab_id")
            )
            description = f"insert tab '{op['title']}' at {op['index']}"
            if op.get("parent_tab_id"):
                description += f" under parent tab {op['parent_tab_id']}"

        elif op_type == "delete_doc_tab":
            request = create_delete_doc_tab_request(op["tab_id"])
            description = f"delete tab '{op['tab_id']}'"

        elif op_type == "update_doc_tab":
            request = create_update_doc_tab_request(op["tab_id"], op["title"])
            description = f"rename tab '{op['tab_id']}' to '{op['title']}'"

        elif op_type == "insert_table_row":
            request = create_insert_table_row_request(
                table_start_index=op["table_start_index"],
                row_index=op["row_index"],
                insert_below=op.get("insert_below", True),
                tab_id=tab_id,
            )
            direction = "below" if op.get("insert_below", True) else "above"
            description = f"insert row {direction} row {op['row_index']} in table at {op['table_start_index']}"

        elif op_type == "delete_table_row":
            request = create_delete_table_row_request(
                table_start_index=op["table_start_index"],
                row_index=op["row_index"],
                tab_id=tab_id,
            )
            description = (
                f"delete row {op['row_index']} from table at {op['table_start_index']}"
            )

        elif op_type == "insert_table_column":
            request = create_insert_table_column_request(
                table_start_index=op["table_start_index"],
                column_index=op["column_index"],
                insert_right=op.get("insert_right", True),
                tab_id=tab_id,
            )
            direction = "right of" if op.get("insert_right", True) else "left of"
            description = f"insert column {direction} column {op['column_index']} in table at {op['table_start_index']}"

        elif op_type == "delete_table_column":
            request = create_delete_table_column_request(
                table_start_index=op["table_start_index"],
                column_index=op["column_index"],
                tab_id=tab_id,
            )
            description = f"delete column {op['column_index']} from table at {op['table_start_index']}"

        elif op_type == "merge_table_cells":
            request = create_merge_table_cells_request(
                table_start_index=op["table_start_index"],
                row_index=op["row_index"],
                column_index=op["column_index"],
                row_span=op["row_span"],
                column_span=op["column_span"],
                tab_id=tab_id,
            )
            description = (
                f"merge cells at ({op['row_index']},{op['column_index']}) "
                f"span {op['row_span']}x{op['column_span']} in table at {op['table_start_index']}"
            )

        elif op_type == "unmerge_table_cells":
            request = create_unmerge_table_cells_request(
                table_start_index=op["table_start_index"],
                row_index=op["row_index"],
                column_index=op["column_index"],
                row_span=op["row_span"],
                column_span=op["column_span"],
                tab_id=tab_id,
            )
            description = (
                f"unmerge cells at ({op['row_index']},{op['column_index']}) "
                f"span {op['row_span']}x{op['column_span']} in table at {op['table_start_index']}"
            )

        elif op_type == "update_table_column_properties":
            request = create_update_table_column_properties_request(
                table_start_index=op["table_start_index"],
                column_indices=op["column_indices"],
                width=op.get("width"),
                width_type=op.get("width_type"),
                tab_id=tab_id,
            )

            if not request:
                raise ValueError(
                    "update_table_column_properties requires at least one of: width, width_type"
                )

            description = (
                f"update column properties for columns {op['column_indices']} "
                f"in table at {op['table_start_index']}"
            )

        else:
            supported_types = [
                "insert_text",
                "delete_text",
                "replace_text",
                "format_text",
                "update_paragraph_style",
                "update_table_cell_style",
                "insert_table",
                "insert_page_break",
                "insert_section_break",
                "find_replace",
                "create_bullet_list",
                "create_named_range",
                "replace_named_range_content",
                "delete_named_range",
                "update_document_style",
                "update_section_style",
                "create_header_footer",
                "insert_image",
                "insert_doc_tab",
                "delete_doc_tab",
                "update_doc_tab",
                "insert_table_row",
                "delete_table_row",
                "insert_table_column",
                "delete_table_column",
                "merge_table_cells",
                "unmerge_table_cells",
                "update_table_column_properties",
            ]
            raise ValueError(
                f"Unsupported operation type '{op_type}'. Supported: {', '.join(supported_types)}"
            )

        return request, description

    async def _execute_batch_requests(
        self, document_id: str, requests: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Execute the batch requests against the Google Docs API.

        Args:
            document_id: Document ID
            requests: List of API requests

        Returns:
            API response
        """
        return await asyncio.to_thread(
            self.service.documents()
            .batchUpdate(documentId=document_id, body={"requests": requests})
            .execute
        )

    def _extract_created_tabs(self, result: dict[str, Any]) -> list[dict[str, str]]:
        """
        Extract tab IDs from insert_doc_tab replies in the batchUpdate response.

        Args:
            result: The batchUpdate API response

        Returns:
            List of dicts with tab_id and title for each created tab
        """
        created_tabs = []
        for reply in result.get("replies", []):
            if "createDocumentTab" in reply:
                props = reply["createDocumentTab"].get("tabProperties", {})
                tab_id = props.get("tabId")
                title = props.get("title", "")
                if tab_id:
                    created_tabs.append({"tab_id": tab_id, "title": title})
        return created_tabs

    def _build_operation_summary(self, operation_descriptions: list[str]) -> str:
        """
        Build a concise summary of operations performed.

        Args:
            operation_descriptions: List of operation descriptions

        Returns:
            Summary string
        """
        if not operation_descriptions:
            return "no operations"

        summary_items = operation_descriptions[:3]  # Show first 3 operations
        summary = ", ".join(summary_items)

        if len(operation_descriptions) > 3:
            remaining = len(operation_descriptions) - 3
            summary += f" and {remaining} more operation{'s' if remaining > 1 else ''}"

        return summary

    def _rewrite_execution_error(
        self, error_msg: str, operations: list[dict[str, Any]]
    ) -> str:
        """
        Rewrite common API failures into actionable guidance for tool callers.
        """
        lowered = error_msg.lower()
        requested_header_footer_creation = any(
            op.get("type") == "create_header_footer" for op in operations
        )

        if (
            requested_header_footer_creation
            and "already exists" in lowered
            and ("createheader" in lowered or "createfooter" in lowered)
        ):
            return (
                "Batch operation failed: the requested header/footer already exists. "
                "For normal header or footer text, use update_doc_headers_footers "
                "instead of batch_update_doc with create_header_footer. "
                "Reserve create_header_footer for advanced section-break layouts."
            )

        return f"Batch operation failed: {error_msg}"

    def get_supported_operations(self) -> dict[str, Any]:
        """
        Get information about supported batch operations.

        Returns:
            Dictionary with supported operation types and their required parameters
        """
        return {
            "supported_operations": {
                "insert_text": {
                    "required": ["index", "text"],
                    "description": "Insert text at specified index",
                },
                "delete_text": {
                    "required": ["start_index", "end_index"],
                    "description": "Delete text in specified range",
                },
                "replace_text": {
                    "required": ["start_index", "end_index", "text"],
                    "description": "Replace text in range with new text",
                },
                "format_text": {
                    "required": ["start_index", "end_index"],
                    "optional": [
                        "bold",
                        "italic",
                        "underline",
                        "strikethrough",
                        "font_size",
                        "font_family",
                        "font_weight",
                        "text_color",
                        "background_color",
                        "link_url",
                        "clear_link",
                        "baseline_offset",
                        "small_caps",
                        "segment_id",
                        "tab_id",
                    ],
                    "description": "Apply formatting to text range",
                },
                "update_paragraph_style": {
                    "required": ["start_index", "end_index"],
                    "optional": [
                        "heading_level",
                        "alignment",
                        "line_spacing",
                        "indent_first_line",
                        "indent_start",
                        "indent_end",
                        "space_above",
                        "space_below",
                        "named_style_type",
                        "direction",
                        "keep_lines_together",
                        "keep_with_next",
                        "avoid_widow_and_orphan",
                        "page_break_before",
                        "spacing_mode",
                        "shading_color",
                        "segment_id",
                        "tab_id",
                    ],
                    "description": "Apply paragraph-level styling (headings, named styles like TITLE/SUBTITLE, alignment, spacing, indentation)",
                },
                "update_table_cell_style": {
                    "required": ["table_start_index"],
                    "optional": [
                        "background_color",
                        "border_color",
                        "border_width",
                        "padding_top",
                        "padding_bottom",
                        "padding_left",
                        "padding_right",
                        "content_alignment",
                        "row_index",
                        "column_index",
                        "row_span",
                        "column_span",
                    ],
                    "description": "Apply table cell styling to an entire table or a targeted cell range",
                },
                "insert_table": {
                    "required": ["rows", "columns"],
                    "optional": ["index", "end_of_segment", "segment_id", "tab_id"],
                    "description": "Insert table at specified index or end of a segment",
                },
                "insert_page_break": {
                    "required": [],
                    "optional": ["index", "end_of_segment", "tab_id"],
                    "description": "Insert page break at specified index or end of body",
                },
                "insert_section_break": {
                    "required": [],
                    "optional": ["index", "end_of_segment", "section_type"],
                    "description": "Insert a CONTINUOUS or NEXT_PAGE section break",
                },
                "find_replace": {
                    "required": ["find_text", "replace_text"],
                    "optional": ["match_case"],
                    "description": "Find and replace text throughout document",
                },
                "create_bullet_list": {
                    "required": ["start_index", "end_index"],
                    "optional": [
                        "list_type",
                        "nesting_level",
                        "paragraph_start_indices",
                        "bullet_preset",
                    ],
                    "description": "Apply or remove native bullet/numbered/checklist formatting (list_type: UNORDERED, ORDERED, CHECKBOX, or NONE to remove; nesting_level: 0-8)",
                },
                "create_named_range": {
                    "required": ["name", "start_index", "end_index"],
                    "optional": ["tab_id", "segment_id"],
                    "description": "Create a named range anchor over a range",
                },
                "replace_named_range_content": {
                    "required": ["text"],
                    "optional": ["named_range_id", "named_range_name", "tab_id"],
                    "description": "Replace the content of one or more named ranges",
                },
                "delete_named_range": {
                    "required": [],
                    "optional": ["named_range_id", "named_range_name", "tab_id"],
                    "description": "Delete a named range by ID or name",
                },
                "update_document_style": {
                    "required": [],
                    "optional": [
                        "background_color",
                        "margin_top",
                        "margin_bottom",
                        "margin_left",
                        "margin_right",
                        "margin_header",
                        "margin_footer",
                        "page_width",
                        "page_height",
                        "page_number_start",
                        "use_even_page_header_footer",
                        "use_first_page_header_footer",
                        "flip_page_orientation",
                        "document_mode",
                        "tab_id",
                    ],
                    "description": "Update document-level layout settings such as margins, page size, headers/footers, and background",
                },
                "update_section_style": {
                    "required": ["start_index", "end_index"],
                    "optional": [
                        "margin_top",
                        "margin_bottom",
                        "margin_left",
                        "margin_right",
                        "margin_header",
                        "margin_footer",
                        "page_number_start",
                        "use_first_page_header_footer",
                        "flip_page_orientation",
                        "content_direction",
                        "column_count",
                        "column_spacing",
                        "column_separator_style",
                    ],
                    "description": "Update section-specific layout such as margins, pagination, orientation, and columns",
                },
                "create_header_footer": {
                    "required": ["section_type"],
                    "optional": ["header_footer_type", "section_break_index"],
                    "description": "Create a header or footer, optionally tied to a section break",
                },
                "insert_image": {
                    "required": ["image_uri"],
                    "optional": [
                        "index",
                        "end_of_segment",
                        "segment_id",
                        "tab_id",
                        "width",
                        "height",
                    ],
                    "description": "Insert an inline image at a location or at the end of a segment",
                },
                "insert_doc_tab": {
                    "required": ["title", "index"],
                    "description": "Insert a new document tab with given title at specified index",
                },
                "delete_doc_tab": {
                    "required": ["tab_id"],
                    "description": "Delete a document tab by its ID",
                },
                "update_doc_tab": {
                    "required": ["tab_id", "title"],
                    "description": "Rename a document tab",
                },
                "insert_table_row": {
                    "required": ["table_start_index", "row_index"],
                    "optional": ["insert_below", "tab_id"],
                    "description": "Insert a row above or below a reference row in a table",
                },
                "delete_table_row": {
                    "required": ["table_start_index", "row_index"],
                    "optional": ["tab_id"],
                    "description": "Delete a row from a table",
                },
                "insert_table_column": {
                    "required": ["table_start_index", "column_index"],
                    "optional": ["insert_right", "tab_id"],
                    "description": "Insert a column to the left or right of a reference column in a table",
                },
                "delete_table_column": {
                    "required": ["table_start_index", "column_index"],
                    "optional": ["tab_id"],
                    "description": "Delete a column from a table",
                },
                "merge_table_cells": {
                    "required": [
                        "table_start_index",
                        "row_index",
                        "column_index",
                        "row_span",
                        "column_span",
                    ],
                    "optional": ["tab_id"],
                    "description": "Merge a rectangular range of cells in a table",
                },
                "unmerge_table_cells": {
                    "required": [
                        "table_start_index",
                        "row_index",
                        "column_index",
                        "row_span",
                        "column_span",
                    ],
                    "optional": ["tab_id"],
                    "description": "Unmerge cells in a table that were previously merged",
                },
                "update_table_column_properties": {
                    "required": ["table_start_index", "column_indices"],
                    "optional": ["width", "width_type", "tab_id"],
                    "description": "Update column width and width type for specified columns in a table",
                },
            },
            "example_operations": [
                {"type": "insert_text", "index": 1, "text": "Hello World"},
                {
                    "type": "format_text",
                    "start_index": 1,
                    "end_index": 12,
                    "bold": True,
                },
                {"type": "insert_table", "index": 20, "rows": 2, "columns": 3},
                {
                    "type": "update_paragraph_style",
                    "start_index": 1,
                    "end_index": 20,
                    "heading_level": 1,
                    "alignment": "CENTER",
                },
            ],
        }
