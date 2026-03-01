"""
E2E tests for Google Docs helper functions with mocked responses.

Tests cover:
- build_text_style: style object construction
- create_insert_text_request: insert text API requests
- create_delete_range_request: delete range API requests
- create_format_text_request: text formatting requests
- create_find_replace_request: find/replace operations
- create_insert_table_request: table insertion
- create_insert_page_break_request: page break insertion
"""

import pytest
from gdocs.docs_helpers import (
    build_text_style,
    create_insert_text_request,
    create_insert_text_segment_request,
    create_delete_range_request,
    create_format_text_request,
    create_find_replace_request,
    create_insert_table_request,
    create_insert_page_break_request,
    create_insert_image_request,
    create_bullet_list_request,
    validate_operation,
)


class TestBuildTextStyle:
    """Tests for build_text_style helper."""

    def test_no_styles(self):
        style, fields = build_text_style()
        assert style == {}
        assert fields == []

    def test_bold_only(self):
        style, fields = build_text_style(bold=True)
        assert style == {"bold": True}
        assert fields == ["bold"]

    def test_italic_only(self):
        style, fields = build_text_style(italic=True)
        assert style == {"italic": True}
        assert fields == ["italic"]

    def test_underline_only(self):
        style, fields = build_text_style(underline=True)
        assert style == {"underline": True}
        assert fields == ["underline"]

    def test_font_size(self):
        style, fields = build_text_style(font_size=14)
        assert style["fontSize"]["magnitude"] == 14
        assert style["fontSize"]["unit"] == "PT"
        assert "fontSize" in fields

    def test_font_family(self):
        style, fields = build_text_style(font_family="Arial")
        assert style["weightedFontFamily"]["fontFamily"] == "Arial"
        assert "weightedFontFamily" in fields

    def test_all_styles_combined(self):
        style, fields = build_text_style(
            bold=True, italic=True, underline=False,
            font_size=12, font_family="Roboto"
        )
        assert style["bold"] is True
        assert style["italic"] is True
        assert style["underline"] is False
        assert style["fontSize"]["magnitude"] == 12
        assert style["weightedFontFamily"]["fontFamily"] == "Roboto"
        assert len(fields) == 5

    def test_false_values_included(self):
        """False values should be included (to explicitly turn off formatting)."""
        style, fields = build_text_style(bold=False)
        assert style == {"bold": False}
        assert fields == ["bold"]


class TestCreateInsertTextRequest:
    """Tests for create_insert_text_request."""

    def test_basic_insert(self):
        req = create_insert_text_request(1, "Hello")
        assert req["insertText"]["location"]["index"] == 1
        assert req["insertText"]["text"] == "Hello"

    def test_insert_at_beginning(self):
        req = create_insert_text_request(0, "Start")
        assert req["insertText"]["location"]["index"] == 0

    def test_insert_with_newlines(self):
        req = create_insert_text_request(5, "Line1\nLine2\n")
        assert req["insertText"]["text"] == "Line1\nLine2\n"


class TestCreateInsertTextSegmentRequest:
    """Tests for create_insert_text_segment_request."""

    def test_header_segment(self):
        req = create_insert_text_segment_request(0, "Header Text", "header_1")
        assert req["insertText"]["location"]["segmentId"] == "header_1"
        assert req["insertText"]["location"]["index"] == 0
        assert req["insertText"]["text"] == "Header Text"

    def test_footer_segment(self):
        req = create_insert_text_segment_request(0, "Footer", "footer_1")
        assert req["insertText"]["location"]["segmentId"] == "footer_1"


class TestCreateDeleteRangeRequest:
    """Tests for create_delete_range_request."""

    def test_basic_delete(self):
        req = create_delete_range_request(5, 10)
        range_obj = req["deleteContentRange"]["range"]
        assert range_obj["startIndex"] == 5
        assert range_obj["endIndex"] == 10

    def test_single_character_delete(self):
        req = create_delete_range_request(3, 4)
        range_obj = req["deleteContentRange"]["range"]
        assert range_obj["endIndex"] - range_obj["startIndex"] == 1


class TestCreateFormatTextRequest:
    """Tests for create_format_text_request."""

    def test_bold_format(self):
        req = create_format_text_request(0, 10, bold=True)
        assert req is not None
        assert req["updateTextStyle"]["range"]["startIndex"] == 0
        assert req["updateTextStyle"]["range"]["endIndex"] == 10
        assert req["updateTextStyle"]["textStyle"]["bold"] is True
        assert "bold" in req["updateTextStyle"]["fields"]

    def test_multiple_styles(self):
        req = create_format_text_request(5, 20, bold=True, italic=True, font_size=16)
        assert req is not None
        assert req["updateTextStyle"]["textStyle"]["bold"] is True
        assert req["updateTextStyle"]["textStyle"]["italic"] is True
        assert "fontSize" in req["updateTextStyle"]["fields"]

    def test_no_styles_returns_none(self):
        req = create_format_text_request(0, 10)
        assert req is None


