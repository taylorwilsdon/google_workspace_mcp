"""Unit tests for gdocs.docs_markdown_writer."""

import pathlib

from gdocs.docs_markdown_writer import markdown_to_docs_requests

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures"


def test_empty_markdown_returns_empty_list():
    requests = markdown_to_docs_requests("")
    assert requests == []


def test_returns_list_of_dicts():
    requests = markdown_to_docs_requests("Hello world")
    assert isinstance(requests, list)
    assert len(requests) >= 1, "Non-empty input should produce at least one request"
    assert all(isinstance(r, dict) for r in requests)


def test_single_paragraph_emits_insert_text():
    requests = markdown_to_docs_requests("Hello world")
    inserts = [r for r in requests if "insertText" in r]
    # Two inserts - the paragraph text plus a blank spacer paragraph
    assert len(inserts) == 2
    assert inserts[0]["insertText"]["text"] == "Hello world\n"
    assert inserts[0]["insertText"]["location"]["index"] == 1
    # Spacer paragraph follows immediately after the paragraph text
    assert inserts[1]["insertText"]["text"] == "\n"
    assert inserts[1]["insertText"]["location"]["index"] == 1 + len("Hello world\n")


def test_two_paragraphs_emit_two_inserts_with_correct_indices():
    requests = markdown_to_docs_requests("First para\n\nSecond para")
    inserts = [r for r in requests if "insertText" in r]
    # Four inserts - each top-level paragraph is followed by a blank spacer
    # paragraph, so two paragraphs yields: text1, spacer1, text2, spacer2.
    assert len(inserts) == 4
    assert inserts[0]["insertText"]["text"] == "First para\n"
    assert inserts[0]["insertText"]["location"]["index"] == 1
    # Spacer after the first paragraph
    assert inserts[1]["insertText"]["text"] == "\n"
    assert inserts[1]["insertText"]["location"]["index"] == 1 + len("First para\n")
    # Second paragraph starts after first paragraph text + spacer newline
    assert inserts[2]["insertText"]["text"] == "Second para\n"
    assert inserts[2]["insertText"]["location"]["index"] == 1 + len("First para\n") + 1
    # Trailing spacer after the second paragraph
    assert inserts[3]["insertText"]["text"] == "\n"
    assert inserts[3]["insertText"]["location"]["index"] == (
        1 + len("First para\n") + 1 + len("Second para\n")
    )


def test_h1_emits_insert_and_heading_style():
    requests = markdown_to_docs_requests("# My Title")
    inserts = [r for r in requests if "insertText" in r]
    styles = [r for r in requests if "updateParagraphStyle" in r]
    # Two inserts - the heading text plus a blank spacer paragraph
    assert len(inserts) == 2
    assert inserts[0]["insertText"]["text"] == "My Title\n"
    assert inserts[1]["insertText"]["text"] == "\n"
    assert inserts[1]["insertText"]["location"]["index"] == 1 + len("My Title\n")
    assert len(styles) == 1
    assert (
        styles[0]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"]
        == "HEADING_1"
    )
    # Range should cover the heading text (not the spacer)
    rng = styles[0]["updateParagraphStyle"]["range"]
    assert rng["startIndex"] == 1
    assert rng["endIndex"] == 1 + len("My Title\n")


def test_h2_h3_h4_h5_h6_all_emit_correct_named_style():
    for level in range(2, 7):
        hashes = "#" * level
        md = f"{hashes} Heading L{level}"
        requests = markdown_to_docs_requests(md)
        styles = [r for r in requests if "updateParagraphStyle" in r]
        assert len(styles) == 1
        assert (
            styles[0]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"]
            == f"HEADING_{level}"
        )


