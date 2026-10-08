"""Tests for the Google Docs to Markdown converter."""

import sys
import os

import pytest
from markdown_it import MarkdownIt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gdocs.docs_markdown import (
    convert_doc_to_markdown,
    format_comments_appendix,
    format_comments_inline,
    parse_drive_comments,
)


# --- Fixtures ---

SIMPLE_DOC = {
    "title": "Simple Test",
    "body": {
        "content": [
            {"sectionBreak": {"sectionStyle": {}}},
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "Hello world\n", "textStyle": {}}}
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                }
            },
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "This is ", "textStyle": {}}},
                        {"textRun": {"content": "bold", "textStyle": {"bold": True}}},
                        {"textRun": {"content": " and ", "textStyle": {}}},
                        {
                            "textRun": {
                                "content": "italic",
                                "textStyle": {"italic": True},
                            }
                        },
                        {"textRun": {"content": " text.\n", "textStyle": {}}},
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                }
            },
        ]
    },
}

HEADINGS_DOC = {
    "title": "Headings",
    "body": {
        "content": [
            {"sectionBreak": {"sectionStyle": {}}},
            {
                "paragraph": {
                    "elements": [{"textRun": {"content": "Title\n", "textStyle": {}}}],
                    "paragraphStyle": {"namedStyleType": "TITLE"},
                }
            },
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "Heading one\n", "textStyle": {}}}
                    ],
                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                }
            },
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "Heading two\n", "textStyle": {}}}
                    ],
                    "paragraphStyle": {"namedStyleType": "HEADING_2"},
                }
            },
        ]
    },
}

TABLE_DOC = {
    "title": "Table Test",
    "body": {
        "content": [
            {"sectionBreak": {"sectionStyle": {}}},
            {
                "table": {
                    "rows": 2,
                    "columns": 2,
                    "tableRows": [
                        {
                            "tableCells": [
                                {
                                    "content": [
                                        {
                                            "paragraph": {
                                                "elements": [
                                                    {
                                                        "textRun": {
                                                            "content": "Name\n",
                                                            "textStyle": {},
                                                        }
                                                    }
                                                ],
                                                "paragraphStyle": {
                                                    "namedStyleType": "NORMAL_TEXT"
                                                },
                                            }
                                        }
                                    ]
                                },
                                {
                                    "content": [
                                        {
                                            "paragraph": {
                                                "elements": [
                                                    {
                                                        "textRun": {
                                                            "content": "Age\n",
                                                            "textStyle": {},
                                                        }
                                                    }
                                                ],
                                                "paragraphStyle": {
                                                    "namedStyleType": "NORMAL_TEXT"
                                                },
                                            }
                                        }
                                    ]
                                },
                            ]
                        },
                        {
                            "tableCells": [
                                {
                                    "content": [
                                        {
                                            "paragraph": {
                                                "elements": [
                                                    {
                                                        "textRun": {
                                                            "content": "Alice\n",
                                                            "textStyle": {},
                                                        }
                                                    }
                                                ],
                                                "paragraphStyle": {
                                                    "namedStyleType": "NORMAL_TEXT"
                                                },
                                            }
                                        }
                                    ]
                                },
                                {
                                    "content": [
                                        {
                                            "paragraph": {
                                                "elements": [
                                                    {
                                                        "textRun": {
                                                            "content": "30\n",
                                                            "textStyle": {},
                                                        }
                                                    }
                                                ],
                                                "paragraphStyle": {
                                                    "namedStyleType": "NORMAL_TEXT"
                                                },
                                            }
                                        }
                                    ]
                                },
                            ]
                        },
                    ],
                }
            },
        ]
    },
}

LIST_DOC = {
    "title": "List Test",
    "lists": {
        "kix.list001": {
            "listProperties": {
                "nestingLevels": [
                    {"glyphType": "GLYPH_TYPE_UNSPECIFIED", "glyphSymbol": "\u2022"},
                ]
            }
        }
    },
    "body": {
        "content": [
            {"sectionBreak": {"sectionStyle": {}}},
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "Item one\n", "textStyle": {}}}
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "bullet": {"listId": "kix.list001", "nestingLevel": 0},
                }
            },
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "Item two\n", "textStyle": {}}}
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "bullet": {"listId": "kix.list001", "nestingLevel": 0},
                }
            },
        ]
    },
}


