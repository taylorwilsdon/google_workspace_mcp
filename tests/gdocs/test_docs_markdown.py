"""Tests for the Google Docs to Markdown converter."""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gdocs.docs_markdown import (
    convert_doc_to_markdown,
    format_comments_appendix,
    format_comments_inline,
    parse_drive_comments,
)
from gdocs.docs_structure import parse_document_structure


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


class TestEmptyDoc:
    def test_empty(self):
        md = convert_doc_to_markdown({"title": "Empty", "body": {"content": []}})
        assert md.strip() == ""


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


# --- Smart chip fixtures ---

RICH_LINK_DOC = {
    "title": "Rich Link Test",
    "body": {
        "content": [
            {"sectionBreak": {"sectionStyle": {}}},
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "Meeting on ", "textStyle": {}}},
                        {
                            "richLink": {
                                "richLinkProperties": {
                                    "title": "March 28, 2026",
                                    "uri": "https://calendar.google.com/event/abc123",
                                }
                            }
                        },
                        {"textRun": {"content": " confirmed.\n", "textStyle": {}}},
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                }
            },
        ]
    },
}

RICH_LINK_TITLE_ONLY_DOC = {
    "title": "Rich Link Title Only",
    "body": {
        "content": [
            {
                "paragraph": {
                    "elements": [
                        {
                            "richLink": {
                                "richLinkProperties": {
                                    "title": "March 28, 2026",
                                }
                            }
                        },
                        {"textRun": {"content": "\n", "textStyle": {}}},
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                }
            },
        ]
    },
}

RICH_LINK_URI_ONLY_DOC = {
    "title": "Rich Link URI Only",
    "body": {
        "content": [
            {
                "paragraph": {
                    "elements": [
                        {
                            "richLink": {
                                "richLinkProperties": {
                                    "uri": "https://docs.google.com/document/d/abc",
                                }
                            }
                        },
                        {"textRun": {"content": "\n", "textStyle": {}}},
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                }
            },
        ]
    },
}

PERSON_DOC = {
    "title": "Person Chip Test",
    "body": {
        "content": [
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "Assigned to ", "textStyle": {}}},
                        {
                            "person": {
                                "personProperties": {
                                    "name": "Jane Doe",
                                    "email": "jane@example.com",
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

PERSON_EMAIL_ONLY_DOC = {
    "title": "Person Email Only",
    "body": {
        "content": [
            {
                "paragraph": {
                    "elements": [
                        {
                            "person": {
                                "personProperties": {
                                    "email": "unknown@example.com",
                                }
                            }
                        },
                        {"textRun": {"content": "\n", "textStyle": {}}},
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                }
            },
        ]
    },
}

MIXED_CHIPS_DOC = {
    "title": "Mixed Chips",
    "body": {
        "content": [
            {
                "paragraph": {
                    "elements": [
                        {
                            "person": {
                                "personProperties": {
                                    "name": "Alice",
                                    "email": "alice@example.com",
                                }
                            }
                        },
                        {"textRun": {"content": " scheduled ", "textStyle": {}}},
                        {
                            "richLink": {
                                "richLinkProperties": {
                                    "title": "April 1, 2026",
                                    "uri": "https://calendar.google.com/event/xyz",
                                }
                            }
                        },
                        {"textRun": {"content": "\n", "textStyle": {}}},
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                }
            },
        ]
    },
}

TABLE_WITH_CHIPS_DOC = {
    "title": "Table with Chips",
    "body": {
        "content": [
            {
                "table": {
                    "rows": 1,
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
                                                        "richLink": {
                                                            "richLinkProperties": {
                                                                "title": "March 28, 2026",
                                                                "uri": "https://cal.google.com/e/1",
                                                            }
                                                        }
                                                    },
                                                    {
                                                        "textRun": {
                                                            "content": "\n",
                                                            "textStyle": {},
                                                        }
                                                    },
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
                                                        "person": {
                                                            "personProperties": {
                                                                "name": "Bob",
                                                                "email": "bob@example.com",
                                                            }
                                                        }
                                                    },
                                                    {
                                                        "textRun": {
                                                            "content": "\n",
                                                            "textStyle": {},
                                                        }
                                                    },
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


# --- Smart chip tests ---


class TestRichLink:
    def test_rich_link_with_title_and_uri(self):
        md = convert_doc_to_markdown(RICH_LINK_DOC)
        assert "[March 28, 2026](https://calendar.google.com/event/abc123)" in md
        assert "Meeting on" in md
        assert "confirmed." in md

    def test_rich_link_title_only(self):
        md = convert_doc_to_markdown(RICH_LINK_TITLE_ONLY_DOC)
        assert "March 28, 2026" in md

    def test_rich_link_uri_only(self):
        md = convert_doc_to_markdown(RICH_LINK_URI_ONLY_DOC)
        assert "https://docs.google.com/document/d/abc" in md


class TestPersonChip:
    def test_person_with_name_and_email(self):
        md = convert_doc_to_markdown(PERSON_DOC)
        assert "Jane Doe (jane@example.com)" in md
        assert "Assigned to" in md

    def test_person_email_only(self):
        md = convert_doc_to_markdown(PERSON_EMAIL_ONLY_DOC)
        assert "unknown@example.com" in md


class TestMixedChips:
    def test_mixed_person_and_date(self):
        md = convert_doc_to_markdown(MIXED_CHIPS_DOC)
        assert "Alice (alice@example.com)" in md
        assert "scheduled" in md
        assert "[April 1, 2026](https://calendar.google.com/event/xyz)" in md


class TestSmartChipsInTable:
    def test_table_with_chips(self):
        md = convert_doc_to_markdown(TABLE_WITH_CHIPS_DOC)
        assert "[March 28, 2026](https://cal.google.com/e/1)" in md
        assert "Bob (bob@example.com)" in md


class TestStructureWithChips:
    def test_structure_extracts_rich_link_text(self):
        structure = parse_document_structure(RICH_LINK_DOC)
        paragraphs = [e for e in structure["body"] if e["type"] == "paragraph"]
        assert any("March 28, 2026" in p["text"] for p in paragraphs)

    def test_structure_extracts_person_text(self):
        structure = parse_document_structure(PERSON_DOC)
        paragraphs = [e for e in structure["body"] if e["type"] == "paragraph"]
        assert any("Jane Doe" in p["text"] for p in paragraphs)

    def test_structure_extracts_date_element_text(self):
        structure = parse_document_structure(DATE_ELEMENT_DOC)
        paragraphs = [e for e in structure["body"] if e["type"] == "paragraph"]
        assert any("Mar 28, 2026" in p["text"] for p in paragraphs)


# --- Date element (date chip) fixtures ---

DATE_ELEMENT_DOC = {
    "title": "Date Element Test",
    "body": {
        "content": [
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "Due by ", "textStyle": {}}},
                        {
                            "dateElement": {
                                "dateId": "kix.m0mpxr9nmt8c",
                                "textStyle": {},
                                "dateElementProperties": {
                                    "timestamp": "2026-03-28T12:00:00Z",
                                    "locale": "en",
                                    "dateFormat": "DATE_FORMAT_MONTH_DAY_YEAR_ABBREVIATED",
                                    "timeFormat": "TIME_FORMAT_DISABLED",
                                    "displayText": "Mar 28, 2026",
                                },
                            }
                        },
                        {"textRun": {"content": " please.\n", "textStyle": {}}},
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                }
            },
        ]
    },
}

DATE_ELEMENT_ONLY_DOC = {
    "title": "Date Only",
    "body": {
        "content": [
            {
                "paragraph": {
                    "elements": [
                        {
                            "dateElement": {
                                "dateId": "kix.abc123",
                                "textStyle": {},
                                "dateElementProperties": {
                                    "timestamp": "2026-01-15T12:00:00Z",
                                    "locale": "en",
                                    "dateFormat": "DATE_FORMAT_MONTH_DAY_YEAR_ABBREVIATED",
                                    "timeFormat": "TIME_FORMAT_DISABLED",
                                    "displayText": "Jan 15, 2026",
                                },
                            }
                        },
                        {"textRun": {"content": "\n", "textStyle": {}}},
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                }
            },
        ]
    },
}


class TestDateElement:
    def test_date_chip_with_surrounding_text(self):
        md = convert_doc_to_markdown(DATE_ELEMENT_DOC)
        assert "Due by" in md
        assert "Mar 28, 2026" in md
        assert "please." in md

    def test_date_chip_standalone(self):
        md = convert_doc_to_markdown(DATE_ELEMENT_ONLY_DOC)
        assert "Jan 15, 2026" in md