def test_bold_span_emits_update_text_style():
    requests = markdown_to_docs_requests("This is **bold** text.")
    inserts = [r for r in requests if "insertText" in r]
    styles = [r for r in requests if "updateTextStyle" in r]
    # Two inserts - the paragraph text plus a blank spacer paragraph
    assert len(inserts) == 2
    assert inserts[0]["insertText"]["text"] == "This is bold text.\n"
    assert inserts[1]["insertText"]["text"] == "\n"
    assert len(styles) == 1
    ts = styles[0]["updateTextStyle"]
    assert ts["textStyle"]["bold"] is True
    rng = ts["range"]
    assert rng["startIndex"] == 1 + len("This is ")
    assert rng["endIndex"] == rng["startIndex"] + len("bold")


def test_italic_span_emits_italic_style():
    requests = markdown_to_docs_requests("Some *italic* word.")
    styles = [r for r in requests if "updateTextStyle" in r]
    assert len(styles) == 1
    assert styles[0]["updateTextStyle"]["textStyle"]["italic"] is True


def test_inline_code_emits_monospace_style():
    requests = markdown_to_docs_requests("Use the `foo()` function.")
    styles = [r for r in requests if "updateTextStyle" in r]
    assert len(styles) == 1
    ts = styles[0]["updateTextStyle"]["textStyle"]
    assert ts.get("weightedFontFamily", {}).get("fontFamily") == "Courier New"


def test_link_emits_link_style():
    requests = markdown_to_docs_requests("See [docs](https://example.com) here.")
    styles = [r for r in requests if "updateTextStyle" in r]
    assert len(styles) == 1
    assert (
        styles[0]["updateTextStyle"]["textStyle"]["link"]["url"]
        == "https://example.com"
    )


def test_combined_bold_and_italic_spans():
    requests = markdown_to_docs_requests("A **bold** and *italic* mix.")
    styles = [r for r in requests if "updateTextStyle" in r]
    assert len(styles) == 2
    style_types = sorted(
        [
            "bold" if s["updateTextStyle"]["textStyle"].get("bold") else "italic"
            for s in styles
        ]
    )
    assert style_types == ["bold", "italic"]


def test_unordered_list_emits_bullets():
    md = "- Item one\n- Item two\n- Item three"
    requests = markdown_to_docs_requests(md)
    inserts = [r for r in requests if "insertText" in r]
    bullets = [r for r in requests if "createParagraphBullets" in r]
    # Four inserts - three list item paragraphs plus a single trailing spacer
    # emitted after the whole list. List items themselves remain tight.
    assert len(inserts) == 4
    assert inserts[0]["insertText"]["text"] == "Item one\n"
    assert inserts[1]["insertText"]["text"] == "Item two\n"
    assert inserts[2]["insertText"]["text"] == "Item three\n"
    assert inserts[3]["insertText"]["text"] == "\n"
    # One bullet creation request covering all three items
    assert len(bullets) == 1
    preset = bullets[0]["createParagraphBullets"]["bulletPreset"]
    assert preset == "BULLET_DISC_CIRCLE_SQUARE"
    # Bullet range must not include the trailing spacer paragraph
    rng = bullets[0]["createParagraphBullets"]["range"]
    assert rng["endIndex"] == 1 + len("Item one\n") + len("Item two\n") + len(
        "Item three\n"
    )


def test_ordered_list_emits_numbered_preset():
    md = "1. First\n2. Second\n3. Third"
    requests = markdown_to_docs_requests(md)
    bullets = [r for r in requests if "createParagraphBullets" in r]
    assert len(bullets) == 1
    preset = bullets[0]["createParagraphBullets"]["bulletPreset"]
    assert preset == "NUMBERED_DECIMAL_ALPHA_ROMAN"


def test_fenced_code_block_emits_monospace_style():
    md = "```python\ndef foo():\n    return 42\n```"
    requests = markdown_to_docs_requests(md)
    inserts = [r for r in requests if "insertText" in r]
    styles = [r for r in requests if "updateTextStyle" in r]
    # Two inserts - the fenced block content plus the trailing spacer paragraph.
    # The code insert carries exactly one trailing newline (the paragraph
    # terminator); the spacer provides the visual gap to the next block.
    assert len(inserts) == 2
    assert inserts[0]["insertText"]["text"] == "def foo():\n    return 42\n"
    assert inserts[1]["insertText"]["text"] == "\n"
    assert len(styles) >= 1
    ts = styles[0]["updateTextStyle"]["textStyle"]
    assert ts.get("weightedFontFamily", {}).get("fontFamily") == "Courier New"