# --- Converter tests ---


class TestTextFormatting:
    def test_plain_text(self):
        md = convert_doc_to_markdown(SIMPLE_DOC)
        assert "Hello world" in md

    def test_bold(self):
        md = convert_doc_to_markdown(SIMPLE_DOC)
        assert "**bold**" in md

    def test_italic(self):
        md = convert_doc_to_markdown(SIMPLE_DOC)
        assert "*italic*" in md


class TestTextLinks:
    @pytest.mark.parametrize(
        "url, expected_href",
        [
            ("https://example.com/api", "https://example.com/api"),
            ("https://example.com/a)b", "https://example.com/a)b"),
            ("https://example.com/a(b", "https://example.com/a(b"),
            ("https://example.com/a(b)c", "https://example.com/a(b)c"),
            ("https://example.com/a b", "https://example.com/a%20b"),
            ("https://example.com/a\tb", "https://example.com/a%09b"),
            ("https://example.com/a\nb", "https://example.com/a%0Ab"),
            ("https://example.com/<a>)", "https://example.com/%3Ca%3E)"),
            ("https://example.com/a\\)b", "https://example.com/a%5C)b"),
        ],
    )
    @pytest.mark.parametrize("monospace", [False, True])
    def test_url_destinations_round_trip_through_markdown(
        self, url, expected_href, monospace
    ):
        """URL punctuation and whitespace cannot truncate or break rendered links."""
        style = {"link": {"url": url}}
        if monospace:
            style["weightedFontFamily"] = {"fontFamily": "Roboto Mono"}
        doc = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "Reference\n",
                                        "textStyle": style,
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }

        markdown = convert_doc_to_markdown(doc)
        tokens = MarkdownIt().parseInline(markdown.strip())[0].children
        assert [
            token.attrGet("href") for token in tokens if token.type == "link_open"
        ] == [expected_href]
        assert "".join(token.content for token in tokens) == "Reference"
        if url == "https://example.com/api":
            label = "`Reference`" if monospace else "Reference"
            assert markdown == f"[{label}]({url})\n"

    @pytest.mark.parametrize(
        "content",
        [
            "example()",
            "a`b",
            "a``b`c",
            "`start",
            "end`",
            "`both`",
            "```",
            "a```b``c",
            " `padded` ",
            " padded ",
            "   ",
        ],
    )
    @pytest.mark.parametrize("linked", [False, True])
    def test_monospace_content_round_trips_through_markdown(self, content, linked):
        """Backticks and edge spaces survive parsing, including inside styled links."""
        style = {"weightedFontFamily": {"fontFamily": "Roboto Mono"}}
        if linked:
            style.update({"bold": True, "link": {"url": "https://example.com/api"}})
        doc = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {"textRun": {"content": "Before "}},
                                {"textRun": {"content": content, "textStyle": style}},
                                {"textRun": {"content": " after\n"}},
                            ]
                        }
                    }
                ]
            }
        }

        markdown = convert_doc_to_markdown(doc)
        tokens = MarkdownIt().parseInline(markdown.strip())[0].children
        code = [token for token in tokens if token.type == "code_inline"]
        assert len(code) == 1
        assert code[0].content == content
        assert "".join(token.content for token in tokens) == f"Before {content} after"
        if linked:
            assert [
                token.attrGet("href") for token in tokens if token.type == "link_open"
            ] == ["https://example.com/api"]
            assert any(token.type == "strong_open" for token in tokens)

    def test_external_and_internal_link_targets(self):
        """Markdown retains styled URLs and readable heading, bookmark, and tab IDs."""
        doc = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "External",
                                        "textStyle": {
                                            "italic": True,
                                            "link": {"url": "https://example.com"},
                                        },
                                    }
                                },
                                {"textRun": {"content": " ", "textStyle": {}}},
                                {
                                    "textRun": {
                                        "content": "Heading",
                                        "textStyle": {
                                            "link": {
                                                "heading": {
                                                    "id": "heading-1",
                                                    "tabId": "tab-2",
                                                }
                                            }
                                        },
                                    }
                                },
                                {"textRun": {"content": " ", "textStyle": {}}},
                                {
                                    "textRun": {
                                        "content": "Bookmark",
                                        "textStyle": {
                                            "link": {
                                                "bookmarkId": "bookmark-1",
                                                "tabId": "tab-3",
                                            }
                                        },
                                    }
                                },
                                {"textRun": {"content": " ", "textStyle": {}}},
                                {
                                    "textRun": {
                                        "content": "Tab\n",
                                        "textStyle": {"link": {"tabId": "tab-4"}},
                                    }
                                },
                            ]
                        }
                    }
                ]
            }
        }

        assert convert_doc_to_markdown(doc) == (
            "[*External*](https://example.com) "
            "Heading [heading: heading-1, tab: tab-2] "
            "Bookmark [bookmark: bookmark-1, tab: tab-3] "
            "Tab [tab: tab-4]\n"
        )

    def test_monospace_link_keeps_code_span_and_destination(self):
        """A monospace link retains both its code formatting and destination."""
        doc = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "example()\n",
                                        "textStyle": {
                                            "weightedFontFamily": {
                                                "fontFamily": "Roboto Mono"
                                            },
                                            "link": {"url": "https://example.com/api"},
                                        },
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }

        assert convert_doc_to_markdown(doc) == (
            "[`example()`](https://example.com/api)\n"
        )

    @pytest.mark.parametrize(
        ("link", "expected"),
        [
            ({"url": "https://example.com"}, "[**Ref**erence](https://example.com)\n"),
            ({"heading": {"id": "h.1"}}, "**Ref**erence [heading: h.1]\n"),
        ],
    )
    def test_link_split_across_style_runs_is_rendered_once(self, link, expected):
        """Style changes inside a link do not split its label or repeat its target."""
        doc = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "Ref",
                                        "textStyle": {"bold": True, "link": link},
                                    }
                                },
                                {
                                    "textRun": {
                                        "content": "erence\n",
                                        "textStyle": {"link": link},
                                    }
                                },
                            ]
                        }
                    }
                ]
            }
        }

        assert convert_doc_to_markdown(doc) == expected


