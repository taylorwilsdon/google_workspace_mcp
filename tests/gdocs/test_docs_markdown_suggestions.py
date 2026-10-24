"""Tests for rendering pending suggestions in the Markdown converter."""

import os
import re
import sys
from copy import deepcopy

import pytest
from markdown_it import MarkdownIt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gdocs.docs_markdown import SUGGESTION_LEGEND, convert_doc_to_markdown


def _run(text, style=None, **extra):
    run = {"content": text, "textStyle": style or {}}
    run.update(extra)
    return {"textRun": run}


def _doc(*paragraphs):
    return {
        "title": "Suggestions",
        "body": {
            "content": [
                {
                    "paragraph": {
                        "elements": list(elements),
                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    }
                }
                for elements in paragraphs
            ]
        },
    }


SUGGESTED_DOC = _doc(
    [
        _run("The fox jumps over the "),
        _run("sleeping dog", suggestedInsertionIds=["suggest.1"]),
        _run("lazy dog", suggestedDeletionIds=["suggest.1"]),
        _run(".\n"),
    ]
)


class TestDatePropertySuggestions:
    @pytest.mark.parametrize(
        "proposed,state,expected",
        [
            (
                {"timestamp": "2026-10-21T00:30:00Z"},
                {"timestampSuggested": True},
                "2026-10-21",
            ),
            (
                {
                    "timestamp": "2026-10-21T00:30:00Z",
                    "displayText": "Oct 21, 2026",
                },
                {"timestampSuggested": True},
                "Oct 21, 2026",
            ),
            (
                {"timeZoneId": "America/Bogota"},
                {"timeZoneIdSuggested": True},
                "2026-10-19",
            ),
            (
                {"timeFormat": "TIME_FORMAT_HOUR_MINUTE"},
                {"timeFormatSuggested": True},
                "2026-10-20 00:30",
            ),
            (
                {"timeFormat": "TIME_FORMAT_HOUR_MINUTE_TIMEZONE"},
                {"timeFormatSuggested": True},
                "2026-10-20 00:30 UTC",
            ),
            (
                {"timestamp": "2099-01-01T00:00:00Z"},
                {"timestampSuggested": False},
                "Oct 20, 2026",
            ),
            (
                {"timestamp": "2099-01-01T00:00:00Z", "displayText": "Wrong date"},
                {},
                "Oct 20, 2026",
            ),
            (
                {"locale": "es", "timestamp": "2099-01-01T00:00:00Z"},
                {"localeSuggested": True, "timestampSuggested": False},
                "2026-10-20",
            ),
        ],
    )
    def test_date_property_suggestions_use_flagged_values(
        self, proposed, state, expected
    ):
        doc = _doc(
            [
                {
                    "dateElement": {
                        "dateElementProperties": {
                            "displayText": "Oct 20, 2026",
                            "timestamp": "2026-10-20T00:30:00Z",
                        },
                        "suggestedDateElementPropertiesChanges": {
                            "s": {
                                "dateElementProperties": proposed,
                                "dateElementPropertiesSuggestionState": state,
                            }
                        },
                    }
                }
            ]
        )
        original = deepcopy(doc)

        assert convert_doc_to_markdown(doc) == SUGGESTION_LEGEND + expected + "\n"
        assert doc == original

    def test_later_suggestion_does_not_reuse_earlier_display_text(self):
        doc = _doc(
            [
                {
                    "dateElement": {
                        "dateElementProperties": {
                            "timestamp": "2026-10-20T00:00:00Z",
                            "displayText": "Oct 20, 2026",
                        },
                        "suggestedDateElementPropertiesChanges": {
                            "first": {
                                "dateElementProperties": {
                                    "timestamp": "2026-10-21T00:00:00Z",
                                    "displayText": "Oct 21, 2026",
                                },
                                "dateElementPropertiesSuggestionState": {
                                    "timestampSuggested": True
                                },
                            },
                            "second": {
                                "dateElementProperties": {
                                    "timestamp": "2026-10-22T00:00:00Z"
                                },
                                "dateElementPropertiesSuggestionState": {
                                    "timestampSuggested": True
                                },
                            },
                        },
                    }
                }
            ]
        )

        assert convert_doc_to_markdown(doc) == SUGGESTION_LEGEND + "2026-10-22\n"