def test_empty_fenced_code_block_omits_zero_length_style_range():
    requests = markdown_to_docs_requests("```\n```")
    assert not any(
        r["updateTextStyle"]["range"]["startIndex"]
        >= r["updateTextStyle"]["range"]["endIndex"]
        for r in requests
        if "updateTextStyle" in r
    )


def test_image_markdown_preserves_alt_text_as_linked_text():
    requests = markdown_to_docs_requests("![Architecture](https://example.com/a.png)")
    inserts = [r for r in requests if "insertText" in r]
    styles = [r for r in requests if "updateTextStyle" in r]

    assert inserts[0]["insertText"]["text"] == "Architecture\n"
    assert styles[0]["updateTextStyle"]["textStyle"]["link"]["url"] == (
        "https://example.com/a.png"
    )


def test_blockquote_emits_indent():
    requests = markdown_to_docs_requests("> This is quoted.\n> Continued.")
    styles = [r for r in requests if "updateParagraphStyle" in r]
    # At least one paragraph style with a positive left indent
    indented = [
        s
        for s in styles
        if s["updateParagraphStyle"]["paragraphStyle"]
        .get("indentStart", {})
        .get("magnitude", 0)
        > 0
    ]
    assert len(indented) >= 1


def test_horizontal_rule_produces_separator_insert():
    # HR should emit some form of insertText separator between the surrounding paragraphs.
    requests = markdown_to_docs_requests("Before\n\n---\n\nAfter")
    inserts = [r for r in requests if "insertText" in r]
    # Expect at least 3 inserts: "Before\n", HR's separator, "After\n"
    assert len(inserts) >= 3


def test_tab_id_threaded_through_all_insert_text_requests():
    md = "# Heading\n\nParagraph with **bold**.\n\n- List item\n\n```python\ncode\n```"
    requests = markdown_to_docs_requests(md, tab_id="t.0.1")

    for r in requests:
        # Every request that has a location or range should carry tabId
        if "insertText" in r:
            assert r["insertText"]["location"].get("tabId") == "t.0.1", (
                f"Missing tabId in insertText: {r}"
            )
        if "updateTextStyle" in r:
            assert r["updateTextStyle"]["range"].get("tabId") == "t.0.1", (
                f"Missing tabId in updateTextStyle: {r}"
            )
        if "updateParagraphStyle" in r:
            assert r["updateParagraphStyle"]["range"].get("tabId") == "t.0.1", (
                f"Missing tabId in updateParagraphStyle: {r}"
            )
        if "createParagraphBullets" in r:
            assert r["createParagraphBullets"]["range"].get("tabId") == "t.0.1", (
                f"Missing tabId in createParagraphBullets: {r}"
            )


def test_no_tab_id_omits_tab_id_field_entirely():
    requests = markdown_to_docs_requests("# Heading\n\nBody.")
    for r in requests:
        if "insertText" in r:
            assert "tabId" not in r["insertText"]["location"]
        if "updateTextStyle" in r:
            assert "tabId" not in r["updateTextStyle"]["range"]
        if "updateParagraphStyle" in r:
            assert "tabId" not in r["updateParagraphStyle"]["range"]