class TestHeadings:
    def test_title(self):
        md = convert_doc_to_markdown(HEADINGS_DOC)
        assert "# Title" in md

    def test_h1(self):
        md = convert_doc_to_markdown(HEADINGS_DOC)
        assert "# Heading one" in md

    def test_h2(self):
        md = convert_doc_to_markdown(HEADINGS_DOC)
        assert "## Heading two" in md


class TestTables:
    def test_table_header(self):
        md = convert_doc_to_markdown(TABLE_DOC)
        assert "| Name | Age |" in md

    def test_table_separator(self):
        md = convert_doc_to_markdown(TABLE_DOC)
        assert "| --- | --- |" in md

    def test_table_row(self):
        md = convert_doc_to_markdown(TABLE_DOC)
        assert "| Alice | 30 |" in md


class TestLists:
    def test_unordered(self):
        md = convert_doc_to_markdown(LIST_DOC)
        assert "- Item one" in md
        assert "- Item two" in md


CHECKLIST_DOC = {
    "title": "Checklist Test",
    "lists": {
        "kix.checklist001": {
            "listProperties": {
                "nestingLevels": [
                    {"glyphType": "GLYPH_TYPE_UNSPECIFIED"},
                ]
            }
        }
    },
    "body": {
        "content": [
            {"sectionBreak": {"sectionStyle": {}}},
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "Buy groceries\n", "textStyle": {}}}
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "bullet": {"listId": "kix.checklist001", "nestingLevel": 0},
                }
            },
            {
                "paragraph": {
                    "elements": [
                        {
                            "textRun": {
                                "content": "Walk the dog\n",
                                "textStyle": {"strikethrough": True},
                            }
                        }
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "bullet": {"listId": "kix.checklist001", "nestingLevel": 0},
                }
            },
        ]
    },
}


