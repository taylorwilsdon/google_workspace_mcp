"""
Tests for section-addressable markdown editing tools.

Covers the three section-edit tools (replace_doc_section_by_heading,
append_doc_after_heading, replace_doc_fully_from_markdown) and the
shared helpers in gdocs.docs_structure (find_heading_range,
body_protected_end_index, count_footnote_refs_in_range).

Mocking convention: unittest.mock + per-file _unwrap helper, matching
tests/gdocs/test_advanced_doc_formatting.py:21-26.
"""

from unittest.mock import Mock

import pytest

from core.utils import UserInputError
from gdocs import docs_tools
from gdocs.docs_structure import (
    body_protected_end_index,
    count_footnote_refs_in_range,
    find_heading_range,
)


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _paragraph(start, end, text, style="NORMAL_TEXT"):
    return {
        "startIndex": start,
        "endIndex": end,
        "paragraph": {
            "elements": [{"textRun": {"content": text}}],
            "paragraphStyle": {"namedStyleType": style},
        },
    }


def _heading(start, end, text, level):
    return _paragraph(start, end, text, style=f"HEADING_{level}")


def _doc(content, revision_id="rev-1"):
    return {"revisionId": revision_id, "body": {"content": content}}


class TestFindHeadingRange:
    def test_single_match(self):
        doc = _doc(
            [
                _heading(1, 12, "Intro\n", 1),
                _paragraph(12, 30, "body text here\n"),
                _heading(30, 40, "Next\n", 1),
                _paragraph(40, 60, "more body\n"),
            ]
        )
        start, end, fn_count = find_heading_range(doc, "Intro", None, "first", None)
        assert (start, end, fn_count) == (1, 30, 0)

    def test_terminates_at_equal_level_heading(self):
        doc = _doc(
            [
                _heading(1, 10, "A\n", 1),
                _heading(10, 22, "B (h2)\n", 2),
                _paragraph(22, 40, "body\n"),
                _heading(40, 50, "C\n", 1),
            ]
        )
        start, end, _ = find_heading_range(doc, "B (h2)", 2, "first", None)
        assert (start, end) == (10, 40)

    def test_terminates_at_shallower_heading(self):
        doc = _doc(
            [
                _heading(1, 12, "h2 head\n", 2),
                _paragraph(12, 25, "body\n"),
                _heading(25, 35, "h1 head\n", 1),
                _paragraph(35, 45, "tail\n"),
            ]
        )
        start, end, _ = find_heading_range(doc, "h2 head", None, "first", None)
        assert (start, end) == (1, 25)

    def test_title_terminates_section(self):
        doc = _doc(
            [
                _heading(1, 12, "h1\n", 1),
                _paragraph(12, 25, "body\n"),
                _paragraph(25, 35, "title\n", style="TITLE"),
            ]
        )
        start, end, _ = find_heading_range(doc, "h1", None, "first", None)
        assert (start, end) == (1, 25)

    def test_subtitle_does_not_terminate(self):
        doc = _doc(
            [
                _heading(1, 12, "h1\n", 1),
                _paragraph(12, 25, "subtitle\n", style="SUBTITLE"),
                _paragraph(25, 40, "body\n"),
            ]
        )
        start, end, _ = find_heading_range(doc, "h1", None, "first", None)
        # SUBTITLE is not a terminator; section extends to body end
        # (body_protected_end_index is endIndex of last paragraph - 1 = 39).
        assert (start, end) == (1, 39)

    def test_no_match_raises(self):
        doc = _doc([_heading(1, 12, "Intro\n", 1)])
        with pytest.raises(UserInputError, match="No heading matching"):
            find_heading_range(doc, "Missing", None, "first", None)

    def test_match_exact_multiple_raises(self):
        doc = _doc(
            [
                _heading(1, 10, "Notes\n", 1),
                _paragraph(10, 20, "a\n"),
                _heading(20, 30, "Notes\n", 1),
            ]
        )
        with pytest.raises(UserInputError, match="Multiple headings"):
            find_heading_range(doc, "Notes", None, "exact", None)

    def test_match_first_with_duplicates_returns_first(self):
        doc = _doc(
            [
                _heading(1, 10, "Notes\n", 1),
                _paragraph(10, 20, "a\n"),
                _heading(20, 30, "Notes\n", 1),
            ]
        )
        start, end, _ = find_heading_range(doc, "Notes", None, "first", None)
        # First "Notes" terminates at the second "Notes" (same level).
        assert (start, end) == (1, 20)

    def test_heading_level_filter(self):
        doc = _doc(
            [
                _heading(1, 10, "Summary\n", 1),
                _paragraph(10, 20, "a\n"),
                _heading(20, 30, "Summary\n", 3),
            ]
        )
        start, end, _ = find_heading_range(doc, "Summary", 3, "first", None)
        # Only the level-3 match qualifies; that's at index 20.
        assert start == 20

    def test_heading_with_inline_object_rejected(self):
        # Heading paragraph containing a footnoteReference — _extract_paragraph_text
        # silently skips it so matched text is unreliable.
        doc = _doc(
            [
                {
                    "startIndex": 1,
                    "endIndex": 12,
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "Intro"}},
                            {"footnoteReference": {"footnoteId": "kix.f1"}},
                            {"textRun": {"content": "\n"}},
                        ],
                        "paragraphStyle": {"namedStyleType": "HEADING_1"},
                    },
                }
            ]
        )
        with pytest.raises(UserInputError, match="inline objects"):
            find_heading_range(doc, "Intro", None, "first", None)

    def test_namedstyletype_absent_defaults_to_normal(self):
        # No namedStyleType key -> defaults to NORMAL_TEXT, not a heading.
        doc = _doc(
            [
                {
                    "startIndex": 1,
                    "endIndex": 12,
                    "paragraph": {
                        "elements": [{"textRun": {"content": "Plain\n"}}],
                        "paragraphStyle": {},  # no namedStyleType
                    },
                }
            ]
        )
        with pytest.raises(UserInputError, match="No heading matching"):
            find_heading_range(doc, "Plain", None, "first", None)

    def test_multi_tab_requires_tab_id(self):
        doc = {
            "revisionId": "r",
            "tabs": [
                {
                    "tabProperties": {"tabId": "t-A"},
                    "documentTab": {
                        "body": {"content": [_heading(1, 12, "Intro\n", 1)]}
                    },
                },
                {
                    "tabProperties": {"tabId": "t-B"},
                    "documentTab": {
                        "body": {"content": [_heading(1, 12, "Other\n", 1)]}
                    },
                },
            ],
        }
        with pytest.raises(UserInputError, match="tab_id is required"):
            find_heading_range(doc, "Intro", None, "first", None)
        # With tab_id, succeeds.
        start, _end, _fn = find_heading_range(doc, "Intro", None, "first", "t-A")
        assert start == 1

    def test_footnote_count_included(self):
        doc = _doc(
            [
                _heading(1, 12, "Intro\n", 1),
                {
                    "startIndex": 12,
                    "endIndex": 28,
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "see"}, "startIndex": 12},
                            {
                                "footnoteReference": {"footnoteId": "f1"},
                                "startIndex": 16,
                            },
                            {"textRun": {"content": " note\n"}, "startIndex": 17},
                        ],
                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    },
                },
            ]
        )
        _start, _end, fn_count = find_heading_range(doc, "Intro", None, "first", None)
        assert fn_count == 1