def test_real_blog_article_produces_reasonable_request_list():
    md_path = FIXTURE_DIR / "sample_blog_article.md"
    md = md_path.read_text(encoding="utf-8")
    requests = markdown_to_docs_requests(md)
    # Smoke test - we expect many insertText and several updateParagraphStyle
    inserts = [r for r in requests if "insertText" in r]
    heading_styles = [
        r
        for r in requests
        if "updateParagraphStyle" in r
        and r["updateParagraphStyle"]["paragraphStyle"]
        .get("namedStyleType", "")
        .startswith("HEADING")
    ]
    assert len(inserts) >= 10, f"Expected many inserts, got {len(inserts)}"
    assert len(heading_styles) >= 3, (
        f"Expected several headings, got {len(heading_styles)}"
    )


def test_real_blog_article_indices_are_monotonic():
    md_path = FIXTURE_DIR / "sample_blog_article.md"
    md = md_path.read_text(encoding="utf-8")
    requests = markdown_to_docs_requests(md)
    inserts = [r for r in requests if "insertText" in r]
    indices = [r["insertText"]["location"]["index"] for r in inserts]
    assert indices == sorted(indices), (
        "insertText indices must be monotonic non-decreasing"
    )


def test_paragraphs_separated_by_blank_paragraph():
    """Top-level paragraphs have a blank paragraph between them for visual spacing."""
    requests = markdown_to_docs_requests("Para1\n\nPara2")
    inserts = [r for r in requests if "insertText" in r]
    texts = [r["insertText"]["text"] for r in inserts]
    # Expect "Para1\n", "\n" (spacer), "Para2\n", "\n" (trailing spacer)
    assert "Para1\n" in texts
    assert "Para2\n" in texts
    para1_idx = texts.index("Para1\n")
    para2_idx = texts.index("Para2\n")
    assert para2_idx > para1_idx + 1, "Blank spacer should exist between paragraphs"
    # And the spacer is a bare "\n"
    spacer_text = texts[para1_idx + 1]
    assert spacer_text == "\n"


def test_list_items_stay_tight_spacer_only_after_list():
    """List items should remain tightly stacked; spacer emits only after the whole list."""
    requests = markdown_to_docs_requests("- One\n- Two\n- Three")
    inserts = [r for r in requests if "insertText" in r]
    texts = [r["insertText"]["text"] for r in inserts]
    # Three list-item paragraphs followed by exactly one spacer "\n"
    assert texts == ["One\n", "Two\n", "Three\n", "\n"]


def test_blockquote_internal_paragraphs_stay_tight():
    """Blockquote internal paragraphs should remain tight; spacer emits only after the blockquote."""
    requests = markdown_to_docs_requests("> Line one\n>\n> Line two")
    inserts = [r for r in requests if "insertText" in r]
    texts = [r["insertText"]["text"] for r in inserts]
    # Two blockquote paragraphs followed by exactly one trailing spacer "\n".
    # No spacer between the two blockquote paragraphs.
    assert texts == ["Line one\n", "Line two\n", "\n"]


def test_paragraph_between_blocks_has_spacers_around_it():
    """Heading then paragraph - spacer after heading AND after paragraph."""
    requests = markdown_to_docs_requests("# Title\n\nBody text")
    inserts = [r for r in requests if "insertText" in r]
    texts = [r["insertText"]["text"] for r in inserts]
    # Heading, spacer, paragraph, spacer
    assert texts == ["Title\n", "\n", "Body text\n", "\n"]


SIMPLE_TABLE_MD = "| A | B |\n|---|---|\n| 1 | 2 |"


def test_table_emits_insert_table_with_correct_dimensions():
    requests = markdown_to_docs_requests(SIMPLE_TABLE_MD)
    tables = [r for r in requests if "insertTable" in r]
    assert len(tables) == 1
    t = tables[0]["insertTable"]
    assert t["rows"] == 2
    assert t["columns"] == 2
    assert t["location"]["index"] == 1


def test_table_does_not_emit_pipe_text_paragraphs():
    """The regression this feature fixes - table rows must never land as
    literal pipe-delimited paragraph text."""
    requests = markdown_to_docs_requests(SIMPLE_TABLE_MD)
    for r in requests:
        if "insertText" in r:
            assert "|" not in r["insertText"]["text"]