class TestChecklists:
    def test_unchecked(self):
        md = convert_doc_to_markdown(CHECKLIST_DOC)
        assert "- [ ] Buy groceries" in md

    def test_checked(self):
        md = convert_doc_to_markdown(CHECKLIST_DOC)
        assert "- [x] Walk the dog" in md

    def test_checked_no_strikethrough(self):
        """Checked items should not have redundant ~~strikethrough~~ markdown."""
        md = convert_doc_to_markdown(CHECKLIST_DOC)
        assert "~~Walk the dog~~" not in md

    def test_regular_bullet_not_checklist(self):
        """Bullet lists with glyphSymbol should remain as plain bullets."""
        md = convert_doc_to_markdown(LIST_DOC)
        assert "[ ]" not in md
        assert "[x]" not in md


PERSON_CHIP_DOC = {
    "title": "Person Chip Test",
    "body": {
        "content": [
            {"sectionBreak": {"sectionStyle": {}}},
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "Assigned to ", "textStyle": {}}},
                        {
                            "person": {
                                "personProperties": {
                                    "name": "Alice Smith",
                                    "email": "alice@example.com",
                                }
                            }
                        },
                        {"textRun": {"content": " for review.\n", "textStyle": {}}},
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                }
            },
        ]
    },
}

RICH_LINK_CHIP_DOC = {
    "title": "Rich Link Chip Test",
    "body": {
        "content": [
            {"sectionBreak": {"sectionStyle": {}}},
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "See ", "textStyle": {}}},
                        {
                            "richLink": {
                                "richLinkProperties": {
                                    "title": "Project Plan",
                                    "uri": "https://docs.google.com/document/d/abc123",
                                }
                            }
                        },
                        {"textRun": {"content": " for details.\n", "textStyle": {}}},
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                }
            },
        ]
    },
}