class TestBodyProtectedEndIndex:
    def test_empty_body_returns_one(self):
        doc = _doc([])
        assert body_protected_end_index(doc) == 1

    def test_last_paragraph_minus_one(self):
        doc = _doc(
            [
                _heading(1, 10, "A\n", 1),
                _paragraph(10, 25, "body\n"),
            ]
        )
        assert body_protected_end_index(doc) == 24

    def test_walks_backward_when_last_element_not_paragraph(self):
        # Last element is a sectionBreak; helper should walk backward to the
        # paragraph before it.
        doc = _doc(
            [
                _heading(1, 10, "A\n", 1),
                _paragraph(10, 25, "body\n"),
                {"startIndex": 25, "endIndex": 26, "sectionBreak": {}},
            ]
        )
        # Last paragraph endIndex 25 - 1 = 24.
        assert body_protected_end_index(doc) == 24


class TestCountFootnoteRefsInRange:
    def test_zero(self):
        doc = _doc([_paragraph(1, 12, "no foots\n")])
        assert count_footnote_refs_in_range(doc, None, 1, 12) == 0

    def test_one_in_range(self):
        doc = _doc(
            [
                {
                    "startIndex": 1,
                    "endIndex": 20,
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "see"}, "startIndex": 1},
                            {
                                "footnoteReference": {"footnoteId": "f1"},
                                "startIndex": 5,
                            },
                            {"textRun": {"content": " here\n"}, "startIndex": 6},
                        ]
                    },
                }
            ]
        )
        assert count_footnote_refs_in_range(doc, None, 1, 20) == 1
        # Out of range:
        assert count_footnote_refs_in_range(doc, None, 6, 20) == 0


