"""Findings 26 and 27 in the Markdown-to-Docs writer.

26: link and image URLs went into `updateTextStyle` untouched, so `javascript:` links
    written through `populate_from_markdown` persisted into a shared document and were
    reproduced when it was read back as markdown.
27: cursor arithmetic used `len(text)`, but Docs API indexes count UTF-16 code units.
    A non-BMP character is one Python character and two UTF-16 units, so every style
    range after an emoji landed in the wrong place.
"""

import pytest

from gdocs.docs_markdown_writer import markdown_to_docs_requests, utf16_len


def _inserted_text(requests):
    return "".join(r["insertText"]["text"] for r in requests if "insertText" in r)


def _link_urls(requests):
    urls = []
    for r in requests:
        style = r.get("updateTextStyle", {}).get("textStyle", {})
        if "link" in style:
            urls.append(style["link"]["url"])
    return urls


def _style_ranges(requests, field=None):
    ranges = []
    for r in requests:
        uts = r.get("updateTextStyle")
        if not uts:
            continue
        if field and field not in uts.get("textStyle", {}):
            continue
        ranges.append((uts["range"]["startIndex"], uts["range"]["endIndex"]))
    return ranges


class TestUtf16Len:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("", 0),
            ("abc", 3),
            ("日本語", 3),  # BMP: one unit each
            ("😀", 2),  # non-BMP: surrogate pair
            ("a😀b", 4),
            ("👨‍👩‍👧", 8),  # three non-BMP + two ZWJ
        ],
    )
    def test_counts_utf16_code_units(self, text, expected):
        assert utf16_len(text) == expected


class TestLinkSchemes:
    def test_javascript_link_is_not_written(self):
        requests = markdown_to_docs_requests(
            "[Click here](javascript:alert(document.cookie))"
        )

        assert _link_urls(requests) == []
        # The visible text is still inserted; only the link is dropped.
        assert "Click here" in _inserted_text(requests)

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
        ],
    )
    def test_dangerous_schemes_are_dropped(self, url):
        requests = markdown_to_docs_requests(f"[x]({url})")

        assert _link_urls(requests) == []

    def test_http_and_https_links_are_kept(self):
        requests = markdown_to_docs_requests(
            "[a](https://example.com/a) and [b](http://example.com/b)"
        )

        assert _link_urls(requests) == [
            "https://example.com/a",
            "http://example.com/b",
        ]

    def test_image_with_dangerous_src_keeps_alt_text_without_a_link(self):
        requests = markdown_to_docs_requests("![diagram](javascript:alert(1))")

        assert _link_urls(requests) == []
        assert "diagram" in _inserted_text(requests)

    def test_image_with_safe_src_is_linked(self):
        requests = markdown_to_docs_requests("![diagram](https://example.com/d.png)")

        assert _link_urls(requests) == ["https://example.com/d.png"]


class TestUtf16Indexing:
    def test_bold_range_after_an_emoji_accounts_for_the_surrogate_pair(self):
        """The emoji occupies two units, so the bold run starts two later."""
        requests = markdown_to_docs_requests("😀**bold**")

        bold_ranges = _style_ranges(requests, "bold")
        assert len(bold_ranges) == 1
        start, end = bold_ranges[0]
        # start_index defaults to 1; the emoji is 2 units, so bold starts at 3.
        assert (start, end) == (3, 7)

    def test_link_range_after_an_emoji_is_correct(self):
        requests = markdown_to_docs_requests("😀[x](https://example.com)")

        link_ranges = _style_ranges(requests, "link")
        assert link_ranges == [(3, 4)]

    def test_bmp_only_text_is_unaffected(self):
        """The change must not shift ranges for ordinary text."""
        requests = markdown_to_docs_requests("ab**cd**")

        assert _style_ranges(requests, "bold") == [(3, 5)]

    def test_cursor_advances_by_utf16_units_across_blocks(self):
        """A later paragraph's styles must also be offset by the emoji."""
        requests = markdown_to_docs_requests("😀\n\n**x**")

        bold_ranges = _style_ranges(requests, "bold")
        assert len(bold_ranges) == 1
        # "😀" (2) + "\n" (1) + spacer "\n" (1) = 4 units after start_index 1.
        assert bold_ranges[0][0] == 5

    def test_emoji_inside_a_code_span_is_measured_in_units(self):
        requests = markdown_to_docs_requests("`😀`")

        code_ranges = _style_ranges(requests, "weightedFontFamily")
        assert code_ranges == [(1, 3)]