class TestSmartChips:
    def test_person_chip(self):
        md = convert_doc_to_markdown(PERSON_CHIP_DOC)
        assert "[Alice Smith](mailto:alice@example.com)" in md

    def test_person_chip_in_context(self):
        md = convert_doc_to_markdown(PERSON_CHIP_DOC)
        assert "Assigned to [Alice Smith](mailto:alice@example.com) for review." in md

    def test_person_chip_name_only(self):
        doc = {
            "title": "Test",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {"person": {"personProperties": {"name": "Bob Jones"}}}
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    }
                ]
            },
        }
        md = convert_doc_to_markdown(doc)
        assert "Bob Jones" in md

    def test_person_chip_email_only(self):
        doc = {
            "title": "Test",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "person": {
                                        "personProperties": {"email": "bob@example.com"}
                                    }
                                }
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    }
                ]
            },
        }
        md = convert_doc_to_markdown(doc)
        assert "[bob@example.com](mailto:bob@example.com)" in md

    def test_rich_link_chip(self):
        md = convert_doc_to_markdown(RICH_LINK_CHIP_DOC)
        assert "[Project Plan](https://docs.google.com/document/d/abc123)" in md

    def test_rich_link_chip_in_context(self):
        md = convert_doc_to_markdown(RICH_LINK_CHIP_DOC)
        assert (
            "See [Project Plan](https://docs.google.com/document/d/abc123) for details."
            in md
        )

    def test_rich_link_uri_only(self):
        doc = {
            "title": "Test",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "richLink": {
                                        "richLinkProperties": {
                                            "uri": "https://example.com"
                                        }
                                    }
                                }
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    }
                ]
            },
        }
        md = convert_doc_to_markdown(doc)
        assert md == "https://example.com\n"

    def test_date_chip_display_text(self):
        doc = {
            "title": "Test",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "Due by ",
                                        "textStyle": {},
                                    }
                                },
                                {
                                    "dateElement": {
                                        "dateElementProperties": {
                                            "displayText": "Mar 31, 2026",
                                            "timestamp": "2026-03-31T00:00:00Z",
                                        }
                                    }
                                },
                                {
                                    "textRun": {
                                        "content": " please.\n",
                                        "textStyle": {},
                                    }
                                },
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    }
                ]
            },
        }
        md = convert_doc_to_markdown(doc)
        assert "Due by Mar 31, 2026 please." in md

    def test_date_chip_timestamp_fallback(self):
        doc = {
            "title": "Test",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "dateElement": {
                                        "dateElementProperties": {
                                            "timestamp": "2026-01-15T00:00:00Z",
                                        }
                                    }
                                }
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    }
                ]
            },
        }
        md = convert_doc_to_markdown(doc)
        assert "2026-01-15" in md

    def test_inline_image(self):
        doc = {
            "title": "Test",
            "inlineObjects": {
                "kix.obj1": {
                    "inlineObjectProperties": {
                        "embeddedObject": {
                            "title": "Logo",
                            "imageProperties": {
                                "contentUri": "https://example.com/logo.png"
                            },
                        }
                    }
                }
            },
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {"inlineObjectElement": {"inlineObjectId": "kix.obj1"}}
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    }
                ]
            },
        }
        md = convert_doc_to_markdown(doc)
        assert "![Logo](https://example.com/logo.png)" in md

    def test_inline_image_no_uri(self):
        doc = {
            "title": "Test",
            "inlineObjects": {
                "kix.obj1": {
                    "inlineObjectProperties": {"embeddedObject": {"title": "Chart"}}
                }
            },
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {"inlineObjectElement": {"inlineObjectId": "kix.obj1"}}
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    }
                ]
            },
        }
        md = convert_doc_to_markdown(doc)
        assert "[Image: Chart]" in md

    def test_footnote_reference(self):
        doc = {
            "title": "Test",
            "footnotes": {
                "kix.fn1": {
                    "content": [
                        {
                            "paragraph": {
                                "elements": [
                                    {
                                        "textRun": {
                                            "content": "See the appendix.\n",
                                            "textStyle": {},
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }
            },
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "Important claim",
                                        "textStyle": {},
                                    }
                                },
                                {"footnoteReference": {"footnoteId": "kix.fn1"}},
                                {
                                    "textRun": {
                                        "content": " and more text.\n",
                                        "textStyle": {},
                                    }
                                },
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    }
                ]
            },
        }
        md = convert_doc_to_markdown(doc)
        assert "Important claim[^kix.fn1] and more text." in md
        assert "[^kix.fn1]: See the appendix." in md

    def test_footnote_reference_preserves_rich_inline_content(self):
        doc = {
            "title": "Test",
            "inlineObjects": {
                "kix.inline1": {
                    "inlineObjectProperties": {
                        "embeddedObject": {
                            "title": "Chart",
                            "imageProperties": {
                                "contentUri": "https://cdn.example.com/chart.png"
                            },
                        }
                    }
                }
            },
            "footnotes": {
                "kix.fn1": {
                    "content": [
                        {
                            "paragraph": {
                                "elements": [
                                    {
                                        "textRun": {
                                            "content": "See ",
                                            "textStyle": {},
                                        }
                                    },
                                    {
                                        "textRun": {
                                            "content": "styled",
                                            "textStyle": {"bold": True},
                                        }
                                    },
                                    {
                                        "textRun": {
                                            "content": " ",
                                            "textStyle": {},
                                        }
                                    },
                                    {
                                        "textRun": {
                                            "content": "link",
                                            "textStyle": {
                                                "link": {
                                                    "url": "https://example.com/link"
                                                }
                                            },
                                        }
                                    },
                                    {
                                        "textRun": {
                                            "content": " ",
                                            "textStyle": {},
                                        }
                                    },
                                    {
                                        "person": {
                                            "personProperties": {
                                                "name": "Ada Lovelace",
                                                "email": "ada@example.com",
                                            }
                                        }
                                    },
                                    {
                                        "textRun": {
                                            "content": " ",
                                            "textStyle": {},
                                        }
                                    },
                                    {
                                        "richLink": {
                                            "richLinkProperties": {
                                                "title": "Project Plan",
                                                "uri": "https://example.com/plan",
                                            }
                                        }
                                    },
                                    {
                                        "textRun": {
                                            "content": " ",
                                            "textStyle": {},
                                        }
                                    },
                                    {
                                        "inlineObjectElement": {
                                            "inlineObjectId": "kix.inline1"
                                        }
                                    },
                                    {
                                        "textRun": {
                                            "content": "\n",
                                            "textStyle": {},
                                        }
                                    },
                                ]
                            }
                        }
                    ]
                }
            },
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "Important claim",
                                        "textStyle": {},
                                    }
                                },
                                {"footnoteReference": {"footnoteId": "kix.fn1"}},
                                {
                                    "textRun": {
                                        "content": ".\n",
                                        "textStyle": {},
                                    }
                                },
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    }
                ]
            },
        }

        md = convert_doc_to_markdown(doc)

        assert "Important claim[^kix.fn1]." in md
        assert (
            "[^kix.fn1]: See **styled** [link](https://example.com/link) "
            "[Ada Lovelace](mailto:ada@example.com) "
            "[Project Plan](https://example.com/plan) "
            "![Chart](https://cdn.example.com/chart.png)"
        ) in md

    def test_horizontal_rule(self):
        doc = {
            "title": "Test",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "Above\n",
                                        "textStyle": {},
                                    }
                                }
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    },
                    {
                        "paragraph": {
                            "elements": [{"horizontalRule": {}}],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    },
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "Below\n",
                                        "textStyle": {},
                                    }
                                }
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    },
                ]
            },
        }
        md = convert_doc_to_markdown(doc)
        assert "---" in md

    def test_auto_text_page_number(self):
        doc = {
            "title": "Test",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "Page ",
                                        "textStyle": {},
                                    }
                                },
                                {"autoText": {"type": "PAGE_NUMBER"}},
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    }
                ]
            },
        }
        md = convert_doc_to_markdown(doc)
        assert "Page [Page #]" in md

    def test_equation_placeholder(self):
        doc = {
            "title": "Test",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "The formula is ",
                                        "textStyle": {},
                                    }
                                },
                                {"equation": {}},
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    }
                ]
            },
        }
        md = convert_doc_to_markdown(doc)
        assert "The formula is [Equation]" in md

    def test_page_break_marker(self):
        """Page breaks separate surrounding text with an explicit marker."""
        doc = {
            "title": "Test",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "Before",
                                        "textStyle": {},
                                    }
                                },
                                {"pageBreak": {}},
                                {
                                    "textRun": {
                                        "content": "After\n",
                                        "textStyle": {},
                                    }
                                },
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    }
                ]
            },
        }
        md = convert_doc_to_markdown(doc)
        assert "Before[Page Break]After" in md

    def test_column_and_section_break_markers(self):
        """Authored breaks are visible; the API's leading section break is not."""
        doc = {
            "title": "Test",
            "body": {
                "content": [
                    {"sectionBreak": {}},
                    {
                        "paragraph": {
                            "elements": [
                                {"textRun": {"content": "Before", "textStyle": {}}},
                                {"columnBreak": {}},
                                {"textRun": {"content": "After\n", "textStyle": {}}},
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    },
                    {"sectionBreak": {}},
                    {
                        "paragraph": {
                            "elements": [
                                {"textRun": {"content": "Next\n", "textStyle": {}}},
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    },
                ]
            },
        }

        md = convert_doc_to_markdown(doc)
        assert md == "Before[Column Break]After\n\n[Section Break]\n\nNext\n"


class TestEmptyDoc:
    def test_empty(self):
        md = convert_doc_to_markdown({"title": "Empty", "body": {"content": []}})
        assert md.strip() == ""


def _make_tab(title, tab_id, text):
    """Helper to build a tab structure with a single paragraph."""
    return {
        "tabProperties": {"title": title, "tabId": tab_id},
        "documentTab": {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": f"{text}\n",
                                        "textStyle": {},
                                    }
                                }
                            ],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    }
                ]
            }
        },
    }