class TestLinkedSuggestionBoundaries:
    @pytest.mark.parametrize(
        "ids,opening,closing",
        [
            ({"suggestedInsertionIds": ["s"]}, "{++", "++}"),
            ({"suggestedDeletionIds": ["s"]}, "{--", "--}"),
            (
                {"suggestedInsertionIds": ["s"], "suggestedDeletionIds": ["t"]},
                "{++{--",
                "--}++}",
            ),
        ],
    )
    @pytest.mark.parametrize("include_colors", [False, True])
    def test_markers_enclose_the_complete_styled_link(
        self, ids, opening, closing, include_colors
    ):
        style = {"link": {"url": "https://example.com"}}
        if include_colors:
            style["foregroundColor"] = {"color": {"rgbColor": {"red": 1.0}}}
        doc = _doc(
            [
                _run("the ", style, **ids),
                _run("docs", {**style, "bold": True}, **ids),
            ]
        )

        markdown = convert_doc_to_markdown(doc, include_colors=include_colors)

        color = " [color: #ff0000]" if include_colors else ""
        assert markdown == (
            SUGGESTION_LEGEND
            + f"{opening}[the **docs**](https://example.com){closing}{color}\n"
        )
        if not include_colors and len(ids) == 1:
            # Removing the marked insertion/deletion must remove its URL too.
            body = markdown.removeprefix(SUGGESTION_LEGEND)
            removed = re.sub(re.escape(opening) + r".*?" + re.escape(closing), "", body)
            assert removed.strip() == ""
            assert "[](https://example.com)" not in removed

    def test_link_is_split_at_distinct_suggestion_boundaries(self):
        style = {"link": {"url": "https://example.com"}}
        doc = _doc(
            [
                _run("Plain", style),
                _run("First", style, suggestedInsertionIds=["first"]),
                _run("Second", style, suggestedInsertionIds=["second"]),
                _run("Deleted", style, suggestedDeletionIds=["deleted"]),
                _run("End", style),
            ]
        )

        markdown = convert_doc_to_markdown(doc)

        assert markdown == SUGGESTION_LEGEND + (
            "[Plain](https://example.com)"
            "{++[First](https://example.com)++}"
            "{++[Second](https://example.com)++}"
            "{--[Deleted](https://example.com)--}"
            "[End](https://example.com)\n"
        )
        body = markdown.removeprefix(SUGGESTION_LEGEND)
        accepted = re.sub(r"\{--.*?--\}", "", body)
        accepted = re.sub(r"\{\+\+(.*?)\+\+\}", r"\1", accepted)
        tokens = MarkdownIt().parseInline(accepted.strip())[0].children
        assert sum(token.type == "link_open" for token in tokens) == 4
        assert "".join(token.content for token in tokens) == "PlainFirstSecondEnd"

    def test_internal_link_target_is_inside_the_marker(self):
        doc = _doc(
            [
                _run(
                    "Heading",
                    {"link": {"heading": {"id": "h.1", "tabId": "t.1"}}},
                    suggestedDeletionIds=["s"],
                )
            ]
        )

        assert convert_doc_to_markdown(doc) == (
            SUGGESTION_LEGEND + "{--Heading [heading: h.1, tab: t.1]--}\n"
        )


class TestSuggestionMarkers:
    @pytest.mark.parametrize(
        "kind,payload,rendered",
        [
            (
                "person",
                {"personProperties": {"name": "Ana", "email": "ana@example.com"}},
                "[Ana](mailto:ana@example.com)",
            ),
            (
                "richLink",
                {"richLinkProperties": {"title": "Docs", "uri": "https://example.com"}},
                "[Docs](https://example.com)",
            ),
            (
                "dateElement",
                {"dateElementProperties": {"displayText": "Oct 20"}},
                "Oct 20",
            ),
            ("inlineObjectElement", {"inlineObjectId": "img-1"}, "[Image: Picture]"),
            ("footnoteReference", {"footnoteId": "fn-1"}, "[^fn-1]"),
            ("horizontalRule", {}, "\n---\n"),
            ("autoText", {"type": "PAGE_NUMBER"}, "[Page #]"),
            ("pageBreak", {}, "[Page Break]"),
            ("columnBreak", {}, "[Column Break]"),
            ("equation", {}, "[Equation]"),
        ],
    )
    @pytest.mark.parametrize(
        "suggestion_ids,opening,closing",
        [
            ({"suggestedInsertionIds": ["s"]}, "{++", "++}"),
            ({"suggestedDeletionIds": ["s"]}, "{--", "--}"),
        ],
    )
    def test_non_text_suggestions_use_ids_from_the_nested_payload(
        self, kind, payload, rendered, suggestion_ids, opening, closing
    ):
        doc = _doc([{kind: {**payload, **suggestion_ids}}])
        doc["inlineObjects"] = {
            "img-1": {
                "inlineObjectProperties": {"embeddedObject": {"title": "Picture"}}
            }
        }

        markdown = convert_doc_to_markdown(doc)

        assert markdown.startswith(SUGGESTION_LEGEND)
        assert opening + rendered + closing in markdown

    def test_insertion_and_deletion_are_marked(self):
        markdown = convert_doc_to_markdown(SUGGESTED_DOC)

        assert "The fox jumps over the {++sleeping dog++}{--lazy dog--}." in markdown

    def test_legend_precedes_a_document_with_suggestions(self):
        markdown = convert_doc_to_markdown(SUGGESTED_DOC)

        assert markdown.startswith(SUGGESTION_LEGEND)

    def test_no_legend_or_markers_without_suggestions(self):
        markdown = convert_doc_to_markdown(_doc([_run("Plain text.\n")]))

        assert markdown.strip() == "Plain text."

    def test_newline_only_suggested_run_renders_nothing(self):
        markdown = convert_doc_to_markdown(
            _doc([_run("End of line"), _run("\n", suggestedInsertionIds=["s"])])
        )

        body = markdown.removeprefix(SUGGESTION_LEGEND)
        assert "{++" not in body
        assert body.strip() == "End of line"

    def test_legend_is_found_inside_tabs(self):
        doc = {
            "title": "Tabbed",
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.0", "title": "Tab 1"},
                    "documentTab": SUGGESTED_DOC,
                }
            ],
        }

        markdown = convert_doc_to_markdown(doc)

        assert markdown.startswith(SUGGESTION_LEGEND)
        assert "{--lazy dog--}" in markdown


