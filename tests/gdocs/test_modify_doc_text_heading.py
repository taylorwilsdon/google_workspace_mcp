"""Tests for the heading-addressed mode of modify_doc_text."""

from unittest.mock import Mock

import pytest

from core.utils import UserInputError
from gdocs import docs_tools
from gdocs.docs_markdown_writer import build_markdown_insert_requests
from gdocs.docs_structure import find_heading_section


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _para(start, text, style="NORMAL_TEXT", bullet=False, footnote=False):
    """Build a body paragraph element; text must end with a newline."""
    elements = [
        {
            "startIndex": start,
            "endIndex": start + len(text),
            "textRun": {"content": text},
        }
    ]
    if footnote:
        elements.insert(
            0, {"startIndex": start, "endIndex": start + 1, "footnoteReference": {}}
        )
    paragraph = {"paragraphStyle": {"namedStyleType": style}, "elements": elements}
    if bullet:
        paragraph["bullet"] = {"listId": "kix.list"}
    return {
        "startIndex": start,
        "endIndex": start + len(text),
        "paragraph": paragraph,
    }


# Indices: 1 "Intro\n" -> 7 "Status\n" -> 14 "Old body\n" -> 23 "Details\n" -> 31 "Tail\n" -> 36
def _doc(revision="rev-1"):
    return {
        "revisionId": revision,
        "body": {
            "content": [
                {"endIndex": 1, "sectionBreak": {}},
                _para(1, "Intro\n", "HEADING_1"),
                _para(7, "Status\n", "HEADING_2"),
                _para(14, "Old body\n"),
                _para(23, "Details\n", "HEADING_2"),
                _para(31, "Tail\n"),
            ]
        },
    }


class TestFindHeadingSection:
    def test_section_ends_at_next_equal_level_heading(self):
        section = find_heading_section(_doc(), "Status")
        assert section["body_start"] == 14
        assert section["section_end"] == 23
        assert section["footnote_refs"] == 0

    def test_deeper_headings_do_not_terminate_section(self):
        doc = _doc()
        doc["body"]["content"][4] = _para(23, "Details\n", "HEADING_3")
        section = find_heading_section(doc, "Status")
        assert section["section_end"] == 35  # before Tail's final newline

    def test_title_terminates_section(self):
        doc = _doc()
        doc["body"]["content"][4] = _para(23, "Details\n", "TITLE")
        assert find_heading_section(doc, "Status")["section_end"] == 23

    def test_last_section_stops_before_protected_newline(self):
        section = find_heading_section(_doc(), "Details")
        assert section["body_start"] == 31
        assert section["section_end"] == 35
        assert section["last_paragraph"]["startIndex"] == 31

    def test_heading_as_last_paragraph_has_empty_body(self):
        doc = _doc()
        doc["body"]["content"] = doc["body"]["content"][:5]
        section = find_heading_section(doc, "Details")
        assert section["body_start"] == 31
        assert section["section_end"] == 30
        assert section["last_paragraph"]["startIndex"] == 23

    def test_heading_level_filter(self):
        with pytest.raises(UserInputError, match="No heading matching"):
            find_heading_section(_doc(), "Status", heading_level=1)
        assert (
            find_heading_section(_doc(), "Status", heading_level=2)["body_start"] == 14
        )

    def test_body_paragraph_with_same_text_is_not_a_heading(self):
        with pytest.raises(UserInputError):
            find_heading_section(_doc(), "Old body")

    def test_counts_footnote_references_in_body(self):
        doc = _doc()
        doc["body"]["content"][3] = _para(14, "Old body\n", footnote=True)
        assert find_heading_section(doc, "Status")["footnote_refs"] == 1

    def test_multi_tab_requires_tab_id(self):
        doc = {
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.a"},
                    "documentTab": {"body": {"content": []}},
                },
                {
                    "tabProperties": {"tabId": "t.b"},
                    "documentTab": {"body": _doc()["body"]},
                },
            ]
        }
        with pytest.raises(UserInputError, match="tab_id is required"):
            find_heading_section(doc, "Status")
        assert find_heading_section(doc, "Status", tab_id="t.b")["body_start"] == 14
        with pytest.raises(UserInputError, match="not found"):
            find_heading_section(doc, "Status", tab_id="t.zzz")

    def test_child_tabs_are_resolved(self):
        child = {
            "tabProperties": {"tabId": "t.child"},
            "documentTab": {"body": _doc()["body"]},
        }
        parent = {
            "tabProperties": {"tabId": "t.parent"},
            "documentTab": {"body": {"content": [_para(1, "Status\n", "HEADING_2")]}},
            "childTabs": [child],
        }
        doc = {"tabs": [parent]}
        with pytest.raises(UserInputError, match="tab_id is required"):
            find_heading_section(doc, "Status")
        assert find_heading_section(doc, "Status", tab_id="t.child")["body_start"] == 14
        assert find_heading_section(doc, "Status", tab_id="t.parent")["body_start"] == 8

    def test_unknown_tab_id_on_single_tab_doc_is_rejected(self):
        doc = {
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.only"},
                    "documentTab": {"body": _doc()["body"]},
                }
            ]
        }
        with pytest.raises(UserInputError, match="not found"):
            find_heading_section(doc, "Status", tab_id="t.nope")

    def test_match_ignores_case_and_surrounding_whitespace(self):
        assert find_heading_section(_doc(), "  status ")["body_start"] == 14

    def test_duplicate_headings_are_refused(self):
        doc = _doc()
        doc["body"]["content"][4] = _para(23, "Status\n", "HEADING_2")
        with pytest.raises(UserInputError, match="matches 2 headings"):
            find_heading_section(doc, "Status")

    def test_heading_level_disambiguates_duplicates(self):
        doc = _doc()
        doc["body"]["content"][4] = _para(23, "Status\n", "HEADING_3")
        assert find_heading_section(doc, "Status", heading_level=3)["body_start"] == 30


