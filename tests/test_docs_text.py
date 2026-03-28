"""Unit tests for gdocs/docs_text.py"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gdocs.docs_text import render_elements, extract_sections


# ---------------------------------------------------------------------------
# Helpers to build minimal Docs API structures
# ---------------------------------------------------------------------------

def make_text_run(content, inserted=False, deleted=False):
    tr = {"content": content}
    if inserted:
        tr["suggestedInsertionIds"] = ["s1"]
    if deleted:
        tr["suggestedDeletionIds"] = ["s2"]
    return {"textRun": tr}


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
# render_elements
# ---------------------------------------------------------------------------

class TestRenderElements:
    def test_plain_text_always_included(self):
        elements = [make_paragraph([make_text_run("Hello\n")])]
        assert render_elements(elements, "original") == "Hello\n"
        assert render_elements(elements, "accepted") == "Hello\n"

    def test_original_excludes_suggested_insertion(self):
        elements = [make_paragraph([
            make_text_run("keep "),
            make_text_run("inserted ", inserted=True),
            make_text_run("this\n"),
        ])]
        result = render_elements(elements, "original")
        assert "inserted" not in result
        assert "keep " in result
        assert "this\n" in result

    def test_original_includes_suggested_deletion(self):
        elements = [make_paragraph([
            make_text_run("keep "),
            make_text_run("deleted ", deleted=True),
            make_text_run("this\n"),
        ])]
        result = render_elements(elements, "original")
        assert "deleted " in result

    def test_accepted_excludes_suggested_deletion(self):
        elements = [make_paragraph([
            make_text_run("keep "),
            make_text_run("deleted ", deleted=True),
            make_text_run("this\n"),
        ])]
        result = render_elements(elements, "accepted")
        assert "deleted" not in result
        assert "keep " in result

    def test_accepted_includes_suggested_insertion(self):
        elements = [make_paragraph([
            make_text_run("keep "),
            make_text_run("inserted ", inserted=True),
            make_text_run("this\n"),
        ])]
        result = render_elements(elements, "accepted")
        assert "inserted " in result

    def test_empty_elements(self):
        assert render_elements([], "original") == ""
        assert render_elements([], "accepted") == ""

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
        result = render_elements([table_element], "original")
        assert "cell1\n" in result
        assert "cell2\n" in result

    def test_depth_guard(self):
        # Should return empty string at depth > 5
        from gdocs.docs_text import render_elements
        assert render_elements([make_paragraph([make_text_run("x\n")])], "original", depth=6) == ""


# ---------------------------------------------------------------------------
# extract_sections
# ---------------------------------------------------------------------------

class TestExtractSections:
    def _body(self, *paragraphs):
        return list(paragraphs)

    def test_no_headings_returns_single_preamble(self):
        body = self._body(
            make_paragraph([make_text_run("intro\n")]),
            make_paragraph([make_text_run("body\n")]),
        )
        sections = extract_sections(body)
        assert len(sections) == 1
        assert sections[0]["title"] == "(preamble)"
        assert sections[0]["level"] == 0
        assert sections[0]["index"] == 0

    def test_heading_starts_new_section(self):
        body = self._body(
            make_paragraph([make_text_run("intro\n")]),
            make_paragraph([make_text_run("Introduction\n")], heading_level=1),
            make_paragraph([make_text_run("body text\n")]),
        )
        sections = extract_sections(body)
        assert len(sections) == 2
        assert sections[0]["title"] == "(preamble)"
        assert sections[1]["title"] == "Introduction"
        assert sections[1]["level"] == 1

    def test_multiple_headings(self):
        body = self._body(
            make_paragraph([make_text_run("Methods\n")], heading_level=1),
            make_paragraph([make_text_run("method text\n")]),
            make_paragraph([make_text_run("Results\n")], heading_level=1),
            make_paragraph([make_text_run("result text\n")]),
        )
        sections = extract_sections(body)
        assert len(sections) == 2
        assert sections[0]["title"] == "Methods"
        assert sections[1]["title"] == "Results"

    def test_section_indices_are_sequential(self):
        body = self._body(
            make_paragraph([make_text_run("A\n")], heading_level=1),
            make_paragraph([make_text_run("B\n")], heading_level=1),
            make_paragraph([make_text_run("C\n")], heading_level=1),
        )
        sections = extract_sections(body)
        assert [s["index"] for s in sections] == [0, 1, 2]

    def test_heading_title_strips_suggested_insertion(self):
        # The heading paragraph has a normal run + an inserted run
        heading = make_paragraph([
            make_text_run("Real Title"),
            make_text_run(" inserted part", inserted=True),
            make_text_run("\n"),
        ], heading_level=2)
        sections = extract_sections([heading])
        assert "inserted part" not in sections[0]["title"]
        assert "Real Title" in sections[0]["title"]

    def test_empty_body(self):
        assert extract_sections([]) == []

    def test_preamble_not_added_if_empty(self):
        # Doc starts directly with a heading — no preamble section
        body = self._body(
            make_paragraph([make_text_run("Title\n")], heading_level=1),
            make_paragraph([make_text_run("text\n")]),
        )
        sections = extract_sections(body)
        assert len(sections) == 1
        assert sections[0]["title"] == "Title"
