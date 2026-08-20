"""Tests for the thread digest: quoted-history stripping and the digest shape.

Covers three layers:
- the pure plain-text stripper in gmail_helpers
- the quote-aware HTML extractor in gmail_tools
- the digest builder and the `digest` flag on both thread tools

The stripper is a heuristic, so the false-positive guards matter as much as the
positive cases: a body it cannot read confidently must come back untouched.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest

from core.utils import UserInputError
from gmail.gmail_helpers import _strip_quoted_plaintext
from gmail.gmail_tools import (
    DIGEST_BODY_TRUNCATE_LIMIT,
    TRUNCATION_NOTICE,
    _build_thread_digest,
    _digest_body_content,
    _html_to_text,
    _html_to_text_with_quote_stats,
    get_gmail_thread_content,
    get_gmail_threads_content_batch,
)


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _message(
    msg_id: str,
    sender: str,
    date: str,
    text: str | None = None,
    html: str | None = None,
    subject: str = "Trailer pickup",
) -> dict:
    parts = []
    if text is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": _b64(text)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": _b64(html)}})
    return {
        "id": msg_id,
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "erik@example.com"},
                {"name": "Date", "value": date},
                {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": "<" + msg_id + "@mail.example.com>"},
            ],
            "parts": parts,
        },
    }


def _build_mock_service(thread_response: dict) -> MagicMock:
    service = MagicMock()
    service.users.return_value.threads.return_value.get.return_value.execute.return_value = thread_response
    return service


# ---------------------------------------------------------------------------
# Plain-text stripping: positive cases
# ---------------------------------------------------------------------------


class TestStripQuotedPlaintext:
    def test_gmail_on_wrote_header(self):
        body = (
            "Sounds good, Tuesday works.\n"
            "\n"
            "On Tue, Aug 12, 2026 at 9:03 AM Erik <erik@example.com> wrote:\n"
            "> Can we do Tuesday?\n"
            "> Thanks\n"
        )
        kept, removed, marker = _strip_quoted_plaintext(body)
        assert kept == "Sounds good, Tuesday works."
        assert marker == "on_wrote"
        assert removed > 0

    def test_gmail_on_wrote_header_wrapped_across_lines(self):
        """Gmail wraps long attribution lines; 'wrote:' lands on the next line."""
        body = (
            "Confirmed.\n"
            "\n"
            "On Tue, Aug 12, 2026 at 9:03 AM Erik Holzhauer <erik@example.com>\n"
            "wrote:\n"
            "> original text\n"
        )
        kept, removed, marker = _strip_quoted_plaintext(body)
        assert kept == "Confirmed."
        assert marker == "on_wrote"
        assert removed > 0

    def test_original_message_separator(self):
        body = (
            "See below.\n"
            "\n"
            "-----Original Message-----\n"
            "From: someone@example.com\n"
            "Subject: old\n"
        )
        kept, removed, marker = _strip_quoted_plaintext(body)
        assert kept == "See below."
        assert marker == "original_message"
        assert removed > 0

    def test_forwarded_message_separator(self):
        body = "FYI\n\n---------- Forwarded message ---------\nFrom: a@b.com\n"
        kept, _, marker = _strip_quoted_plaintext(body)
        assert kept == "FYI"
        assert marker == "forwarded_message"

    def test_outlook_header_block(self):
        body = (
            "Approved.\n"
            "\n"
            "From: Erik <erik@example.com>\n"
            "Sent: Tuesday, August 12, 2026 9:03 AM\n"
            "To: Vendor <vendor@example.com>\n"
            "Subject: Trailer pickup\n"
            "\n"
            "Original body here.\n"
        )
        kept, removed, marker = _strip_quoted_plaintext(body)
        assert kept == "Approved."
        assert marker == "outlook_header"
        assert removed > 0

    def test_outlook_underscore_separator(self):
        body = (
            "Works for me.\n"
            "\n"
            "________________________________\n"
            "From: Erik <erik@example.com>\n"
            "Sent: Tuesday\n"
        )
        kept, _, marker = _strip_quoted_plaintext(body)
        assert kept == "Works for me."
        assert marker == "outlook_separator"

    def test_quote_prefix_run(self):
        body = "Yes.\n\n> line one\n> line two\n> line three\n"
        kept, _, marker = _strip_quoted_plaintext(body)
        assert kept == "Yes."
        assert marker == "quote_prefix"


# ---------------------------------------------------------------------------
# Plain-text stripping: the guards that stop it cutting real content
# ---------------------------------------------------------------------------


class TestStripQuotedPlaintextGuards:
    def test_bare_from_line_is_not_a_quote_header(self):
        """'From:' alone is ordinary prose without a Sent/Date and To line."""
        body = (
            "Here is the breakdown.\n"
            "From: the Chicago yard we can move ten units.\n"
            "Let me know.\n"
        )
        kept, removed, marker = _strip_quoted_plaintext(body)
        assert kept == body
        assert removed == 0
        assert marker is None

    def test_single_stray_quote_line_is_not_a_run(self):
        body = (
            "He said:\n"
            "> we need the titles first\n"
            "which matches what Chris told me, so I will chase the titles today\n"
            "and come back to you with a date once they land.\n"
        )
        kept, removed, marker = _strip_quoted_plaintext(body)
        assert kept == body
        assert removed == 0
        assert marker is None

    def test_body_that_is_quote_from_first_line_is_kept_whole(self):
        """No new content to keep, so cutting would destroy the only copy."""
        body = "On Tue, Aug 12, 2026 at 9:03 AM Erik <erik@example.com> wrote:\n> hi\n"
        kept, removed, marker = _strip_quoted_plaintext(body)
        assert kept == body
        assert removed == 0
        assert marker is None

    def test_empty_body_unchanged(self):
        for body in ("", "   \n  \n"):
            kept, removed, marker = _strip_quoted_plaintext(body)
            assert kept == body
            assert removed == 0
            assert marker is None

    def test_body_with_no_quote_unchanged(self):
        body = "Short note with no history at all.\nSecond line.\n"
        kept, removed, marker = _strip_quoted_plaintext(body)
        assert kept == body
        assert removed == 0
        assert marker is None


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------


class TestHtmlQuoteStripping:
    def test_blockquote_removed_and_counted(self):
        html = "<div>New reply.</div><blockquote>Old quoted history.</blockquote>"
        text, removed, marker = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert text == "New reply."
        assert marker == "blockquote"
        assert removed == len("Old quoted history.")

    def test_gmail_quote_div_removed(self):
        html = '<div>Reply.</div><div class="gmail_quote">Quoted.</div>'
        text, removed, marker = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert text == "Reply."
        assert marker == "gmail_quote"
        assert removed > 0

    def test_outlook_container_id_removed(self):
        html = '<div>Reply.</div><div id="divRplyFwdMsg">Quoted.</div>'
        text, _, marker = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert text == "Reply."
        assert marker == "outlook_container"

    def test_nested_tags_do_not_close_quote_early(self):
        html = (
            "<div>Reply.</div>"
            "<blockquote><div><p>Old <b>bold</b> text</p></div></blockquote>"
            "<div>Trailing signature.</div>"
        )
        text, _, _ = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert "Old" not in text
        assert "bold" not in text
        assert "Reply." in text
        assert "Trailing signature." in text

    def test_void_tags_inside_quote_do_not_unbalance_nesting(self):
        html = (
            "<div>Reply.</div>"
            '<blockquote>Old<br><img src="x">more old</blockquote>'
            "<div>After.</div>"
        )
        text, _, _ = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert "Old" not in text
        assert "more old" not in text
        assert "After." in text

    def test_self_closing_br_does_not_end_the_quote(self):
        """HTMLParser dispatches "<br />" to BOTH handlers. Reading that end
        tag as the end of the quote leaks the rest of the quoted history."""
        html = (
            "<div>Reply.</div>"
            "<blockquote>old<br />STILL QUOTED</blockquote>"
            "<div>Sig.</div>"
        )
        text, removed, _ = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert "STILL QUOTED" not in text
        assert "old" not in text
        assert text == "Reply.Sig."
        assert removed == len("old") + len("STILL QUOTED")

    def test_unclosed_block_tags_do_not_swallow_later_content(self):
        """Unclosed <p> is everywhere in real email HTML. A blind nesting
        counter stays positive past </blockquote> and discards the rest of the
        message, which is the one thing this must never do."""
        html = (
            "<div>Reply.</div>"
            "<blockquote><p>old<p>more</blockquote>"
            "<div>Signature.</div>"
        )
        text, removed, _ = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert "Signature." in text
        assert "old" not in text
        assert "more" not in text
        assert removed == len("old") + len("more")

    def test_unclosed_list_items_do_not_swallow_later_content(self):
        html = (
            "<div>Reply.</div>"
            "<blockquote><ul><li>a<li>b</ul></blockquote>"
            "<div>After.</div>"
        )
        text, _, _ = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert "After." in text
        assert "a" not in text.replace("Reply.", "").replace("After.", "")

    def test_nested_same_tag_quote_closes_at_the_outer_container(self):
        html = (
            "<div>R.</div>"
            "<blockquote>a<blockquote>b</blockquote>c</blockquote>"
            "<div>After.</div>"
        )
        text, removed, _ = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert text == "R.After."
        assert removed == 3

    def test_stray_end_tag_inside_quote_does_not_end_it(self):
        html = (
            "<div>Reply.</div>"
            "<blockquote>old</span>still old</blockquote>"
            "<div>After.</div>"
        )
        text, _, _ = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert "still old" not in text
        assert "After." in text

    def test_unclosed_quote_container_keeps_the_whole_body(self):
        """When the container never closes, the parser cannot tell where the
        quote ended. Keeping duplication beats discarding real content."""
        html = '<div>Reply.</div><div class="gmail_quote"><div>old</div>Trailing.'
        text, removed, marker = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert "Reply." in text
        assert "Trailing." in text
        assert "old" in text
        assert removed == 0
        assert marker is None

    def test_unclosed_blockquote_keeps_the_whole_body(self):
        html = "<div>Reply.</div><blockquote>old<div>Trailing.</div>"
        text, removed, marker = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert "Trailing." in text
        assert removed == 0
        assert marker is None

    def test_unclosed_nested_same_tag_inside_quote_keeps_the_body(self):
        """The nested <div> closes but the container does not, so the quote is
        still open at the end of the document."""
        html = '<div>R.</div><div class="gmail_quote">a<div>b</div>c'
        text, removed, marker = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert text == "R.abc"
        assert removed == 0
        assert marker is None

    def test_closed_quote_after_an_unclosed_child_still_strips(self):
        """Guard against over-correcting: a properly closed container must
        still strip even when its children are unbalanced."""
        html = (
            "<div>Reply.</div>"
            '<div class="gmail_quote"><p>old<p>more</div>'
            "<div>Sig.</div>"
        )
        text, removed, marker = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert text == "Reply.Sig."
        assert removed == len("old") + len("more")
        assert marker == "gmail_quote"

    def test_trailing_incomplete_entity_is_not_lost(self):
        """feed() withholds a tail like "&amp" with no semicolon; close()
        flushes it. Without that, the whole body came back empty."""
        assert _html_to_text("<div>Tom &amp") == "Tom &"
        text, _, _ = _html_to_text_with_quote_stats("<div>Tom &amp", strip_quotes=True)
        assert text == "Tom &"

    def test_class_markers_match_whole_tokens_not_substrings(self):
        """A substring test lets "notgmail_quote" masquerade as a quote
        container and delete real content."""
        html = '<div>Keep me.</div><div class="notgmail_quote">REAL CONTENT</div>'
        text, removed, marker = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert "REAL CONTENT" in text
        assert removed == 0
        assert marker is None

    def test_marker_embedded_in_a_longer_token_is_not_matched(self):
        html = '<div>Keep.</div><div class="x-gmail_quote-ish">CONTENT</div>'
        text, removed, marker = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert "CONTENT" in text
        assert removed == 0
        assert marker is None

    def test_marker_among_several_class_tokens_is_matched(self):
        html = '<div>R.</div><div class="foo gmail_quote bar">old</div><div>A.</div>'
        text, _, marker = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert text == "R.A."
        assert marker == "gmail_quote"

    def test_gmail_quote_container_variant_is_still_matched(self):
        """Exact-token matching would have dropped this variant, which the old
        substring test only caught by accident."""
        html = '<div>R.</div><div class="gmail_quote_container">old</div><div>A.</div>'
        text, _, marker = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert text == "R.A."
        assert marker == "gmail_quote_container"

    def test_class_matching_is_case_insensitive(self):
        html = '<div>R.</div><div class="GMail_Quote">old</div><div>A.</div>'
        text, _, marker = _html_to_text_with_quote_stats(html, strip_quotes=True)
        assert text == "R.A."
        assert marker == "gmail_quote"

    def test_strip_quotes_off_keeps_everything(self):
        html = "<div>New reply.</div><blockquote>Old quoted history.</blockquote>"
        text, removed, marker = _html_to_text_with_quote_stats(html, strip_quotes=False)
        assert "Old quoted history." in text
        assert removed == 0
        assert marker is None

    def test_html_to_text_behavior_unchanged(self):
        """The existing helper keeps its signature and its output."""
        assert _html_to_text("<div>Best,<br>Alice</div>") == "Best, Alice"
        assert _html_to_text("<script>x<br>y</script><p>Visible</p>") == "Visible"
        # Pre-existing behavior: only <br> introduces a space, and quoted
        # content is NOT stripped on this path.
        assert _html_to_text("<div>A</div><blockquote>B</blockquote>") == "AB"


# ---------------------------------------------------------------------------
# Digest body assembly
# ---------------------------------------------------------------------------


class TestDigestBodyContent:
    def test_plain_text_body_is_stripped_and_reported(self):
        text = (
            "New line.\n\nOn Tue, Aug 12, 2026 at 9:03 AM E <e@x.com> wrote:\n> old\n"
        )
        body = _digest_body_content(text, "")
        assert body["content"] == "New line."
        assert body["source"] == "text"
        assert body["removed_chars"] > 0
        assert body["marker"] == "on_wrote"
        assert body["truncated"] is False

    def test_html_only_body_is_stripped(self):
        html = "<div>Just this.</div><blockquote>All the history.</blockquote>"
        body = _digest_body_content("", html)
        assert body["content"] == "Just this."
        assert body["source"] == "html"
        assert body["removed_chars"] > 0

    def test_entirely_quoted_html_falls_back_rather_than_returning_nothing(self):
        """Stripping must never be the reason a caller sees an empty body."""
        html = "<blockquote>Everything is quoted.</blockquote>"
        body = _digest_body_content("", html)
        assert "Everything is quoted." in body["content"]
        assert body["removed_chars"] == 0
        assert body["marker"] is None

    def test_long_body_is_capped_and_flagged(self):
        text = "x" * (DIGEST_BODY_TRUNCATE_LIMIT + 500)
        body = _digest_body_content(text, "")
        assert body["truncated"] is True
        assert len(body["content"]) < len(text)
        assert "[Content truncated...]" in body["content"]

    def test_truncated_body_stays_within_the_limit(self):
        """_truncate_content appends a notice, so the digest has to reserve
        room for it or a body capped at N comes back longer than N."""
        text = "x" * (DIGEST_BODY_TRUNCATE_LIMIT + 500)
        body = _digest_body_content(text, "")
        assert body["truncated"] is True
        assert len(body["content"]) <= DIGEST_BODY_TRUNCATE_LIMIT
        assert TRUNCATION_NOTICE.strip() in body["content"]

    def test_body_exactly_at_the_limit_is_not_truncated(self):
        text = "x" * DIGEST_BODY_TRUNCATE_LIMIT
        body = _digest_body_content(text, "")
        assert body["truncated"] is False
        assert len(body["content"]) == DIGEST_BODY_TRUNCATE_LIMIT

    def test_absurdly_small_limit_does_not_produce_a_negative_slice(self):
        body = _digest_body_content("x" * 100, "", max_chars=5)
        assert body["truncated"] is True
        assert TRUNCATION_NOTICE.strip() in body["content"]

    def test_empty_body_reports_no_readable_content(self):
        body = _digest_body_content("", "")
        assert body["content"] == "[No readable content found]"
        assert body["removed_chars"] == 0


# ---------------------------------------------------------------------------
# Digest builder
# ---------------------------------------------------------------------------


def _quoted_thread() -> dict:
    """A three-message thread where each reply quotes everything before it."""
    m1 = "Can you move the ten reefers the week of the 31st?"
    m2 = (
        "Yes, that works.\n"
        "\n"
        "On Mon, Aug 11, 2026 at 8:00 AM Erik <erik@example.com> wrote:\n"
        "> " + m1 + "\n"
    )
    m3 = (
        "Booked for Tuesday.\n"
        "\n"
        "On Tue, Aug 12, 2026 at 9:03 AM Vendor <vendor@example.com> wrote:\n"
        "> Yes, that works.\n"
        "> On Mon, Aug 11, 2026 at 8:00 AM Erik <erik@example.com> wrote:\n"
        "> > " + m1 + "\n"
    )
    return {
        "id": "t1",
        "messages": [
            _message(
                "m1",
                "Erik <erik@example.com>",
                "Mon, 11 Aug 2026 08:00:00 -0600",
                text=m1,
            ),
            _message(
                "m2",
                "Vendor <vendor@example.com>",
                "Tue, 12 Aug 2026 09:03:00 -0600",
                text=m2,
            ),
            _message(
                "m3",
                "Erik <erik@example.com>",
                "Tue, 12 Aug 2026 15:00:00 -0600",
                text=m3,
            ),
        ],
    }


class TestBuildThreadDigest:
    def test_shape_and_per_message_fields(self):
        digest = _build_thread_digest(_quoted_thread(), "t1")
        assert digest["thread_id"] == "t1"
        assert digest["subject"] == "Trailer pickup"
        assert digest["message_count"] == 3
        assert len(digest["messages"]) == 3

        first = digest["messages"][0]
        assert first["index"] == 1
        assert first["id"] == "m1"
        assert first["from"] == "Erik <erik@example.com>"
        assert first["date"] == "Mon, 11 Aug 2026 08:00:00 -0600"
        assert first["message_id_header"] == "<m1@mail.example.com>"
        assert first["subject"] is None  # same as thread subject, so omitted

    def test_quoted_history_is_removed_from_later_messages(self):
        digest = _build_thread_digest(_quoted_thread(), "t1")
        contents = [message["content"] for message in digest["messages"]]
        assert contents[0].startswith("Can you move the ten reefers")
        assert contents[1] == "Yes, that works."
        assert contents[2] == "Booked for Tuesday."
        # The first message's text must appear exactly once across the digest.
        assert sum("ten reefers" in content for content in contents) == 1

    def test_stats_report_every_removal(self):
        digest = _build_thread_digest(_quoted_thread(), "t1")
        stats = digest["stats"]
        assert stats["quoted_chars_removed"] > 0
        assert stats["messages_truncated"] == 0
        assert stats["digest_chars"] < stats["original_chars"]
        per_message = sum(m["quoted_chars_removed"] for m in digest["messages"])
        assert stats["quoted_chars_removed"] == per_message

    def test_empty_thread(self):
        digest = _build_thread_digest({"messages": []}, "t0")
        assert digest["message_count"] == 0
        assert digest["messages"] == []
        assert digest["stats"]["quoted_chars_removed"] == 0

    def test_differing_subject_is_kept(self):
        thread = _quoted_thread()
        thread["messages"][1]["payload"]["headers"] = [
            header
            for header in thread["messages"][1]["payload"]["headers"]
            if header["name"] != "Subject"
        ] + [{"name": "Subject", "value": "Re: Trailer pickup - revised"}]
        digest = _build_thread_digest(thread, "t1")
        assert digest["messages"][1]["subject"] == "Re: Trailer pickup - revised"


# ---------------------------------------------------------------------------
# Tool wiring
# ---------------------------------------------------------------------------


class TestToolWiring:
    @pytest.mark.asyncio
    async def test_default_still_returns_string(self):
        service = _build_mock_service(_quoted_thread())
        result = await _unwrap(get_gmail_thread_content)(
            service=service, thread_id="t1", user_google_email="erik@example.com"
        )
        assert isinstance(result, str)
        assert "Thread ID: t1" in result

    @pytest.mark.asyncio
    async def test_digest_true_returns_structured_dict(self):
        service = _build_mock_service(_quoted_thread())
        result = await _unwrap(get_gmail_thread_content)(
            service=service,
            thread_id="t1",
            user_google_email="erik@example.com",
            digest=True,
        )
        assert isinstance(result, dict)
        assert set(result.keys()) == {
            "thread_id",
            "subject",
            "message_count",
            "messages",
            "stats",
        }
        assert result["messages"][1]["content"] == "Yes, that works."

    @pytest.mark.asyncio
    async def test_digest_with_analysis_carries_both(self):
        service = _build_mock_service(_quoted_thread())
        result = await _unwrap(get_gmail_thread_content)(
            service=service,
            thread_id="t1",
            user_google_email="erik@example.com",
            digest=True,
            include_analysis=True,
        )
        assert "analysis" in result
        assert isinstance(result["analysis"], dict)
        assert result["message_count"] == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body_format", ["html", "raw"])
    async def test_digest_rejects_non_text_body_format(self, body_format):
        """Silently ignoring a documented parameter is the same family of fault
        as silently dropping content, so the combination is refused."""
        service = _build_mock_service(_quoted_thread())
        with pytest.raises(UserInputError) as excinfo:
            await _unwrap(get_gmail_thread_content)(
                service=service,
                thread_id="t1",
                user_google_email="erik@example.com",
                digest=True,
                body_format=body_format,
            )
        assert "body_format" in str(excinfo.value)
        assert body_format in str(excinfo.value)
        # and it must fail before spending an API call
        service.users.return_value.threads.return_value.get.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body_format", ["html", "raw"])
    async def test_batch_digest_rejects_non_text_body_format(self, body_format):
        service = MagicMock()
        with pytest.raises(UserInputError):
            await _unwrap(get_gmail_threads_content_batch)(
                service=service,
                thread_ids=["t1"],
                user_google_email="erik@example.com",
                digest=True,
                body_format=body_format,
            )
        service.new_batch_http_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_html_body_format_without_digest_is_untouched(self):
        """The rejection must not leak into the existing formatted path."""
        service = _build_mock_service(_quoted_thread())
        result = await _unwrap(get_gmail_thread_content)(
            service=service,
            thread_id="t1",
            user_google_email="erik@example.com",
            body_format="html",
        )
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_batch_digest_returns_threads_and_stats(self):
        thread = _quoted_thread()
        service = MagicMock()
        service.new_batch_http_request.side_effect = RuntimeError("no batch in test")
        service.users.return_value.threads.return_value.get.return_value.execute.return_value = thread

        result = await _unwrap(get_gmail_threads_content_batch)(
            service=service,
            thread_ids=["t1"],
            user_google_email="erik@example.com",
            digest=True,
        )
        assert isinstance(result, dict)
        assert result["requested"] == 1
        assert result["thread_count"] == 1
        assert result["errors"] == []
        assert result["threads"][0]["messages"][2]["content"] == "Booked for Tuesday."
        assert result["stats"]["quoted_chars_removed"] > 0

    @pytest.mark.asyncio
    async def test_batch_default_still_returns_string(self):
        thread = _quoted_thread()
        service = MagicMock()
        service.new_batch_http_request.side_effect = RuntimeError("no batch in test")
        service.users.return_value.threads.return_value.get.return_value.execute.return_value = thread

        result = await _unwrap(get_gmail_threads_content_batch)(
            service=service,
            thread_ids=["t1"],
            user_google_email="erik@example.com",
        )
        assert isinstance(result, str)
        assert "Retrieved 1 threads:" in result
