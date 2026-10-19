"""Tests for context-preserving Google Docs plain-text rendering."""

import logging

import pytest

from gdocs.docs_links import LinkTarget, resolve_link_target
from gdocs.docs_plain_text import render_doc_to_plain_text


def _paragraph(*elements, positioned_object_ids=None):
    """Build a paragraph with optional positioned-object anchors."""
    paragraph = {"elements": list(elements)}
    if positioned_object_ids is not None:
        paragraph["positionedObjectIds"] = positioned_object_ids
    return {"paragraph": paragraph}


def _text(content, *, link=None):
    """Build a text run with optional link metadata."""
    style = {"link": link} if link is not None else {}
    return {"textRun": {"content": content, "textStyle": style}}


def _tab(title, tab_id, content, **document_tab_fields):
    """Build a tab with body content and optional segment or object metadata."""
    return {
        "tabProperties": {"title": title, "tabId": tab_id},
        "documentTab": {
            "body": {"content": content},
            **document_tab_fields,
        },
    }


class TestCompatibility:
    def test_ordinary_paragraphs_are_byte_for_byte_unchanged(self):
        """Blank lines, spaces, and split runs survive plain-text rendering."""
        doc = {
            "body": {
                "content": [
                    _paragraph(_text("First paragraph\n")),
                    _paragraph(_text("\n")),
                    _paragraph(_text(" \t\n")),
                    _paragraph(_text("Second "), _text("paragraph\n")),
                    _paragraph(_text("\n")),
                ]
            }
        }

        assert render_doc_to_plain_text(doc) == (
            "First paragraph\n\n \t\nSecond paragraph\n\n"
        )

    @pytest.mark.parametrize("content", ["", "\n", " \t\n"])
    def test_empty_and_whitespace_only_contexts_are_preserved(self, content):
        """An otherwise empty document retains its exact whitespace content."""
        doc = {"body": {"content": [_paragraph(_text(content))]}}

        assert render_doc_to_plain_text(doc) == content

    def test_single_and_nested_tabs_keep_existing_marker_format(self):
        """Nested tabs retain their titles, IDs, and indentation in separators."""
        parent = _tab("Main", "tab-1", [_paragraph(_text("Parent\n"))])
        parent["childTabs"] = [_tab("Details", "tab-2", [_paragraph(_text("Child\n"))])]

        assert render_doc_to_plain_text({"tabs": [parent]}) == (
            "\n--- TAB: Main (ID: tab-1) ---\n"
            "Parent\n"
            "\n--- TAB:     Details (ID: tab-2) ---\n"
            "Child\n"
        )


class TestLinkTargets:
    def test_resolver_uses_documented_priority(self):
        """External URLs take precedence when multiple target fields are present."""
        assert resolve_link_target(
            {
                "url": "https://example.com",
                "heading": {"id": "heading-1", "tabId": "tab-1"},
                "bookmark": {"id": "bookmark-1", "tabId": "tab-1"},
                "tabId": "tab-2",
            }
        ) == LinkTarget("url", "https://example.com")

    def test_resolver_supports_new_and_legacy_internal_shapes(self):
        """Nested and legacy target fields normalize consistently, including tabs."""
        assert resolve_link_target(
            {"heading": {"id": "heading-1", "tabId": "tab-2"}}
        ) == LinkTarget("heading", "heading-1", "tab-2")
        assert resolve_link_target(
            {"headingId": "legacy-heading", "tabId": "tab-2"}
        ) == LinkTarget("heading", "legacy-heading", "tab-2")
        assert resolve_link_target(
            {"bookmark": {"id": "bookmark-1", "tabId": "tab-3"}}
        ) == LinkTarget("bookmark", "bookmark-1", "tab-3")
        assert resolve_link_target(
            {"bookmarkId": "legacy-bookmark", "tabId": "tab-3"}
        ) == LinkTarget("bookmark", "legacy-bookmark", "tab-3")
        assert resolve_link_target({"tabId": "tab-4"}) == LinkTarget("tab", "tab-4")
        assert resolve_link_target({"unsupported": "target"}) == LinkTarget("unknown")

    def test_plain_text_renders_external_and_internal_targets(self):
        """Link annotations preserve destinations and resolve local tab context."""
        doc = {
            "tabs": [
                _tab(
                    "Main",
                    "tab-current",
                    [
                        _paragraph(
                            _text(
                                "External",
                                link={"url": "https://example.com"},
                            ),
                            _text(" "),
                            _text(
                                "Heading",
                                link={
                                    "heading": {
                                        "id": "heading-1",
                                        "tabId": "tab-target",
                                    }
                                },
                            ),
                            _text(" "),
                            _text(
                                "Bookmark",
                                link={"bookmarkId": "bookmark-1"},
                            ),
                            _text(" "),
                            _text("Tab\n", link={"tabId": "tab-other"}),
                        )
                    ],
                )
            ]
        }

        assert render_doc_to_plain_text(doc) == (
            "\n--- TAB: Main (ID: tab-current) ---\n"
            "External (https://example.com) "
            "Heading [heading: heading-1, tab: tab-target] "
            "Bookmark [bookmark: bookmark-1, tab: tab-current] "
            "Tab [tab: tab-other]\n"
        )

    def test_link_split_across_style_runs_is_annotated_once(self):
        """A link spanning several styled runs keeps its label contiguous."""
        link = {"url": "https://example.com/ref"}
        doc = {
            "body": {
                "content": [
                    _paragraph(
                        _text("See "),
                        _text("Ref", link=link),
                        _text("erence", link=link),
                        _text(" and "),
                        _text("Other\n", link={"url": "https://example.com/other"}),
                    )
                ]
            }
        }

        assert render_doc_to_plain_text(doc) == (
            "See Reference (https://example.com/ref) and "
            "Other (https://example.com/other)\n"
        )