class TestCreateFindReplaceRequest:
    """Tests for create_find_replace_request."""

    def test_basic_replace(self):
        req = create_find_replace_request("old", "new")
        assert req["replaceAllText"]["containsText"]["text"] == "old"
        assert req["replaceAllText"]["replaceText"] == "new"
        assert req["replaceAllText"]["containsText"]["matchCase"] is False

    def test_case_sensitive_replace(self):
        req = create_find_replace_request("Old", "New", match_case=True)
        assert req["replaceAllText"]["containsText"]["matchCase"] is True

    def test_replace_with_empty_string(self):
        req = create_find_replace_request("remove_me", "")
        assert req["replaceAllText"]["replaceText"] == ""


class TestCreateInsertTableRequest:
    """Tests for create_insert_table_request."""

    def test_basic_table(self):
        req = create_insert_table_request(1, 3, 4)
        assert req["insertTable"]["location"]["index"] == 1
        assert req["insertTable"]["rows"] == 3
        assert req["insertTable"]["columns"] == 4

    def test_single_cell_table(self):
        req = create_insert_table_request(0, 1, 1)
        assert req["insertTable"]["rows"] == 1
        assert req["insertTable"]["columns"] == 1


class TestCreateInsertPageBreakRequest:
    """Tests for create_insert_page_break_request."""

    def test_basic_page_break(self):
        req = create_insert_page_break_request(10)
        assert req["insertPageBreak"]["location"]["index"] == 10


class TestCreateInsertImageRequest:
    """Tests for create_insert_image_request."""

    def test_basic_image(self):
        req = create_insert_image_request(5, "https://example.com/image.png")
        assert req["insertInlineImage"]["location"]["index"] == 5
        assert req["insertInlineImage"]["uri"] == "https://example.com/image.png"
        assert "objectSize" not in req["insertInlineImage"]

    def test_image_with_width(self):
        req = create_insert_image_request(1, "https://example.com/img.png", width=300)
        assert req["insertInlineImage"]["objectSize"]["width"]["magnitude"] == 300
        assert req["insertInlineImage"]["objectSize"]["width"]["unit"] == "PT"
        assert "height" not in req["insertInlineImage"]["objectSize"]

    def test_image_with_height(self):
        req = create_insert_image_request(1, "https://example.com/img.png", height=200)
        assert req["insertInlineImage"]["objectSize"]["height"]["magnitude"] == 200
        assert "width" not in req["insertInlineImage"]["objectSize"]

    def test_image_with_both_dimensions(self):
        req = create_insert_image_request(1, "https://x.com/i.png", width=400, height=300)
        size = req["insertInlineImage"]["objectSize"]
        assert size["width"]["magnitude"] == 400
        assert size["height"]["magnitude"] == 300


class TestCreateBulletListRequest:
    """Tests for create_bullet_list_request."""

    def test_unordered_list(self):
        req = create_bullet_list_request(1, 50, "UNORDERED")
        assert req["createParagraphBullets"]["range"]["startIndex"] == 1
        assert req["createParagraphBullets"]["range"]["endIndex"] == 50
        assert req["createParagraphBullets"]["bulletPreset"] == "BULLET_DISC_CIRCLE_SQUARE"

    def test_ordered_list(self):
        req = create_bullet_list_request(1, 50, "ORDERED")
        assert req["createParagraphBullets"]["bulletPreset"] == "NUMBERED_DECIMAL_ALPHA_ROMAN"

    def test_default_is_unordered(self):
        req = create_bullet_list_request(1, 10)
        assert req["createParagraphBullets"]["bulletPreset"] == "BULLET_DISC_CIRCLE_SQUARE"


class TestValidateOperation:
    """Tests for validate_operation."""

    def test_valid_insert_text(self):
        ok, msg = validate_operation({"type": "insert_text", "index": 1, "text": "hi"})
        assert ok is True
        assert msg == ""

    def test_valid_delete_text(self):
        ok, msg = validate_operation({"type": "delete_text", "start_index": 1, "end_index": 5})
        assert ok is True

    def test_valid_find_replace(self):
        ok, msg = validate_operation({"type": "find_replace", "find_text": "a", "replace_text": "b"})
        assert ok is True

    def test_missing_type(self):
        ok, msg = validate_operation({"index": 1})
        assert ok is False
        assert "type" in msg.lower()

    def test_unsupported_type(self):
        ok, msg = validate_operation({"type": "unknown_op"})
        assert ok is False
        assert "unsupported" in msg.lower()

    def test_missing_required_field(self):
        ok, msg = validate_operation({"type": "insert_text", "index": 1})
        assert ok is False
        assert "text" in msg.lower()

    def test_valid_insert_table(self):
        ok, msg = validate_operation({"type": "insert_table", "index": 1, "rows": 3, "columns": 2})
        assert ok is True

    def test_valid_format_text(self):
        ok, msg = validate_operation({"type": "format_text", "start_index": 0, "end_index": 10})
        assert ok is True
