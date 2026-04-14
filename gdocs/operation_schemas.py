"""
Typed Pydantic schemas for Google Docs batch operations.

These models are used to generate a richer MCP schema for batch_update_doc so
LLMs receive a machine-readable contract instead of a free-form object array.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, BeforeValidator, model_validator


def _coerce_json_str_to_list(v: Any) -> Any:
    """Accept JSON-encoded lists for MCP clients that serialize arrays as strings."""
    if not isinstance(v, str):
        return v

    try:
        parsed = json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return v

    return parsed if isinstance(parsed, list) else v


class StrictDocOperation(BaseModel):
    """Base model for strictly typed high-impact operations."""

    model_config = ConfigDict(extra="forbid")

    tab_id: Optional[str] = Field(
        default=None,
        description="Optional document tab ID to target.",
    )


class SegmentTargetDocOperation(StrictDocOperation):
    """Base model for operations that can target document segments."""

    segment_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional header/footer/footnote segment ID. Use a real ID returned by "
            "inspect_doc_structure; do not guess values like 'kix.header'."
        ),
    )


class InsertTextOperation(SegmentTargetDocOperation):
    type: Literal["insert_text"]
    text: str = Field(description="Text to insert.")
    index: Optional[int] = Field(
        default=None,
        description="Insertion index. Omit when end_of_segment=true.",
    )
    end_of_segment: bool = Field(
        default=False,
        description="Append to the end of the targeted body/segment instead of using index.",
    )

    @model_validator(mode="after")
    def validate_location(self) -> "InsertTextOperation":
        if self.end_of_segment == (self.index is not None):
            raise ValueError("Provide exactly one of 'index' or 'end_of_segment=true'.")
        return self


class ReplaceTextOperation(SegmentTargetDocOperation):
    type: Literal["replace_text"]
    start_index: int
    end_index: int
    text: str = Field(description="Replacement text.")


class DeleteTextOperation(SegmentTargetDocOperation):
    type: Literal["delete_text"]
    start_index: int
    end_index: int


class FormatTextOperation(SegmentTargetDocOperation):
    type: Literal["format_text"]
    start_index: int
    end_index: int
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    strikethrough: Optional[bool] = None
    font_size: Optional[int] = None
    font_family: Optional[str] = None
    font_weight: Optional[int] = None
    text_color: Optional[str] = None
    background_color: Optional[str] = None
    link_url: Optional[str] = None
    clear_link: Optional[bool] = None
    baseline_offset: Optional[str] = None
    small_caps: Optional[bool] = None


class UpdateParagraphStyleOperation(SegmentTargetDocOperation):
    type: Literal["update_paragraph_style"]
    start_index: int
    end_index: int
    heading_level: Optional[int] = None
    alignment: Optional[str] = None
    line_spacing: Optional[float] = None
    indent_first_line: Optional[float] = None
    indent_start: Optional[float] = None
    indent_end: Optional[float] = None
    space_above: Optional[float] = None
    space_below: Optional[float] = None
    named_style_type: Optional[str] = None
    direction: Optional[str] = None
    keep_lines_together: Optional[bool] = None
    keep_with_next: Optional[bool] = None
    avoid_widow_and_orphan: Optional[bool] = None
    page_break_before: Optional[bool] = None
    spacing_mode: Optional[str] = None
    shading_color: Optional[str] = None


class UpdateTableCellStyleOperation(StrictDocOperation):
    type: Literal["update_table_cell_style"]
    table_start_index: int
    background_color: Optional[str] = None
    border_color: Optional[str] = None
    border_width: Optional[float] = None
    padding_top: Optional[float] = None
    padding_bottom: Optional[float] = None
    padding_left: Optional[float] = None
    padding_right: Optional[float] = None
    content_alignment: Optional[str] = None
    row_index: Optional[int] = None
    column_index: Optional[int] = None
    row_span: Optional[int] = None
    column_span: Optional[int] = None


class InsertTableOperation(SegmentTargetDocOperation):
    type: Literal["insert_table"]
    rows: int
    columns: int
    index: Optional[int] = Field(
        default=None,
        description="Insertion index. Omit when end_of_segment=true.",
    )
    end_of_segment: bool = Field(
        default=False,
        description="Append to the end of the targeted body/segment instead of using index.",
    )

    @model_validator(mode="after")
    def validate_location(self) -> "InsertTableOperation":
        if self.end_of_segment == (self.index is not None):
            raise ValueError("Provide exactly one of 'index' or 'end_of_segment=true'.")
        return self


class InsertTableRowOperation(StrictDocOperation):
    type: Literal["insert_table_row"]
    table_start_index: int
    row_index: int
    insert_below: bool = True


class DeleteTableRowOperation(StrictDocOperation):
    type: Literal["delete_table_row"]
    table_start_index: int
    row_index: int


class InsertTableColumnOperation(StrictDocOperation):
    type: Literal["insert_table_column"]
    table_start_index: int
    column_index: int
    insert_right: bool = True


class DeleteTableColumnOperation(StrictDocOperation):
    type: Literal["delete_table_column"]
    table_start_index: int
    column_index: int


class MergeTableCellsOperation(StrictDocOperation):
    type: Literal["merge_table_cells"]
    table_start_index: int
    row_index: int
    column_index: int
    row_span: int
    column_span: int


class UnmergeTableCellsOperation(StrictDocOperation):
    type: Literal["unmerge_table_cells"]
    table_start_index: int
    row_index: int
    column_index: int
    row_span: int
    column_span: int


class UpdateTableColumnPropertiesOperation(StrictDocOperation):
    type: Literal["update_table_column_properties"]
    table_start_index: int
    column_indices: list[int]
    width: Optional[float] = None
    width_type: Optional[str] = None


class InsertPageBreakOperation(StrictDocOperation):
    type: Literal["insert_page_break"]
    index: Optional[int] = Field(
        default=None,
        description="Insertion index. Omit when end_of_segment=true.",
    )
    end_of_segment: bool = Field(
        default=False,
        description="Append to the end of the body instead of using index.",
    )

    @model_validator(mode="after")
    def validate_location(self) -> "InsertPageBreakOperation":
        if self.end_of_segment == (self.index is not None):
            raise ValueError("Provide exactly one of 'index' or 'end_of_segment=true'.")
        return self


class InsertSectionBreakOperation(StrictDocOperation):
    type: Literal["insert_section_break"]
    index: Optional[int] = Field(
        default=None,
        description="Insertion index. Omit when end_of_segment=true.",
    )
    end_of_segment: bool = Field(
        default=False,
        description="Append to the end of the body instead of using index.",
    )
    section_type: Literal["CONTINUOUS", "NEXT_PAGE"] = "NEXT_PAGE"

    @model_validator(mode="after")
    def validate_location(self) -> "InsertSectionBreakOperation":
        if self.end_of_segment == (self.index is not None):
            raise ValueError("Provide exactly one of 'index' or 'end_of_segment=true'.")
        return self


class FindReplaceOperation(StrictDocOperation):
    type: Literal["find_replace"]
    find_text: str
    replace_text: str
    match_case: bool = False


class CreateBulletListOperation(SegmentTargetDocOperation):
    type: Literal["create_bullet_list"]
    start_index: int
    end_index: int
    list_type: Literal["UNORDERED", "ORDERED", "CHECKBOX", "NONE"] = "UNORDERED"
    nesting_level: Optional[int] = None
    paragraph_start_indices: Optional[list[int]] = None
    bullet_preset: Optional[str] = None


class CreateNamedRangeOperation(SegmentTargetDocOperation):
    type: Literal["create_named_range"]
    name: str
    start_index: int
    end_index: int


class ReplaceNamedRangeContentOperation(StrictDocOperation):
    type: Literal["replace_named_range_content"]
    text: str
    named_range_id: Optional[str] = None
    named_range_name: Optional[str] = None

    @model_validator(mode="after")
    def validate_named_range_target(self) -> "ReplaceNamedRangeContentOperation":
        if bool(self.named_range_id) == bool(self.named_range_name):
            raise ValueError(
                "Provide exactly one of 'named_range_id' or 'named_range_name'."
            )
        return self


class DeleteNamedRangeOperation(StrictDocOperation):
    type: Literal["delete_named_range"]
    named_range_id: Optional[str] = None
    named_range_name: Optional[str] = None

    @model_validator(mode="after")
    def validate_named_range_target(self) -> "DeleteNamedRangeOperation":
        if bool(self.named_range_id) == bool(self.named_range_name):
            raise ValueError(
                "Provide exactly one of 'named_range_id' or 'named_range_name'."
            )
        return self


class UpdateDocumentStyleOperation(StrictDocOperation):
    type: Literal["update_document_style"]
    background_color: Optional[str] = None
    margin_top: Optional[float] = None
    margin_bottom: Optional[float] = None
    margin_left: Optional[float] = None
    margin_right: Optional[float] = None
    margin_header: Optional[float] = None
    margin_footer: Optional[float] = None
    page_width: Optional[float] = None
    page_height: Optional[float] = None
    page_number_start: Optional[int] = None
    use_even_page_header_footer: Optional[bool] = None
    use_first_page_header_footer: Optional[bool] = None
    flip_page_orientation: Optional[bool] = None
    document_mode: Optional[Literal["PAGES", "PAGELESS"]] = None


class UpdateSectionStyleOperation(StrictDocOperation):
    type: Literal["update_section_style"]
    start_index: int
    end_index: int
    margin_top: Optional[float] = None
    margin_bottom: Optional[float] = None
    margin_left: Optional[float] = None
    margin_right: Optional[float] = None
    margin_header: Optional[float] = None
    margin_footer: Optional[float] = None
    page_number_start: Optional[int] = None
    use_first_page_header_footer: Optional[bool] = None
    flip_page_orientation: Optional[bool] = None
    content_direction: Optional[Literal["LEFT_TO_RIGHT", "RIGHT_TO_LEFT"]] = None
    column_count: Optional[int] = None
    column_spacing: Optional[float] = None
    column_separator_style: Optional[Literal["NONE", "BETWEEN_EACH_COLUMN"]] = None


class CreateHeaderFooterOperation(StrictDocOperation):
    type: Literal["create_header_footer"]
    section_type: Literal["header", "footer"] = Field(
        description="Which section to create."
    )
    header_footer_type: Literal["DEFAULT", "FIRST_PAGE_ONLY", "EVEN_PAGE"] = Field(
        default="DEFAULT",
        description="Header/footer type to create.",
    )
    section_break_index: Optional[int] = Field(
        default=None,
        description="Optional section break index for section-scoped layouts.",
    )


class InsertImageOperation(SegmentTargetDocOperation):
    type: Literal["insert_image"]
    image_uri: str = Field(description="Image URL or resolvable image URI.")
    index: Optional[int] = Field(
        default=None,
        description="Insertion index. Omit when end_of_segment=true.",
    )
    width: Optional[int] = None
    height: Optional[int] = None
    end_of_segment: bool = Field(
        default=False,
        description="Append to the end of the targeted body/segment instead of using index.",
    )

    @model_validator(mode="after")
    def validate_location(self) -> "InsertImageOperation":
        if self.end_of_segment == (self.index is not None):
            raise ValueError("Provide exactly one of 'index' or 'end_of_segment=true'.")
        return self


class InsertDocTabOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["insert_doc_tab"]
    title: str
    index: int
    parent_tab_id: Optional[str] = None


class DeleteDocTabOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["delete_doc_tab"]
    tab_id: str


class UpdateDocTabOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["update_doc_tab"]
    tab_id: str
    title: str


BatchDocOperation = Annotated[
    Union[
        InsertTextOperation,
        DeleteTextOperation,
        ReplaceTextOperation,
        FormatTextOperation,
        UpdateParagraphStyleOperation,
        UpdateTableCellStyleOperation,
        InsertTableOperation,
        InsertTableRowOperation,
        DeleteTableRowOperation,
        InsertTableColumnOperation,
        DeleteTableColumnOperation,
        MergeTableCellsOperation,
        UnmergeTableCellsOperation,
        UpdateTableColumnPropertiesOperation,
        InsertPageBreakOperation,
        InsertSectionBreakOperation,
        FindReplaceOperation,
        CreateBulletListOperation,
        CreateNamedRangeOperation,
        ReplaceNamedRangeContentOperation,
        DeleteNamedRangeOperation,
        UpdateDocumentStyleOperation,
        UpdateSectionStyleOperation,
        CreateHeaderFooterOperation,
        InsertImageOperation,
        InsertDocTabOperation,
        DeleteDocTabOperation,
        UpdateDocTabOperation,
    ],
    Field(discriminator="type"),
]

BatchDocOperations = Annotated[
    list[BatchDocOperation],
    BeforeValidator(_coerce_json_str_to_list),
]