def test_table_cell_fill_indices_match_empty_table_layout():
    """Cells fill bottom-right to top-left at the deterministic indexes of a
    freshly inserted empty table - for a 2x2 table inserted at index 1 the
    cell paragraphs start at 5, 7, 10, 12."""
    requests = markdown_to_docs_requests(SIMPLE_TABLE_MD)
    inserts = [
        r["insertText"]
        for r in requests
        if "insertText" in r and r["insertText"]["text"] != "\n"
    ]
    fills = [(i["location"]["index"], i["text"]) for i in inserts]
    assert fills == [(12, "2"), (10, "1"), (7, "B"), (5, "A")]


def test_table_header_row_is_bolded():
    requests = markdown_to_docs_requests(SIMPLE_TABLE_MD)
    bold_ranges = [
        (
            r["updateTextStyle"]["range"]["startIndex"],
            r["updateTextStyle"]["range"]["endIndex"],
        )
        for r in requests
        if "updateTextStyle" in r and r["updateTextStyle"]["textStyle"].get("bold")
    ]
    # Header cells "B" at [7, 8) and "A" at [5, 6); body cells stay unstyled.
    assert sorted(bold_ranges) == [(5, 6), (7, 8)]


def test_content_after_table_lands_past_the_populated_table():
    """The cursor must account for the table's full footprint - structure
    plus inserted cell text - so following blocks do not overlap it."""
    requests = markdown_to_docs_requests(SIMPLE_TABLE_MD + "\n\nAfter")
    inserts = [r for r in requests if "insertText" in r]
    after = [i for i in inserts if i["insertText"]["text"] == "After\n"]
    assert len(after) == 1
    # An empty 2x2 table inserted at 1 spans [2, 14) with the next paragraph
    # at 14; four 1-char cells add 4; the spacer paragraph adds 1 - so
    # "After" starts at 19.
    assert after[0]["insertText"]["location"]["index"] == 19


def test_table_after_heading_parses_as_table():
    """A table directly under a heading (no blank line) must still emit
    insertTable - the acceptance-test layout that regressed in the field."""
    requests = markdown_to_docs_requests("## Register\n" + SIMPLE_TABLE_MD)
    tables = [r for r in requests if "insertTable" in r]
    assert len(tables) == 1


def test_table_with_inline_styles_and_special_chars_in_cells():
    md = '| Item | Price |\n|---|---|\n| **Widget** "Pro" | $300 |'
    requests = markdown_to_docs_requests(md)
    tables = [r for r in requests if "insertTable" in r]
    assert len(tables) == 1
    texts = [
        r["insertText"]["text"]
        for r in requests
        if "insertText" in r and r["insertText"]["text"] != "\n"
    ]
    assert 'Widget "Pro"' in texts
    assert "$300" in texts
    # The bold span inside the body cell is preserved
    bolds = [
        r
        for r in requests
        if "updateTextStyle" in r and r["updateTextStyle"]["textStyle"].get("bold")
    ]
    # Two header cells plus the **Widget** span
    assert len(bolds) == 3


def test_table_empty_cells_emit_no_inserts():
    md = "| A | B |\n|---|---|\n| 1 | |"
    requests = markdown_to_docs_requests(md)
    inserts = [
        r["insertText"]
        for r in requests
        if "insertText" in r and r["insertText"]["text"] != "\n"
    ]
    assert [i["text"] for i in inserts] == ["1", "B", "A"]


def test_table_threads_tab_id_through_all_requests():
    requests = markdown_to_docs_requests(SIMPLE_TABLE_MD, tab_id="t.0.1")
    tables = [r for r in requests if "insertTable" in r]
    assert tables[0]["insertTable"]["location"].get("tabId") == "t.0.1"
    for r in requests:
        if "insertText" in r:
            assert r["insertText"]["location"].get("tabId") == "t.0.1"
        if "updateTextStyle" in r:
            assert r["updateTextStyle"]["range"].get("tabId") == "t.0.1"


