"""Unit tests for index-aligned text extraction.

An empty paragraph occupies an index in the document. Dropping it from the
extracted text shifts every subsequent offset, so any index computed by
searching that text lands in the wrong place. The same holds for paragraph
elements that are not textRun.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gdocs.docs_helpers import (
    OBJECT_PLACEHOLDER,
    extract_text_from_elements,
    process_tab_hierarchy,
)


def _para(*elements):
    return {"paragraph": {"elements": list(elements)}}


def _run(content):
    return {"textRun": {"content": content}}


def _spanned(key, start, end):
    """A non-text paragraph element carrying its own index span."""
    return {key: {}, "startIndex": start, "endIndex": end}


# "Alpha", empty paragraph, "Bravo", empty paragraph - body spans indices 1-15.
ALPHA_BRAVO_BODY = [
    _para(_run("Alpha\n")),
    _para(_run("\n")),
    _para(_run("Bravo\n")),
    _para(_run("\n")),
]


def test_empty_paragraphs_are_preserved():
    assert extract_text_from_elements(ALPHA_BRAVO_BODY) == "Alpha\n\nBravo\n\n"


def test_extracted_length_matches_document_span():
    """Body spans indices 1-15, i.e. 14 characters."""
    assert len(extract_text_from_elements(ALPHA_BRAVO_BODY)) == 15 - 1


def test_offset_of_later_text_matches_document_index():
    """'Bravo' starts at index 8; searching the extracted text must agree.

    The extracted text is 0-based while the body starts at index 1, so the
    document index is the offset plus one.
    """
    text = extract_text_from_elements(ALPHA_BRAVO_BODY)
    assert text.index("Bravo") + 1 == 8


def test_paragraph_with_no_elements_is_not_dropped():
    body = [_para(_run("A\n")), {"paragraph": {}}, _para(_run("B\n"))]
    assert extract_text_from_elements(body) == "A\nB\n"


def test_page_break_occupies_its_index_span():
    body = [
        _para(_run("Alpha\n")),
        _para(_spanned("pageBreak", 7, 8), _run("\n")),
    ]
    text = extract_text_from_elements(body)
    assert text == "Alpha\n" + OBJECT_PLACEHOLDER + "\n"
    assert len(text) == 8


def test_inline_object_occupies_one_index():
    body = [_para(_run("a"), _spanned("inlineObjectElement", 2, 3), _run("b\n"))]
    assert extract_text_from_elements(body) == "a" + OBJECT_PLACEHOLDER + "b\n"


def test_multi_index_element_gets_one_placeholder_per_index():
    body = [_para(_spanned("richLink", 1, 4), _run("\n"))]
    assert extract_text_from_elements(body) == OBJECT_PLACEHOLDER * 3 + "\n"


def test_unrecognised_element_type_still_holds_its_indices():
    """Deriving width from the span, not a name list, survives new API types."""
    body = [_para(_run("a"), _spanned("someFutureElement", 2, 3), _run("b\n"))]
    assert extract_text_from_elements(body) == "a" + OBJECT_PLACEHOLDER + "b\n"


def test_element_without_a_span_contributes_nothing():
    body = [_para({"footnoteReference": {"footnoteId": "kix.f1"}}, _run("\n"))]
    assert extract_text_from_elements(body) == "\n"


def test_tab_header_is_emitted_when_named():
    text = extract_text_from_elements(ALPHA_BRAVO_BODY, "My Tab", "t.abc")
    assert text.startswith("\n--- TAB: My Tab (ID: t.abc) ---\n")
    assert text.endswith("Alpha\n\nBravo\n\n")


def test_table_cell_text_is_included():
    body = [
        {
            "table": {
                "tableRows": [
                    {"tableCells": [{"content": [_para(_run("cell\n"))]}]},
                ]
            }
        }
    ]
    assert extract_text_from_elements(body) == "cell\n"


def test_recursion_is_bounded():
    assert extract_text_from_elements([_para(_run("x\n"))], depth=6) == ""


def test_tab_hierarchy_descends_into_child_tabs():
    tab = {
        "tabProperties": {"tabId": "t.parent", "title": "Parent"},
        "documentTab": {"body": {"content": [_para(_run("Parent text\n"))]}},
        "childTabs": [
            {
                "tabProperties": {"tabId": "t.child", "title": "Child"},
                "documentTab": {"body": {"content": [_para(_run("Child text\n"))]}},
            }
        ],
    }

    text = process_tab_hierarchy(tab)
    assert "Parent text\n" in text
    assert "Child text\n" in text
    # Nested tabs are indented in their header to show the hierarchy.
    assert "--- TAB:     Child (ID: t.child) ---" in text