class TestBuildMarkdownInsertRequests:
    def test_plain_insert(self):
        requests = build_markdown_insert_requests("Hello", 10)
        assert requests[0]["insertText"] == {
            "text": "Hello\n",
            "location": {"index": 10},
        }

    def test_split_and_drop_bullet_precede_markdown(self):
        requests = build_markdown_insert_requests(
            "Hello", 10, tab_id="t", split_paragraph=True, drop_bullet=True
        )
        assert requests[0] == {
            "insertText": {"text": "\n", "location": {"index": 10, "tabId": "t"}}
        }
        assert requests[1] == {
            "deleteParagraphBullets": {
                "range": {"startIndex": 11, "endIndex": 12, "tabId": "t"}
            }
        }
        assert requests[2]["insertText"]["location"]["index"] == 11

    def test_empty_markdown_yields_nothing(self):
        assert build_markdown_insert_requests("   ", 10, split_paragraph=True) == []


def _service(doc):
    service = Mock()
    docs = service.documents.return_value
    docs.get.return_value.execute.return_value = doc
    docs.batchUpdate.return_value.execute.return_value = {"replies": []}
    return service


async def _call(service, **kwargs):
    return await _unwrap(docs_tools.modify_doc_text)(
        service=service,
        user_google_email="test@example.com",
        document_id="d" * 25,
        **kwargs,
    )


def _batch_body(service):
    return service.documents.return_value.batchUpdate.call_args.kwargs["body"]


