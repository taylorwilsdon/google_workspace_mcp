"""Tests for empty-paragraph and trailing-paragraph statistics in structure analysis."""

import json
from unittest.mock import Mock

import pytest

from gdocs import docs_tools
from gdocs.docs_structure import (
    EMPTY_PARAGRAPH_RANGE_LIMIT,
    analyze_document_complexity,
)


def _unwrap(tool):
    """Unwrap a FunctionTool + decorator chain to the original function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _paragraph(start, end, text, bullet=False):
    """Build a minimal Docs API paragraph element."""
    paragraph = {"elements": [{"textRun": {"content": text}}]}
    if bullet:
        paragraph["bullet"] = {"listId": "kix.list1", "nestingLevel": 0}
    return {"startIndex": start, "endIndex": end, "paragraph": paragraph}


def _section_break(start, end):
    return {"startIndex": start, "endIndex": end, "sectionBreak": {"sectionStyle": {}}}


def _doc(*elements):
    return {"body": {"content": list(elements)}}


def _extract_json(result):
    """Pull the JSON payload out of the tool's human-readable response."""
    payload = result.split("\n\n", 1)[1].rsplit("\n\nLink:", 1)[0]
    return json.loads(payload)


class TestEmptyParagraphStatistics:
    def test_document_without_empty_paragraphs(self):
        stats = analyze_document_complexity(
            _doc(
                _paragraph(1, 7, "Hello\n"),
                _paragraph(7, 13, "World\n"),
            )
        )

        assert stats["empty_paragraphs"] == 0
        assert stats["empty_paragraph_ranges"] == []

    def test_empty_paragraphs_are_counted_and_located(self):
        stats = analyze_document_complexity(
            _doc(
                _paragraph(1, 7, "Hello\n"),
                _paragraph(7, 8, "\n"),
                _paragraph(8, 14, "World\n"),
                _paragraph(14, 15, "\n"),
            )
        )

        assert stats["empty_paragraphs"] == 2
        assert stats["empty_paragraph_ranges"] == [
            {"start": 7, "end": 8},
            {"start": 14, "end": 15},
        ]

    def test_section_breaks_are_not_empty_paragraphs(self):
        stats = analyze_document_complexity(
            _doc(
                _section_break(0, 1),
                _paragraph(1, 7, "Hello\n"),
            )
        )

        assert stats["empty_paragraphs"] == 0
        assert stats["empty_paragraph_ranges"] == []


class TestEmptyParagraphRangeLimit:
    @staticmethod
    def _doc_with_empty_paragraphs(count):
        elements, index = [], 1
        for _ in range(count):
            elements.append(_paragraph(index, index + 1, "\n"))
            index += 1
        return _doc(*elements)

    def test_ranges_are_capped_but_count_is_not(self):
        over_limit = EMPTY_PARAGRAPH_RANGE_LIMIT + 50
        stats = analyze_document_complexity(self._doc_with_empty_paragraphs(over_limit))

        assert stats["empty_paragraphs"] == over_limit
        assert len(stats["empty_paragraph_ranges"]) == EMPTY_PARAGRAPH_RANGE_LIMIT
        assert stats["empty_paragraph_ranges_truncated"] is True

    def test_ranges_at_the_limit_are_not_flagged(self):
        stats = analyze_document_complexity(
            self._doc_with_empty_paragraphs(EMPTY_PARAGRAPH_RANGE_LIMIT)
        )

        assert len(stats["empty_paragraph_ranges"]) == EMPTY_PARAGRAPH_RANGE_LIMIT
        assert stats["empty_paragraph_ranges_truncated"] is False


class TestLastParagraphStatistics:
    def test_reports_trailing_list_item(self):
        stats = analyze_document_complexity(
            _doc(
                _paragraph(1, 7, "Hello\n"),
                _paragraph(7, 13, "Item\n", bullet=True),
            )
        )

        assert stats["last_paragraph"] == {"is_list_item": True, "is_empty": False}

    def test_reports_trailing_plain_paragraph(self):
        stats = analyze_document_complexity(
            _doc(
                _paragraph(1, 7, "Item\n", bullet=True),
                _paragraph(7, 13, "Hello\n"),
            )
        )

        assert stats["last_paragraph"] == {"is_list_item": False, "is_empty": False}

    def test_reports_trailing_empty_paragraph(self):
        stats = analyze_document_complexity(
            _doc(
                _paragraph(1, 7, "Hello\n"),
                _paragraph(7, 8, "\n"),
            )
        )

        assert stats["last_paragraph"] == {"is_list_item": False, "is_empty": True}

    def test_ignores_trailing_non_paragraph_elements(self):
        stats = analyze_document_complexity(
            _doc(
                _paragraph(1, 7, "Item\n", bullet=True),
                _section_break(7, 8),
            )
        )

        assert stats["last_paragraph"] == {"is_list_item": True, "is_empty": False}

    def test_document_without_paragraphs(self):
        stats = analyze_document_complexity(_doc(_section_break(0, 1)))

        assert stats["empty_paragraphs"] == 0
        assert stats["empty_paragraph_ranges"] == []
        assert stats["last_paragraph"] is None


class TestInspectDocStructureReportsStatistics:
    @staticmethod
    def _service(doc):
        service = Mock()
        service.documents.return_value.get.return_value.execute = Mock(return_value=doc)
        return service

    @pytest.mark.asyncio
    async def test_basic_and_detailed_modes_agree(self):
        doc = _doc(
            _paragraph(1, 7, "Hello\n"),
            _paragraph(7, 8, "\n"),
            _paragraph(8, 14, "Item\n", bullet=True),
        )

        basic = _extract_json(
            await _unwrap(docs_tools.inspect_doc_structure)(
                service=self._service(doc),
                user_google_email="user@example.com",
                document_id="doc123",
            )
        )
        detailed = _extract_json(
            await _unwrap(docs_tools.inspect_doc_structure)(
                service=self._service(doc),
                user_google_email="user@example.com",
                document_id="doc123",
                detailed=True,
            )
        )

        expected = {
            "empty_paragraphs": 1,
            "empty_paragraph_ranges": [{"start": 7, "end": 8}],
            "empty_paragraph_ranges_truncated": False,
            "last_paragraph": {"is_list_item": True, "is_empty": False},
        }

        for key, value in expected.items():
            assert basic[key] == value
            assert detailed["statistics"][key] == value

    @pytest.mark.asyncio
    async def test_existing_statistics_keys_are_preserved(self):
        doc = _doc(_paragraph(1, 7, "Hello\n"))

        basic = _extract_json(
            await _unwrap(docs_tools.inspect_doc_structure)(
                service=self._service(doc),
                user_google_email="user@example.com",
                document_id="doc123",
            )
        )

        for key in (
            "total_elements",
            "tables",
            "paragraphs",
            "section_breaks",
            "total_length",
            "has_headers",
            "has_footers",
        ):
            assert key in basic

    @pytest.mark.asyncio
    async def test_detailed_elements_expose_list_membership(self):
        doc = _doc(
            _paragraph(1, 7, "Hello\n"),
            _paragraph(7, 13, "Item\n", bullet=True),
        )

        detailed = _extract_json(
            await _unwrap(docs_tools.inspect_doc_structure)(
                service=self._service(doc),
                user_google_email="user@example.com",
                document_id="doc123",
                detailed=True,
            )
        )

        paragraphs = [e for e in detailed["elements"] if e["type"] == "paragraph"]
        assert [p["is_list_item"] for p in paragraphs] == [False, True]