class TestDocumentTabs:
    def test_single_tab_no_heading(self):
        """A single-tab doc should render without a tab heading."""
        doc = {"tabs": [_make_tab("Main", "t1", "Hello world")]}
        md = convert_doc_to_markdown(doc)
        assert "Hello world" in md
        assert "# Main" not in md

    def test_multi_tab_headings(self):
        """Multi-tab docs should get a heading per tab."""
        doc = {
            "tabs": [
                _make_tab("Overview", "t1", "First tab content"),
                _make_tab("Details", "t2", "Second tab content"),
            ]
        }
        md = convert_doc_to_markdown(doc)
        assert "# Overview" in md
        assert "First tab content" in md
        assert "# Details" in md
        assert "Second tab content" in md

    def test_multi_tab_keeps_empty_tabs(self):
        """Empty tabs should still render a heading in multi-tab docs."""
        doc = {
            "tabs": [
                _make_tab("Overview", "t1", "First tab content"),
                _make_tab("Empty", "t2", ""),
            ]
        }
        md = convert_doc_to_markdown(doc)
        assert "# Overview" in md
        assert "First tab content" in md
        assert "# Empty" in md

    def test_child_tabs(self):
        """Child tabs should be flattened and rendered."""
        parent = _make_tab("Parent", "t1", "Parent content")
        child = _make_tab("Child", "t2", "Child content")
        parent["childTabs"] = [child]
        doc = {"tabs": [parent]}
        md = convert_doc_to_markdown(doc)
        assert "# Parent" in md
        assert "Parent content" in md
        assert "# Child" in md
        assert "Child content" in md

    def test_legacy_body_fallback(self):
        """Docs without tabs should still work via legacy body field."""
        md = convert_doc_to_markdown(SIMPLE_DOC)
        assert "Hello world" in md


