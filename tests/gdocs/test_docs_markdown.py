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