class TestFootnoteSuggestions:
    @pytest.mark.parametrize(
        "suggestion_ids,opening,closing",
        [
            ({}, "", ""),
            ({"suggestedInsertionIds": ["s"]}, "{++", "++}"),
            ({"suggestedDeletionIds": ["s"]}, "{--", "--}"),
            (
                {"suggestedInsertionIds": ["a"], "suggestedDeletionIds": ["b"]},
                "{++{--",
                "--}++}",
            ),
        ],
    )
    def test_populated_definition_inherits_reference_suggestion(
        self, suggestion_ids, opening, closing
    ):
        """References and definitions share their pending suggestion markers."""
        doc = _doc(
            [
                _run("Text"),
                {"footnoteReference": {"footnoteId": "fn-1", **suggestion_ids}},
            ]
        )
        doc["footnotes"] = {
            "fn-1": _doc([_run("Footnote detail\n", {"bold": True})])["body"]
        }

        markdown = convert_doc_to_markdown(doc)

        assert f"Text{opening}[^fn-1]{closing}" in markdown
        assert f"[^fn-1]: {opening}**Footnote detail**{closing}" in markdown
        assert markdown.startswith(SUGGESTION_LEGEND) == bool(suggestion_ids)


class TestSuggestedTextStyle:
    def test_suggested_bold_is_rendered(self):
        doc = _doc(
            [
                _run(
                    "sleeping dog",
                    suggestedTextStyleChanges={
                        "suggest.2": {
                            "textStyle": {"bold": True},
                            "textStyleSuggestionState": {"boldSuggested": True},
                        }
                    },
                ),
                _run("\n"),
            ]
        )

        assert "**sleeping dog**" in convert_doc_to_markdown(doc)

    def test_suggested_removal_of_bold_is_rendered(self):
        doc = _doc(
            [
                _run(
                    "quiet dog",
                    {"bold": True},
                    suggestedTextStyleChanges={
                        "suggest.3": {
                            "textStyle": {},
                            "textStyleSuggestionState": {"boldSuggested": True},
                        }
                    },
                ),
                _run("\n"),
            ]
        )

        assert "**" not in convert_doc_to_markdown(doc)

    def test_unflagged_fields_in_a_suggestion_are_ignored(self):
        doc = _doc(
            [
                _run(
                    "dog",
                    suggestedTextStyleChanges={
                        "suggest.4": {
                            "textStyle": {"bold": True, "italic": True},
                            "textStyleSuggestionState": {"italicSuggested": True},
                        }
                    },
                ),
                _run("\n"),
            ]
        )

        markdown = convert_doc_to_markdown(doc)

        assert "*dog*" in markdown
        assert "**dog**" not in markdown

    def test_suggested_style_combines_with_insertion_marker(self):
        doc = _doc(
            [
                _run(
                    "new words",
                    suggestedInsertionIds=["s"],
                    suggestedTextStyleChanges={
                        "s": {
                            "textStyle": {"bold": True},
                            "textStyleSuggestionState": {"boldSuggested": True},
                        }
                    },
                ),
                _run("\n"),
            ]
        )

        assert "{++**new words**++}" in convert_doc_to_markdown(doc)