class TestModifyDocTextHeading:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("revision", ["rev-1", None])
    @pytest.mark.parametrize(
        "kwargs,request_type,operation",
        [
            ({"text": "New **body**"}, "deleteContentRange", "Replaced"),
            (
                {"text": "More", "end_of_segment": True},
                "insertText",
                "Appended to",
            ),
            ({"bold": True}, "updateTextStyle", "Applied formatting to"),
        ],
    )
    async def test_suggestion_mode_preserves_heading_operation_and_revision_guard(
        self, revision, kwargs, request_type, operation
    ):
        service = _service(_doc(revision=revision))

        result = await _call(service, heading="Status", write_mode="SUGGEST", **kwargs)

        body = _batch_body(service)
        expected_control = {"writeMode": "SUGGEST"}
        if revision:
            expected_control["requiredRevisionId"] = revision
        assert body["writeControl"] == expected_control
        assert request_type in body["requests"][0]
        assert result.startswith(f"Suggested: {operation}")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kwargs",
        [{"text": "New"}, {"text": "More", "end_of_segment": True}, {"bold": True}],
    )
    async def test_failed_heading_suggestion_save_reports_error(self, kwargs):
        service = _service(_doc())
        service.documents.return_value.batchUpdate.return_value.execute.return_value = {
            "commentUpdateState": "ALL_FAILED_UNKNOWN_REASON"
        }

        result = await _call(service, heading="Status", write_mode="SUGGEST", **kwargs)

        assert result.startswith("Error:")
        assert "did not save these changes as suggestions" in result
        assert "version history" in result

    @pytest.mark.asyncio
    async def test_replace_section_deletes_body_then_inserts_markdown(self):
        service = _service(_doc())
        result = await _call(service, heading="Status", text="New **body**")

        body = _batch_body(service)
        assert body["writeControl"] == {"requiredRevisionId": "rev-1"}
        requests = body["requests"]
        assert requests[0] == {
            "deleteContentRange": {"range": {"startIndex": 14, "endIndex": 23}}
        }
        assert requests[1]["insertText"]["location"]["index"] == 14
        assert requests[1]["insertText"]["text"] == "New body\n"
        styles = [
            r["updateParagraphStyle"]["paragraphStyle"]
            for r in requests
            if "updateParagraphStyle" in r
        ]
        assert styles == [
            {"namedStyleType": "NORMAL_TEXT"},
            {"spaceBelow": {"magnitude": 12, "unit": "PT"}},
        ]
        service.documents.return_value.get.assert_called_once_with(
            documentId="d" * 25, includeTabsContent=True
        )
        assert "Replaced section under heading 'Status'" in result

    @pytest.mark.asyncio
    async def test_replace_last_section_keeps_final_newline_and_drops_bullet(self):
        doc = _doc()
        doc["body"]["content"][5] = _para(31, "Tail\n", bullet=True)
        service = _service(doc)
        await _call(service, heading="Details", text="Fresh")

        requests = _batch_body(service)["requests"]
        assert requests[0] == {
            "deleteContentRange": {"range": {"startIndex": 31, "endIndex": 35}}
        }
        assert requests[1] == {
            "deleteParagraphBullets": {"range": {"startIndex": 31, "endIndex": 32}}
        }
        assert requests[2]["insertText"]["location"]["index"] == 31

    @pytest.mark.asyncio
    async def test_append_to_section_inserts_at_section_end(self):
        service = _service(_doc())
        result = await _call(
            service, heading="Status", text="More", end_of_segment=True
        )

        requests = _batch_body(service)["requests"]
        assert not any("deleteContentRange" in r for r in requests)
        assert requests[0]["insertText"] == {
            "text": "More\n",
            "location": {"index": 23},
        }
        assert "Appended to section under heading 'Status'" in result

    @pytest.mark.asyncio
    async def test_append_to_last_section_splits_final_paragraph(self):
        service = _service(_doc())
        await _call(service, heading="Details", text="More", end_of_segment=True)

        requests = _batch_body(service)["requests"]
        assert requests[0] == {"insertText": {"text": "\n", "location": {"index": 35}}}
        assert requests[1]["insertText"]["location"]["index"] == 36

    @pytest.mark.asyncio
    async def test_append_into_empty_trailing_bullet_drops_bullet(self):
        doc = _doc()
        doc["body"]["content"][5] = _para(31, "Tail\n", bullet=True)
        doc["body"]["content"].append(_para(36, "\n", bullet=True))
        service = _service(doc)
        await _call(service, heading="Details", text="More", end_of_segment=True)

        requests = _batch_body(service)["requests"]
        assert requests[0] == {
            "deleteParagraphBullets": {"range": {"startIndex": 36, "endIndex": 37}}
        }
        assert requests[1]["insertText"] == {
            "text": "More\n",
            "location": {"index": 36},
        }

    @pytest.mark.asyncio
    async def test_replace_under_trailing_heading_with_empty_body_appends(self):
        doc = _doc()
        doc["body"]["content"] = doc["body"]["content"][:5]
        service = _service(doc)
        await _call(service, heading="Details", text="Body")

        requests = _batch_body(service)["requests"]
        assert not any("deleteContentRange" in r for r in requests)
        assert requests[0] == {"insertText": {"text": "\n", "location": {"index": 30}}}
        assert requests[1]["insertText"]["location"]["index"] == 31

    @pytest.mark.asyncio
    async def test_formatting_only_targets_section_body(self):
        service = _service(_doc())
        result = await _call(service, heading="Status", bold=True)

        requests = _batch_body(service)["requests"]
        assert len(requests) == 1
        assert requests[0]["updateTextStyle"]["range"] == {
            "startIndex": 14,
            "endIndex": 23,
        }
        assert requests[0]["updateTextStyle"]["textStyle"]["bold"] is True
        assert "Applied formatting to section 14-23" in result

    @pytest.mark.asyncio
    async def test_tab_id_is_threaded_through(self):
        doc = {
            "revisionId": "r",
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.a"},
                    "documentTab": {"body": {"content": []}},
                },
                {
                    "tabProperties": {"tabId": "t.b"},
                    "documentTab": {"body": _doc()["body"]},
                },
            ],
        }
        service = _service(doc)
        await _call(service, heading="Status", text="x", tab_id="t.b")

        requests = _batch_body(service)["requests"]
        assert requests[0]["deleteContentRange"]["range"]["tabId"] == "t.b"
        assert requests[1]["insertText"]["location"]["tabId"] == "t.b"

    @pytest.mark.asyncio
    async def test_no_write_control_when_revision_missing(self):
        service = _service(_doc(revision=None))
        await _call(service, heading="Status", text="x")
        assert "writeControl" not in _batch_body(service)

    @pytest.mark.asyncio
    async def test_replace_refuses_section_with_footnotes(self):
        doc = _doc()
        doc["body"]["content"][3] = _para(14, "Old body\n", footnote=True)
        service = _service(doc)
        with pytest.raises(UserInputError, match="footnote"):
            await _call(service, heading="Status", text="x")
        service.documents.return_value.batchUpdate.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kwargs, fragment",
        [
            ({"start_index": 1, "text": "x"}, "cannot be combined with 'heading'"),
            ({"end_index": 5, "text": "x"}, "cannot be combined with 'heading'"),
            ({"segment_id": "kix.h", "text": "x"}, "body only"),
            ({"heading_level": 9, "text": "x"}, "between 1 and 6"),
            ({"text": "x", "bold": True}, "rendered as Markdown"),
            ({"bold": True, "end_of_segment": True}, "end_of_segment"),
            ({"text": "   "}, "rendered to no content"),
        ],
    )
    async def test_argument_errors_do_not_write(self, kwargs, fragment):
        service = _service(_doc())
        result = await _call(service, heading="Status", **kwargs)
        assert result.startswith("Error:")
        assert fragment in result
        service.documents.return_value.batchUpdate.assert_not_called()

    @pytest.mark.asyncio
    async def test_blank_heading_does_not_write(self):
        service = _service(_doc())
        result = await _call(service, heading=" ", text="x")
        assert result == "Error: 'heading' must not be empty."
        service.documents.return_value.batchUpdate.assert_not_called()

    @pytest.mark.asyncio
    async def test_duplicate_heading_does_not_write(self):
        doc = _doc()
        doc["body"]["content"][4] = _para(23, "Status\n", "HEADING_2")
        service = _service(doc)
        with pytest.raises(UserInputError, match="matches 2 headings"):
            await _call(service, heading="Status", text="x")
        service.documents.return_value.batchUpdate.assert_not_called()

    @pytest.mark.asyncio
    async def test_child_tab_writes_use_child_tab_indices(self):
        child_body = {
            "content": [
                {"endIndex": 1, "sectionBreak": {}},
                _para(1, "Status\n", "HEADING_2"),
                _para(8, "child\n"),
            ]
        }
        doc = {
            "revisionId": "r",
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.parent"},
                    "documentTab": {"body": _doc()["body"]},
                    "childTabs": [
                        {
                            "tabProperties": {"tabId": "t.child"},
                            "documentTab": {"body": child_body},
                        }
                    ],
                }
            ],
        }
        service = _service(doc)
        await _call(service, heading="Status", text="x", tab_id="t.child")

        requests = _batch_body(service)["requests"]
        assert requests[0] == {
            "deleteContentRange": {
                "range": {"startIndex": 8, "endIndex": 13, "tabId": "t.child"}
            }
        }

    @pytest.mark.asyncio
    async def test_formatting_empty_section_errors(self):
        doc = _doc()
        doc["body"]["content"] = doc["body"]["content"][:5]
        service = _service(doc)
        result = await _call(service, heading="Details", italic=True)
        assert result.startswith("Error:") and "no content to format" in result

    @pytest.mark.asyncio
    async def test_index_mode_still_requires_start_index(self):
        service = _service(_doc())
        result = await _call(service, text="x")
        assert (
            result == "Error: 'start_index' is required unless 'heading' is provided."
        )