def test_multiple_tables_maintain_correct_offsets():
    md = SIMPLE_TABLE_MD + "\n\nBetween\n\n| X | Y |\n|---|---|\n| 9 | 8 |"
    requests = markdown_to_docs_requests(md)
    tables = [r["insertTable"] for r in requests if "insertTable" in r]
    assert len(tables) == 2
    # First table at 1; populated it ends at 18, spacer -> 19, "Between\n"
    # ends at 27, spacer -> 28 - the second table inserts there.
    assert tables[0]["location"]["index"] == 1
    assert tables[1]["location"]["index"] == 28


def test_emoji_paragraph_advances_cursor_in_utf16_units():
    """Docs indexes count UTF-16 code units - a non-BMP emoji is 2 units."""
    requests = markdown_to_docs_requests("Hi \U0001f600\n\nAfter")
    inserts = [r for r in requests if "insertText" in r]
    texts = [r["insertText"]["text"] for r in inserts]
    assert texts == ["Hi \U0001f600\n", "\n", "After\n", "\n"]
    # "Hi <emoji>\n" is 6 UTF-16 units (H, i, space, 2 for the emoji, newline),
    # so the spacer lands at 7 and "After" at 8.
    assert inserts[1]["insertText"]["location"]["index"] == 7
    assert inserts[2]["insertText"]["location"]["index"] == 8


def test_emoji_before_bold_span_offsets_style_range_in_utf16_units():
    requests = markdown_to_docs_requests("\U0001f600 **bold**")
    styles = [
        r
        for r in requests
        if "updateTextStyle" in r and r["updateTextStyle"]["textStyle"].get("bold")
    ]
    assert len(styles) == 1
    rng = styles[0]["updateTextStyle"]["range"]
    # "<emoji> " is 3 UTF-16 units, so bold spans [4, 8).
    assert rng["startIndex"] == 4
    assert rng["endIndex"] == 8


def test_emoji_in_table_cell_keeps_following_content_aligned():
    md = "| A | B |\n|---|---|\n| \U0001f600 | ok |\n\nAfter"
    requests = markdown_to_docs_requests(md)
    inserts = [r for r in requests if "insertText" in r]
    after = [i for i in inserts if i["insertText"]["text"] == "After\n"]
    assert len(after) == 1
    # Empty 2x2 table at 1 puts the next paragraph at 14; cell text adds
    # 1 (A) + 1 (B) + 2 (emoji) + 2 (ok) = 6 UTF-16 units; spacer adds 1 -
    # so "After" starts at 21.
    assert after[0]["insertText"]["location"]["index"] == 21


def test_emoji_header_cell_bold_range_uses_utf16_units():
    md = "| \U0001f600! | B |\n|---|---|\n| 1 | 2 |"
    requests = markdown_to_docs_requests(md)
    bold_ranges = [
        (
            r["updateTextStyle"]["range"]["startIndex"],
            r["updateTextStyle"]["range"]["endIndex"],
        )
        for r in requests
        if "updateTextStyle" in r and r["updateTextStyle"]["textStyle"].get("bold")
    ]
    # Header cell "<emoji>!" fills at 5 and spans 3 UTF-16 units - [5, 8).
    assert (5, 8) in bold_ranges


def test_wide_table_beyond_docs_limit_degrades_to_text():
    """Docs rejects tables wider than 20 columns - the converter degrades to
    plain text lines rather than failing the whole batch."""
    cols = 21
    header = "|" + "|".join(f" c{n} " for n in range(cols)) + "|"
    sep = "|" + "---|" * cols
    row = "|" + "|".join(f" v{n} " for n in range(cols)) + "|"
    requests = markdown_to_docs_requests("\n".join([header, sep, row]))
    assert not any("insertTable" in r for r in requests)
    texts = [r["insertText"]["text"] for r in requests if "insertText" in r]
    assert any(t.startswith("c0 | c1") for t in texts)