class TestReviewFollowUps:
    @pytest.mark.parametrize("location", ["body", "tab", "table", "footnote"])
    def test_colored_suggestion_survives_document_containers(self, location):
        doc = _doc(
            [
                _run(
                    "new words",
                    {"foregroundColor": {"color": {"rgbColor": {"red": 1.0}}}},
                    suggestedInsertionIds=["s"],
                )
            ]
        )
        if location == "tab":
            doc = {"tabs": [{"documentTab": doc}]}
        elif location == "table":
            doc = {
                "body": {
                    "content": [
                        {"table": {"tableRows": [{"tableCells": [doc["body"]]}]}}
                    ]
                }
            }
        elif location == "footnote":
            footnote = doc["body"]
            doc = _doc([{"footnoteReference": {"footnoteId": "f1"}}])
            doc["footnotes"] = {"f1": footnote}

        markdown = convert_doc_to_markdown(doc, include_colors=True)

        assert markdown.startswith(SUGGESTION_LEGEND)
        assert "{++new words++} [color: #ff0000]" in markdown

    def test_suggested_link_stays_grouped_with_color_annotation(self):
        color = {"foregroundColor": {"color": {"rgbColor": {"red": 1.0}}}}
        change = {
            "s": {
                "textStyle": {"link": {"url": "https://example.com"}},
                "textStyleSuggestionState": {"linkSuggested": True},
            }
        }
        doc = _doc(
            [
                _run("the ", color, suggestedTextStyleChanges=change),
                _run("docs", {**color, "bold": True}, suggestedTextStyleChanges=change),
            ]
        )

        markdown = convert_doc_to_markdown(doc, include_colors=True)

        assert "[the **docs**](https://example.com) [color: #ff0000]" in markdown

    def test_suggested_foreground_color_is_rendered_as_accepted(self):
        doc = _doc(
            [
                _run(
                    "dog",
                    {"foregroundColor": {"color": {"rgbColor": {"red": 1.0}}}},
                    suggestedTextStyleChanges={
                        "s": {
                            "textStyle": {
                                "foregroundColor": {
                                    "color": {"rgbColor": {"blue": 1.0}}
                                }
                            },
                            "textStyleSuggestionState": {
                                "foregroundColorSuggested": True
                            },
                        }
                    },
                )
            ]
        )

        markdown = convert_doc_to_markdown(doc, include_colors=True)

        assert markdown == SUGGESTION_LEGEND + "dog [color: #0000ff]\n"

    def test_style_only_suggestion_gets_the_legend(self):
        doc = _doc(
            [
                _run(
                    "dog",
                    suggestedTextStyleChanges={
                        "s": {
                            "textStyle": {"bold": True},
                            "textStyleSuggestionState": {"boldSuggested": True},
                        }
                    },
                ),
                _run("\n"),
            ]
        )

        markdown = convert_doc_to_markdown(doc)

        assert markdown.startswith(SUGGESTION_LEGEND)
        assert "**dog**" in markdown

    def test_suggested_link_is_grouped_and_rendered(self):
        change = {
            "s": {
                "textStyle": {"link": {"url": "https://example.com"}},
                "textStyleSuggestionState": {"linkSuggested": True},
            }
        }
        doc = _doc(
            [
                _run("Read "),
                _run("the ", suggestedTextStyleChanges=change),
                _run("docs", {"bold": True}, suggestedTextStyleChanges=change),
                _run(" now.\n"),
            ]
        )

        markdown = convert_doc_to_markdown(doc)

        assert "[the **docs**](https://example.com)" in markdown

    def test_suggested_link_removal_drops_the_link(self):
        doc = _doc(
            [
                _run(
                    "old link",
                    {"link": {"url": "https://old.example"}},
                    suggestedTextStyleChanges={
                        "s": {
                            "textStyle": {},
                            "textStyleSuggestionState": {"linkSuggested": True},
                        }
                    },
                ),
                _run("\n"),
            ]
        )

        markdown = convert_doc_to_markdown(doc)

        assert "old.example" not in markdown
        assert "old link" in markdown

    def test_deleted_person_chip_is_marked(self):
        doc = _doc(
            [
                _run("Ask "),
                {
                    "person": {
                        "personProperties": {"name": "Ana", "email": "ana@example.com"},
                        "suggestedDeletionIds": ["s"],
                    },
                },
                _run(".\n"),
            ]
        )

        markdown = convert_doc_to_markdown(doc)

        assert "Ask {--[Ana](mailto:ana@example.com)--}." in markdown

    def test_inserted_date_chip_is_marked(self):
        doc = _doc(
            [
                _run("Due "),
                {
                    "dateElement": {
                        "dateElementProperties": {"displayText": "Oct 20"},
                        "suggestedInsertionIds": ["s"],
                    },
                },
                _run("\n"),
            ]
        )

        markdown = convert_doc_to_markdown(doc)

        assert "Due {++" in markdown and "Oct 20" in markdown and "++}" in markdown