# --- Comment parsing tests ---


class TestParseComments:
    def test_filters_resolved(self):
        response = {
            "comments": [
                {
                    "content": "open",
                    "resolved": False,
                    "author": {"displayName": "A"},
                    "replies": [],
                },
                {
                    "content": "closed",
                    "resolved": True,
                    "author": {"displayName": "B"},
                    "replies": [],
                },
            ]
        }
        result = parse_drive_comments(response, include_resolved=False)
        assert len(result) == 1
        assert result[0]["content"] == "open"

    def test_includes_resolved(self):
        response = {
            "comments": [
                {
                    "content": "open",
                    "resolved": False,
                    "author": {"displayName": "A"},
                    "replies": [],
                },
                {
                    "content": "closed",
                    "resolved": True,
                    "author": {"displayName": "B"},
                    "replies": [],
                },
            ]
        }
        result = parse_drive_comments(response, include_resolved=True)
        assert len(result) == 2

    def test_anchor_text(self):
        response = {
            "comments": [
                {
                    "content": "note",
                    "resolved": False,
                    "author": {"displayName": "A"},
                    "quotedFileContent": {"value": "highlighted text"},
                    "replies": [],
                }
            ]
        }
        result = parse_drive_comments(response)
        assert result[0]["anchor_text"] == "highlighted text"


# --- Comment formatting tests ---


class TestInlineComments:
    def test_inserts_footnote(self):
        md = "Some text here."
        comments = [
            {
                "author": "Alice",
                "content": "Note.",
                "anchor_text": "text",
                "replies": [],
                "resolved": False,
            }
        ]
        result = format_comments_inline(md, comments)
        assert "text[^c1]" in result
        assert "[^c1]: **Alice**: Note." in result

    def test_unmatched_goes_to_appendix(self):
        md = "No match."
        comments = [
            {
                "author": "Alice",
                "content": "Note.",
                "anchor_text": "missing",
                "replies": [],
                "resolved": False,
            }
        ]
        result = format_comments_inline(md, comments)
        assert "## Comments" in result
        assert "> missing" in result


class TestAppendixComments:
    def test_structure(self):
        comments = [
            {
                "author": "Alice",
                "content": "Note.",
                "anchor_text": "some text",
                "replies": [],
                "resolved": False,
            }
        ]
        result = format_comments_appendix(comments)
        assert "## Comments" in result
        assert "> some text" in result
        assert "**Alice**: Note." in result

    def test_empty(self):
        assert format_comments_appendix([]).strip() == ""
