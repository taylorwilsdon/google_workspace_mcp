"""Tests for bare-newline handling in HTML bodies (html_newlines_to_br)."""

import base64
from email import message_from_bytes
from email.policy import SMTP

import pytest

from gmail.gmail_helpers import _build_forward_content, html_newlines_to_br
from gmail.gmail_tools import _prepare_gmail_message

SIGNATURE_HTML = (
    '<div dir="ltr"><div><blockquote style="margin:0px 0px 0px 15px">'
    "<b>Jane Doe</b></blockquote><blockquote>Engineer</blockquote></div>"
    "<div><br></div></div>"
)


def _decode_parts(raw_b64: str) -> dict:
    msg = message_from_bytes(base64.urlsafe_b64decode(raw_b64), policy=SMTP)
    parts = {}
    for part in msg.walk():
        if part.get_content_type().startswith("text/"):
            # SMTP policy emits CRLF; compare against the LF the caller passed.
            parts[part.get_content_type()] = part.get_content().replace("\r\n", "\n")
    return parts


class TestHtmlNewlinesToBr:
    def test_bare_paragraphs_get_breaks(self):
        body = "Hi Jane,\n\nFirst paragraph.\nSecond line.\n\nRegards,\nJohn"
        assert html_newlines_to_br(body) == (
            "Hi Jane,<br><br>\nFirst paragraph.<br>\nSecond line.<br><br>\nRegards,<br>\nJohn"
        )

    def test_well_formed_html_is_untouched(self):
        body = (
            "<p>Hi Jane,</p>\n<p>Thanks.</p>\n<ul>\n<li>one</li>\n<li>two</li>\n</ul>"
        )
        assert html_newlines_to_br(body) == body

    @pytest.mark.parametrize(
        "tag",
        ["address", "aside", "fieldset", "figure", "footer", "header", "main", "nav"],
    )
    def test_semantic_block_elements_are_untouched(self, tag):
        body = f"<{tag}>content</{tag}>\nnext"
        assert html_newlines_to_br(body) == body

    def test_compact_html_is_untouched(self):
        body = "<p>Hi</p><p>Bye</p>"
        assert html_newlines_to_br(body) == body

    def test_newline_inside_paragraph_becomes_break(self):
        body = "<p>Hi,\nhow are you</p>\n<p>fine</p>"
        assert html_newlines_to_br(body) == "<p>Hi,<br>\nhow are you</p>\n<p>fine</p>"

    def test_inline_tags_do_not_swallow_breaks(self):
        body = "<b>Note:</b>\nnext line\n\n<a href='x'>link</a>\nend"
        assert html_newlines_to_br(body) == (
            "<b>Note:</b><br>\nnext line<br><br>\n<a href='x'>link</a><br>\nend"
        )

    def test_appended_signature_survives(self):
        body = "Hi,\nline two<br><br>" + SIGNATURE_HTML
        assert html_newlines_to_br(body) == "Hi,<br>\nline two<br><br>" + SIGNATURE_HTML

    def test_runs_of_newlines_cap_at_two_breaks(self):
        assert html_newlines_to_br("a\n\n\n\nb") == "a<br><br>\nb"

    @pytest.mark.parametrize(
        "body",
        [
            "<p>Log:</p>\n<pre>line 1\nline 2</pre>\nafter\nmore",
            "<style>\na{color:red}\nb{color:blue}\n</style>\ntext\nmore",
            "<script>\nlet a = 1;\nlet b = 2;\n</script>\ntext\nmore",
            "<TEXTAREA>one\ntwo</TEXTAREA>",
        ],
    )
    def test_raw_text_elements_are_never_touched(self, body):
        assert html_newlines_to_br(body) == body

    def test_pre_prefixed_tag_name_does_not_disable_conversion(self):
        assert html_newlines_to_br("<preview>a</preview>\nb") == (
            "<preview>a</preview><br>\nb"
        )

    def test_head_markup_is_untouched(self):
        body = (
            "<html>\n<head>\n<meta charset='utf-8'>\n<title>T</title>\n"
            "<link rel='x'>\n</head>\n<body>\n<p>Hi</p>\n</body>\n</html>"
        )
        assert html_newlines_to_br(body) == body

    def test_indentation_between_tags_is_formatting(self):
        body = "<div>\n  <span>a</span>\n  <span>b</span>\n</div>"
        assert html_newlines_to_br(body) == body

    def test_indented_text_still_gets_breaks(self):
        assert html_newlines_to_br("Items:\n  one\n  two") == (
            "Items:<br>\n  one<br>\n  two"
        )

    def test_newlines_just_inside_inline_element_are_formatting(self):
        body = '<td>\n<a href="x">\n<img src="y">\n</a>\n</td>'
        assert html_newlines_to_br(body) == body

    def test_newline_after_void_inline_tag_still_breaks(self):
        assert html_newlines_to_br("<img src='y'>\nCaption") == (
            "<img src='y'><br>\nCaption"
        )

    def test_leading_and_trailing_newlines_are_formatting(self):
        assert html_newlines_to_br("\nHi\n\nThanks\n") == "\nHi<br><br>\nThanks\n"

    def test_gt_inside_quoted_attribute_does_not_split_tag(self):
        body = "<p title=\"a>b\">\nx</p>\n<p data-x='1>0'>\ny</p>"
        assert html_newlines_to_br(body) == body

    def test_crlf_runs_cap_at_two_breaks(self):
        assert html_newlines_to_br("a\r\n\r\n\r\nb") == "a<br><br>\nb"

    def test_is_idempotent(self):
        once = html_newlines_to_br("Hi,\n\nline\nend")
        assert html_newlines_to_br(once) == once

    @pytest.mark.parametrize("body", ["", "no newlines here", "<p>one</p>"])
    def test_noop_inputs(self, body):
        assert html_newlines_to_br(body) == body


class TestPrepareGmailMessageNewlines:
    def test_html_body_is_not_rewritten(self):
        # Conversion happens on the caller's body before composition, so a
        # composed body carrying third-party markup reaches MIME untouched.
        body = "<div>\n<span>Hello</span>\n<span>world</span>\n</div>"
        raw_b64, _, _, _ = _prepare_gmail_message(
            subject="Test", body=body, to="jane@example.com", body_format="html"
        )
        assert _decode_parts(raw_b64)["text/html"].strip() == body

    def test_plain_body_is_unaffected(self):
        body = "Hi Jane,\n\nplain text stays plain."
        raw_b64, _, _, _ = _prepare_gmail_message(
            subject="Test", body=body, to="jane@example.com", body_format="plain"
        )
        parts = _decode_parts(raw_b64)
        assert "text/html" not in parts
        assert parts["text/plain"].strip() == body


class TestForwardNoteNewlines:
    def test_html_note_converted_but_original_untouched(self):
        original_html = "<div>\n<span>Hello</span>\n<span>world</span>\n</div>"
        _, body, body_format = _build_forward_content(
            headers={},
            bodies={"html": original_html, "text": ""},
            forward_message="FYI\n\nsee below",
            forward_message_format="html",
            subject_override=None,
        )
        assert body_format == "html"
        assert "<div>FYI<br><br>\nsee below</div>" in body
        assert original_html in body