class TestReplaceDocSectionByHeading:
    @pytest.mark.asyncio
    async def test_emits_single_batchupdate_with_writecontrol(self):
        fn = _unwrap(docs_tools.replace_doc_section_by_heading)
        mock_service = Mock()
        mock_get = Mock()
        mock_get.execute = Mock(
            return_value=_doc(
                [
                    _heading(1, 10, "A\n", 1),
                    _paragraph(10, 25, "old body\n"),
                    _heading(25, 35, "B\n", 1),
                ],
                revision_id="rev-XYZ",
            )
        )
        mock_service.documents().get.return_value = mock_get

        captured = {}

        def _batch(documentId, body):
            captured["documentId"] = documentId
            captured["body"] = body
            return Mock(execute=Mock(return_value={}))

        mock_service.documents().batchUpdate.side_effect = _batch

        result = await fn(
            mock_service,
            "user@example.com",
            "doc-123",
            "A",
            "## A\n\nNew body.",
        )

        assert captured["documentId"] == "doc-123"
        assert captured["body"]["writeControl"]["requiredRevisionId"] == "rev-XYZ"
        requests = captured["body"]["requests"]
        # First request must be the delete; inserts follow.
        assert "deleteContentRange" in requests[0]
        assert requests[0]["deleteContentRange"]["range"] == {
            "startIndex": 1,
            "endIndex": 25,
        }
        # Subsequent requests are inserts/styles from markdown_to_docs_requests.
        assert any("insertText" in r for r in requests[1:])
        assert "doc-123" in result
        assert "Link:" in result

    @pytest.mark.asyncio
    async def test_footnote_in_section_raises_before_api(self):
        fn = _unwrap(docs_tools.replace_doc_section_by_heading)
        mock_service = Mock()
        mock_get = Mock()
        mock_get.execute = Mock(
            return_value=_doc(
                [
                    _heading(1, 10, "A\n", 1),
                    {
                        "startIndex": 10,
                        "endIndex": 25,
                        "paragraph": {
                            "elements": [
                                {"textRun": {"content": "see"}, "startIndex": 10},
                                {
                                    "footnoteReference": {"footnoteId": "f1"},
                                    "startIndex": 14,
                                },
                                {
                                    "textRun": {"content": " here\n"},
                                    "startIndex": 15,
                                },
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        },
                    },
                ]
            )
        )
        mock_service.documents().get.return_value = mock_get

        with pytest.raises(UserInputError, match="footnote reference"):
            await fn(mock_service, "user@example.com", "doc-1", "A", "anything")
        # batchUpdate must NOT be called.
        mock_service.documents().batchUpdate.assert_not_called()

    @pytest.mark.asyncio
    async def test_revision_id_missing_raises(self):
        fn = _unwrap(docs_tools.replace_doc_section_by_heading)
        mock_service = Mock()
        mock_get = Mock()
        mock_get.execute = Mock(
            return_value={"body": {"content": [_heading(1, 10, "A\n", 1)]}}
        )
        mock_service.documents().get.return_value = mock_get
        with pytest.raises(UserInputError, match="WriteControl unavailable"):
            await fn(mock_service, "user@example.com", "doc-1", "A", "x")


class TestAppendDocAfterHeading:
    @pytest.mark.asyncio
    async def test_insert_only_at_section_end(self):
        fn = _unwrap(docs_tools.append_doc_after_heading)
        mock_service = Mock()
        mock_service.documents().get.return_value = Mock(
            execute=Mock(
                return_value=_doc(
                    [
                        _heading(1, 10, "A\n", 1),
                        _paragraph(10, 25, "body\n"),
                        _heading(25, 35, "B\n", 1),
                    ]
                )
            )
        )
        captured = {}

        def _batch(documentId, body):
            captured["body"] = body
            return Mock(execute=Mock(return_value={}))

        mock_service.documents().batchUpdate.side_effect = _batch

        await fn(mock_service, "user@example.com", "doc-1", "A", "**new para**")

        requests = captured["body"]["requests"]
        # No delete; first request should be an insert at section_end (25).
        assert all("deleteContentRange" not in r for r in requests)
        first_insert = next(r for r in requests if "insertText" in r)
        assert first_insert["insertText"]["location"]["index"] == 25


class TestReplaceDocFullyFromMarkdown:
    @pytest.mark.asyncio
    async def test_full_body_replace(self):
        fn = _unwrap(docs_tools.replace_doc_fully_from_markdown)
        mock_service = Mock()
        mock_service.documents().get.return_value = Mock(
            execute=Mock(
                return_value=_doc(
                    [
                        _heading(1, 10, "Old\n", 1),
                        _paragraph(10, 30, "old body\n"),
                    ]
                )
            )
        )
        captured = {}

        def _batch(documentId, body):
            captured["body"] = body
            return Mock(execute=Mock(return_value={}))

        mock_service.documents().batchUpdate.side_effect = _batch

        await fn(mock_service, "user@example.com", "doc-1", "# Fresh\n\nbody")

        requests = captured["body"]["requests"]
        # First request is the body delete (1, 29 — last paragraph endIndex 30 -1).
        assert requests[0]["deleteContentRange"]["range"] == {
            "startIndex": 1,
            "endIndex": 29,
        }
        # writeControl present.
        assert "writeControl" in captured["body"]

    @pytest.mark.asyncio
    async def test_empty_body_skips_delete(self):
        fn = _unwrap(docs_tools.replace_doc_fully_from_markdown)
        mock_service = Mock()
        # Empty body — body_protected_end_index returns 1, skipping delete.
        mock_service.documents().get.return_value = Mock(
            execute=Mock(return_value=_doc([]))
        )
        captured = {}

        def _batch(documentId, body):
            captured["body"] = body
            return Mock(execute=Mock(return_value={}))

        mock_service.documents().batchUpdate.side_effect = _batch

        await fn(mock_service, "user@example.com", "doc-1", "# New")
        requests = captured["body"]["requests"]
        assert all("deleteContentRange" not in r for r in requests)
