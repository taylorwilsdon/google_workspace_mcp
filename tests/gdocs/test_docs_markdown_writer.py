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
