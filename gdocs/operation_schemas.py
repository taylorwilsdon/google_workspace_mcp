"""
Typed Pydantic schemas for Google Docs batch operations.

These models are used to generate a richer MCP schema for batch_update_doc so
LLMs receive a machine-readable contract instead of a free-form object array.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union
from zoneinfo import available_timezones

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    BeforeValidator,
    field_validator,
    model_validator,
)


ParagraphBorderEdge = Literal["top", "bottom", "left", "right", "between"]
TableBorderEdge = Literal["top", "bottom", "left", "right"]
_IANA_TIME_ZONES = frozenset(available_timezones())


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


class InsertionLocationOperation(StrictDocOperation):
    """One explicit location or semantic anchor for an insertion."""

    index: Optional[int] = Field(
        default=None,
        description="Insertion index. Omit when end_of_segment=true.",
    )
    end_of_segment: bool = Field(
        default=False,
        description="Append to the end of the targeted body/segment instead of using index.",
    )
    after_heading: Optional[str] = Field(
        default=None,
        description=(
            "Insert immediately after the heading paragraph with this exact text, "
            "resolved to an index at execution time. Errors when it matches zero or "
            "more than one heading."
        ),
    )
    before_heading: Optional[str] = Field(
        default=None,
        description=(
            "Insert immediately before the heading paragraph with this exact text. "
            "Errors when it matches zero or more than one heading."
        ),
    )
    anchor_text: Optional[str] = Field(
        default=None,
        description=(
            "Insert relative to this literal text, which must occur exactly once "
            "within a single paragraph. Use anchor_position to pick which side."
        ),
    )
    anchor_position: Literal["before", "after"] = Field(
        default="after",
        description="Which side of anchor_text to insert on. Defaults to 'after'.",
    )

    @model_validator(mode="after")
    def validate_location(self) -> "InsertionLocationOperation":
        anchors = [self.after_heading, self.before_heading, self.anchor_text]
        provided = sum(
            [self.index is not None, self.end_of_segment]
            + [anchor is not None for anchor in anchors]
        )
        if provided != 1:
            raise ValueError(
                "Provide exactly one of 'index', 'end_of_segment=true', "
                "'after_heading', 'before_heading' or 'anchor_text'."
            )
        return self


class InsertTextOperation(InsertionLocationOperation, SegmentTargetDocOperation):
    type: Literal["insert_text"]
    text: str = Field(description="Text to insert.")


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
    font_size: Optional[float] = None
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
    border_edges: Optional[list[ParagraphBorderEdge]] = Field(
        default=None,
        min_length=1,
        description="Paragraph border edges to update; omit to update top, bottom, left, and right.",
    )
    border_color: Optional[str] = None
    border_width: Optional[float] = None
    border_padding: Optional[float] = None
    border_dash: Optional[str] = None


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
    border_edges: Optional[list[TableBorderEdge]] = Field(
        default=None,
        min_length=1,
        description="Table-cell border edges to update; omit to update all four edges.",
    )


class InsertTableOperation(InsertionLocationOperation, SegmentTargetDocOperation):
    type: Literal["insert_table"]
    rows: int
    columns: int


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


class UpdateTableRowStyleOperation(StrictDocOperation):
    type: Literal["update_table_row_style"]
    table_start_index: int
    row_indices: list[int] = Field(
        description="Zero-based row indices to style, e.g. [0] for the header row."
    )
    min_row_height: Optional[float] = Field(
        default=None,
        description="Minimum row height in points.",
    )


class PinTableHeaderRowsOperation(StrictDocOperation):
    type: Literal["pin_table_header_rows"]
    table_start_index: int
    pinned_header_rows_count: int = Field(
        ge=0,
        description="Number of leading rows to pin as a repeating header on each "
        "page. 0 unpins all rows. Use this dedicated request because the "
        "'tableHeader' value reported in TableRowStyle cannot be set through "
        "UpdateTableRowStyleRequest.",
    )


class InsertPageBreakOperation(InsertionLocationOperation):
    type: Literal["insert_page_break"]


class InsertSectionBreakOperation(InsertionLocationOperation):
    type: Literal["insert_section_break"]
    section_type: Literal["CONTINUOUS", "NEXT_PAGE"] = "NEXT_PAGE"


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


class InsertImageOperation(InsertionLocationOperation, SegmentTargetDocOperation):
    type: Literal["insert_image"]
    image_uri: str = Field(description="Image URL or resolvable image URI.")
    width: Optional[int] = None
    height: Optional[int] = None


class InsertPersonOperation(InsertionLocationOperation, SegmentTargetDocOperation):
    type: Literal["insert_person"]
    email: str = Field(
        min_length=1,
        description="Email of the person to @mention as a smart chip.",
    )


class InsertDateOperation(InsertionLocationOperation, SegmentTargetDocOperation):
    type: Literal["insert_date"]
    date: str = Field(
        min_length=1,
        description="YYYY-MM-DD, or an ISO 8601 timestamp with an offset (displayed in UTC unless timezone is set).",
    )
    date_format: Optional[
        Literal[
            "DATE_FORMAT_MONTH_DAY_ABBREVIATED",
            "DATE_FORMAT_MONTH_DAY_FULL",
            "DATE_FORMAT_MONTH_DAY_YEAR_ABBREVIATED",
            "DATE_FORMAT_ISO8601",
        ]
    ] = Field(
        default=None,
        description="Defaults to DATE_FORMAT_MONTH_DAY_YEAR_ABBREVIATED.",
    )
    time_format: Optional[
        Literal[
            "TIME_FORMAT_DISABLED",
            "TIME_FORMAT_HOUR_MINUTE",
            "TIME_FORMAT_HOUR_MINUTE_TIMEZONE",
        ]
    ] = Field(default=None, description="Omit to show no time.")
    timezone: Optional[str] = Field(
        default=None,
        description="IANA time zone ID shown on the chip. Requires time_format TIME_FORMAT_HOUR_MINUTE_TIMEZONE.",
    )

    @field_validator("date")
    @classmethod
    def _validate_date(cls, value: str) -> str:
        """Accept YYYY-MM-DD or an ISO 8601 timestamp that carries a time zone."""
        try:
            if len(value) == 10:
                datetime.strptime(value, "%Y-%m-%d")
            else:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if "T" not in value or parsed.tzinfo is None:
                    raise ValueError(value)
        except ValueError as exc:
            raise ValueError(
                "date must be YYYY-MM-DD or an ISO 8601 timestamp with a time zone, "
                "e.g. 2026-11-03T09:30:00Z"
            ) from exc
        return value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: Optional[str]) -> Optional[str]:
        """Reject names the IANA time zone database does not know."""
        if value is not None and value not in _IANA_TIME_ZONES:
            raise ValueError(f"unknown IANA time zone: {value}")
        return value

    @model_validator(mode="after")
    def validate_timezone_format(self) -> "InsertDateOperation":
        if self.timezone and self.time_format != "TIME_FORMAT_HOUR_MINUTE_TIMEZONE":
            raise ValueError(
                "timezone requires time_format TIME_FORMAT_HOUR_MINUTE_TIMEZONE."
            )
        return self


class InsertCommentOperation(SegmentTargetDocOperation):
    type: Literal["insert_comment"]
    content: str = Field(
        min_length=1,
        max_length=2048,
        description="Comment text as plain text.",
    )
    start_index: Optional[int] = Field(
        default=None, description="Start of the commented range. Pair with end_index."
    )
    end_index: Optional[int] = Field(
        default=None, description="End of the commented range. Pair with start_index."
    )
    quoted_text: Optional[str] = Field(
        default=None,
        description=(
            "Anchor the comment to this literal text, resolved to a range at "
            "execution time. Must occur exactly once within a single paragraph."
        ),
    )
    assignee_email: Optional[str] = Field(
        default=None, description="Optional email to assign the comment to."
    )

    @model_validator(mode="after")
    def validate_range(self) -> "InsertCommentOperation":
        has_indices = self.start_index is not None and self.end_index is not None
        has_partial = (self.start_index is None) != (self.end_index is None)
        if has_partial or has_indices == (self.quoted_text is not None):
            raise ValueError(
                "Provide exactly one of 'start_index'+'end_index' or 'quoted_text'."
            )
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
        UpdateTableRowStyleOperation,
        PinTableHeaderRowsOperation,
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
        InsertPersonOperation,
        InsertDateOperation,
        InsertCommentOperation,
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
