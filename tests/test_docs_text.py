"""Unit tests for gdocs/docs_text.py"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gdocs.docs_text import render_elements, extract_sections, SUGGESTIONS_VIEW_MODE


# ---------------------------------------------------------------------------
# Helpers to build minimal Docs API structures
# ---------------------------------------------------------------------------

def make_text_run(content):
    return {"textRun": {"content": content}}


def make_paragraph(text_runs, heading_level=None):
    style = {}
    if heading_level:
        style["namedStyleType"] = f"HEADING_{heading_level}"
    return {
        "paragraph": {
            "paragraphStyle": style,
            "elements": text_runs,
        }
    }


# ---------------------------------------------------------------------------
# SUGGESTIONS_VIEW_MODE mapping
# ---------------------------------------------------------------------------

class TestSuggestionsViewMode:
    def test_original_maps_to_preview_without_suggestions(self):
        assert SUGGESTIONS_VIEW_MODE["original"] == "PREVIEW_WITHOUT_SUGGESTIONS"

    def test_accepted_maps_to_preview_suggestions_accepted(self):
        assert SUGGESTIONS_VIEW_MODE["accepted"] == "PREVIEW_SUGGESTIONS_ACCEPTED"


# ---------------------------------------------------------------------------
# render_elements
# ---------------------------------------------------------------------------

class TestRenderElements:
    def test_plain_text_extracted(self):
        elements = [make_paragraph([make_text_run("Hello\n")])]
        assert render_elements(elements) == "Hello\n"

    def test_multiple_runs_concatenated(self):
        elements = [make_paragraph([make_text_run("foo "), make_text_run("bar\n")])]
        assert render_elements(elements) == "foo bar\n"

    def test_empty_elements(self):
        assert render_elements([]) == ""

    def test_whitespace_only_lines_skipped(self):
        elements = [make_paragraph([make_text_run("   \n")])]
        assert render_elements(elements) == ""

    def test_table_cells_rendered(self):
        table_element = {
            "table": {
                "tableRows": [
                    {"tableCells": [
                        {"content": [make_paragraph([make_text_run("cell1\n")])]},
                        {"content": [make_paragraph([make_text_run("cell2\n")])]},
                    ]}
                ]
            }
        }
        result = render_elements([table_element])
        assert "cell1\n" in result
        assert "cell2\n" in result

    def test_depth_guard(self):
        assert render_elements([make_paragraph([make_text_run("x\n")])], depth=6) == ""


# ---------------------------------------------------------------------------
# extract_sections
# ---------------------------------------------------------------------------

class TestExtractSections:
    def test_no_headings_returns_single_preamble(self):
        body = [
            make_paragraph([make_text_run("intro\n")]),
            make_paragraph([make_text_run("body\n")]),
        ]
        sections = extract_sections(body)
        assert len(sections) == 1
        assert sections[0]["title"] == "(preamble)"
        assert sections[0]["level"] == 0
        assert sections[0]["index"] == 0

    def test_heading_starts_new_section(self):
        body = [
            make_paragraph([make_text_run("intro\n")]),
            make_paragraph([make_text_run("Introduction\n")], heading_level=1),
            make_paragraph([make_text_run("body text\n")]),
        ]
        sections = extract_sections(body)
        assert len(sections) == 2
        assert sections[0]["title"] == "(preamble)"
        assert sections[1]["title"] == "Introduction"
        assert sections[1]["level"] == 1

    def test_multiple_headings(self):
        body = [
            make_paragraph([make_text_run("Methods\n")], heading_level=1),
            make_paragraph([make_text_run("method text\n")]),
            make_paragraph([make_text_run("Results\n")], heading_level=1),
            make_paragraph([make_text_run("result text\n")]),
        ]
        sections = extract_sections(body)
        assert len(sections) == 2
        assert sections[0]["title"] == "Methods"
        assert sections[1]["title"] == "Results"

    def test_section_indices_are_sequential(self):
        body = [
            make_paragraph([make_text_run("A\n")], heading_level=1),
            make_paragraph([make_text_run("B\n")], heading_level=1),
            make_paragraph([make_text_run("C\n")], heading_level=1),
        ]
        sections = extract_sections(body)
        assert [s["index"] for s in sections] == [0, 1, 2]

    def test_empty_body(self):
        assert extract_sections([]) == []

    def test_preamble_not_added_if_empty(self):
        body = [
            make_paragraph([make_text_run("Title\n")], heading_level=1),
            make_paragraph([make_text_run("text\n")]),
        ]
        sections = extract_sections(body)
        assert len(sections) == 1
        assert sections[0]["title"] == "Title"