class TestContextualElements:
    def test_chips_use_readable_values_and_fallbacks(self):
        """Person, rich-link, and date chips retain available values and fallbacks."""
        doc = {
            "body": {
                "content": [
                    _paragraph(
                        {
                            "person": {
                                "personProperties": {
                                    "name": "Ada Lovelace",
                                    "email": "ada@example.com",
                                }
                            }
                        },
                        _text(" | "),
                        {
                            "person": {
                                "personProperties": {"email": "grace@example.com"}
                            }
                        },
                        _text(" | "),
                        {"person": {"personProperties": {}}},
                        _text(" | "),
                        {
                            "richLink": {
                                "richLinkProperties": {
                                    "title": "Project",
                                    "uri": "https://example.com/project",
                                }
                            }
                        },
                        _text(" | "),
                        {"richLink": {"richLinkProperties": {}}},
                        _text(" | "),
                        {
                            "dateElement": {
                                "dateElementProperties": {
                                    "displayText": "July 14, 2026"
                                }
                            }
                        },
                        _text(" | "),
                        {
                            "dateElement": {
                                "dateElementProperties": {
                                    "timestamp": "2026-07-14T00:00:00Z"
                                }
                            }
                        },
                        _text(" | "),
                        {"dateElement": {"dateElementProperties": {}}},
                        _text("\n"),
                    )
                ]
            }
        }

        assert render_doc_to_plain_text(doc) == (
            "Ada Lovelace <ada@example.com> | <grace@example.com> | "
            "[Person chip: details unavailable] | "
            "Project (https://example.com/project) | "
            "[Rich link: details unavailable] | July 14, 2026 | "
            "2026-07-14T00:00:00Z | [Date: details unavailable]\n"
        )

    @pytest.mark.parametrize(
        ("source_properties", "expected_logo"),
        [
            ({}, "[Image: Logo]"),
            ({"sourceUri": ""}, "[Image: Logo]"),
            (
                {"sourceUri": "https://example.com/logo.png"},
                "[Image: Logo; URI: https://example.com/logo.png]",
            ),
        ],
    )
    def test_inline_positioned_and_unresolved_objects(
        self, source_properties, expected_logo
    ):
        """Images retain descriptions and source URIs without exposing content URIs."""
        doc = {
            "inlineObjects": {
                "inline-1": {
                    "inlineObjectProperties": {
                        "embeddedObject": {
                            "title": "Logo",
                            "imageProperties": {
                                "contentUri": "https://example.com/private-logo.png",
                                **source_properties,
                            },
                        }
                    }
                }
            },
            "positionedObjects": {
                "positioned-1": {
                    "positionedObjectProperties": {
                        "embeddedObject": {"description": "Diagram"}
                    }
                },
                "positioned-2": {
                    "positionedObjectProperties": {
                        "embeddedObject": {
                            "imageProperties": {
                                "sourceUri": "https://example.com/chart.png",
                                "contentUri": "https://example.com/private-chart.png",
                            }
                        }
                    }
                },
            },
            "body": {
                "content": [
                    _paragraph(
                        {"inlineObjectElement": {"inlineObjectId": "inline-1"}},
                        _text("\n"),
                    ),
                    _paragraph(
                        _text("See diagram\n"),
                        positioned_object_ids=["positioned-1"],
                    ),
                    _paragraph(
                        {"inlineObjectElement": {"inlineObjectId": "missing-inline"}},
                        _text("\n"),
                    ),
                ]
            },
        }

        assert render_doc_to_plain_text(doc) == (
            f"{expected_logo}\n"
            "See diagram [Image: Diagram]\n"
            "[Inline object missing-inline: details unavailable]\n"
            "\n--- UNRESOLVED POSITIONED OBJECTS ---\n"
            "positioned-2: [Image; URI: https://example.com/chart.png]\n"
        )

    def test_dangling_positioned_object_reference_is_explicit(self):
        """A positioned-object anchor without metadata produces a visible marker."""
        doc = {
            "body": {
                "content": [
                    _paragraph(
                        _text("Diagram\n"), positioned_object_ids=["missing-object"]
                    )
                ]
            }
        }

        assert render_doc_to_plain_text(doc) == (
            "Diagram [Positioned object missing-object: details unavailable]\n"
        )

    def test_headers_footers_and_footnotes_are_labeled(self):
        """Segment labels and referenced footnote content remain discoverable."""
        tab = _tab(
            "Main",
            "tab-1",
            [
                _paragraph(
                    _text("Claim"),
                    {"footnoteReference": {"footnoteId": "fn-1"}},
                    _text(".\n"),
                )
            ],
            headers={"header-1": {"content": [_paragraph(_text("Header\n"))]}},
            footers={"footer-1": {"content": [_paragraph(_text("Footer\n"))]}},
            footnotes={
                "fn-1": {"content": [_paragraph(_text("Supporting detail.\n"))]}
            },
        )

        assert render_doc_to_plain_text({"tabs": [tab]}) == (
            "\n--- TAB: Main (ID: tab-1) ---\n"
            "Claim[Footnote: fn-1].\n"
            "Footnote fn-1: Supporting detail.\n"
            "\n--- HEADER: header-1 ---\n"
            "Header\n"
            "\n--- FOOTER: footer-1 ---\n"
            "Footer\n"
        )

    def test_table_of_contents_and_nested_table_cells_are_recursive(self):
        """Recursive structures preserve text and links with readable cell boundaries."""
        nested_table = {
            "table": {
                "tableRows": [
                    {
                        "tableCells": [
                            {"content": [_paragraph(_text("Nested A\n"))]},
                            {"content": [_paragraph(_text("Nested B\n"))]},
                        ]
                    }
                ]
            }
        }
        doc = {
            "body": {
                "content": [
                    {
                        "tableOfContents": {
                            "content": [_paragraph(_text("Heading 1\n"))]
                        }
                    },
                    {
                        "table": {
                            "tableRows": [
                                {
                                    "tableCells": [
                                        {
                                            "content": [
                                                _paragraph(_text("A1\n")),
                                                _paragraph(_text("A2\n")),
                                            ]
                                        },
                                        {"content": [_paragraph(_text("B\n"))]},
                                    ]
                                },
                                {
                                    "tableCells": [
                                        {"content": [nested_table]},
                                        {"content": [_paragraph(_text("C\n"))]},
                                    ]
                                },
                            ]
                        }
                    },
                ]
            }
        }

        assert render_doc_to_plain_text(doc) == (
            "Heading 1\nA1 A2\tB\nNested A\tNested B\tC\n"
        )

    def test_breaks_auto_text_equations_and_private_use_chip(self):
        """Non-text elements and opaque smart chips produce descriptive placeholders."""
        doc = {
            "body": {
                "content": [
                    {"sectionBreak": {}},
                    _paragraph(
                        _text("Before"),
                        {"pageBreak": {}},
                        {"columnBreak": {}},
                        {"autoText": {"type": "UNKNOWN_TYPE"}},
                        {"equation": {}},
                        _text("\ue907After\n"),
                    ),
                    {"sectionBreak": {}},
                    _paragraph(_text("Next section\n")),
                ]
            }
        }

        assert render_doc_to_plain_text(doc) == (
            "Before[Page Break][Column Break]"
            "[Auto text: UNKNOWN_TYPE][Equation]"
            "[Smart Chip: details unavailable from Docs API]After\n"
            "[Section Break]\n"
            "Next section\n"
        )

    def test_unknown_variants_emit_markers_and_warnings(self, caplog):
        """Unknown structural and inline variants are visible and logged."""
        doc = {
            "body": {
                "content": [
                    _paragraph({"futureInlineElement": {}}),
                    {"futureStructuralElement": {}},
                ]
            }
        }

        with caplog.at_level(logging.WARNING, logger="gdocs.docs_plain_text"):
            rendered = render_doc_to_plain_text(doc)

        assert rendered == (
            "[Unsupported Google Docs element: futureInlineElement]"
            "[Unsupported Google Docs element: futureStructuralElement]"
        )
        assert "futureInlineElement" in caplog.text
        assert "futureStructuralElement" in caplog.text
