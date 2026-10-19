"""Emails wrapped inside another email (message/rfc822 parts).

Covers rendering of wrapped messages and attribution of files found inside them.
All fixtures are synthetic and use the Gmail API `format=full` payload shape.
"""

import base64
import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gmail.gmail_tools import (  # noqa: E402
    ATTACHED_MESSAGE_HEADER_LIMIT,
    ATTACHED_MESSAGE_MAX_COUNT,
    ATTACHED_MESSAGE_MAX_DEPTH,
    _extract_attachments,
    _extract_message_bodies,
    _format_thread_content,
    _render_attached_messages,
    get_gmail_message_content,
    get_gmail_messages_content_batch,
)


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _text(text, mime="text/plain"):
    return {"mimeType": mime, "filename": "", "body": {"data": _b64(text)}}


def _hdrs(**headers):
    return [{"name": k.replace("_", "-"), "value": v} for k, v in headers.items()]


def _rfc822(inner):
    """A message/rfc822 part as Gmail exposes it: the wrapped message's own MIME
    tree, carrying the wrapped message's headers, is the part's single child."""
    return {
        "mimeType": "message/rfc822",
        "filename": "",
        "body": {"size": 0},
        "parts": [inner],
    }


def _wrapped(subject="Wrapped subject", body="Wrapped plain body", extra_parts=None):
    parts = [_text(body), _text(f"<p>{body}</p>", "text/html")]
    inner = {
        "mimeType": "multipart/alternative",
        "headers": _hdrs(
            From="Original Sender <original@example.com>",
            To="list@example.org",
            Subject=subject,
            Date="Tue, 01 Sep 2026 09:00:00 +0000",
        ),
        "parts": parts,
    }
    if extra_parts:
        inner = {
            "mimeType": "multipart/mixed",
            "headers": inner["headers"],
            "parts": [
                {"mimeType": "multipart/alternative", "parts": parts},
                *extra_parts,
            ],
        }
    return inner


def _wrapper(*parts):
    return {
        "mimeType": "multipart/mixed",
        "headers": _hdrs(From="notices@example.org", Subject="Message pending"),
        "parts": [_text("Wrapper body: a message is awaiting moderation."), *parts],
    }


def _file(name, attachment_id):
    return {
        "mimeType": "image/png",
        "filename": name,
        "body": {"attachmentId": attachment_id, "size": 2048},
    }


def _deep_multipart(part):
    for _ in range(sys.getrecursionlimit() + 50):
        part = {"mimeType": "multipart/mixed", "parts": [part]}
    return part


MARKER_1 = (
    "--- ATTACHED MESSAGE 1 (headers as claimed by the attachment, unverified) ---"
)


class TestRenderAttachedMessages:
    def test_wrapped_message_headers_and_body_are_rendered(self):
        out = _render_attached_messages(_wrapper(_rfc822(_wrapped())))

        assert MARKER_1 in out
        assert "From: Original Sender <original@example.com>" in out
        assert "Subject: Wrapped subject" in out
        assert "Date: Tue, 01 Sep 2026 09:00:00 +0000" in out
        assert "Wrapped plain body" in out

    def test_the_wrapper_body_is_still_only_the_wrapper(self):
        """The shared body walker is untouched: reply quoting and forwarding use it,
        and must not start pulling a wrapped email into a quote."""
        bodies = _extract_message_bodies(_wrapper(_rfc822(_wrapped())))
        assert bodies["text"] == "Wrapper body: a message is awaiting moderation."

    def test_an_ordinary_message_renders_nothing(self):
        ordinary = {
            "mimeType": "multipart/alternative",
            "parts": [_text("hi"), _text("<p>hi</p>", "text/html")],
        }
        assert _render_attached_messages(ordinary) == ""
        assert _render_attached_messages(_text("single part")) == ""
        assert _render_attached_messages({}) == ""

    def test_html_only_wrapped_message_is_converted_in_text_mode(self):
        inner = {
            "mimeType": "text/html",
            "headers": _hdrs(Subject="HTML only"),
            "body": {"data": _b64("<p>Only <b>HTML</b> here</p>")},
        }
        out = _render_attached_messages(_wrapper(_rfc822(inner)))
        assert "Subject: HTML only" in out
        assert "Only" in out and "<p>" not in out

    def test_body_format_is_forwarded_to_the_wrapped_body(self):
        payload = _wrapper(_rfc822(_wrapped(body="Formatted")))
        assert "<p>Formatted</p>" in _render_attached_messages(payload, "html")
        assert "<p>Formatted</p>" not in _render_attached_messages(payload, "text")

    def test_headers_fall_back_to_the_rfc822_part(self):
        """Safety net if the wrapped message's headers are not on its root part."""
        inner = _wrapped()
        part = _rfc822({k: v for k, v in inner.items() if k != "headers"})
        part["headers"] = _hdrs(From="fallback@example.com", Subject="From the part")
        out = _render_attached_messages(_wrapper(part))
        assert "From: fallback@example.com" in out
        assert "Subject: From the part" in out
        assert "Wrapped plain body" in out

    def test_a_bounce_that_wraps_the_original_renders_it(self):
        """message/rfc822 inside multipart/report goes through the generic path.
        Other parts of a report (delivery-status, returned headers) are not rendered."""
        report = {
            "mimeType": "multipart/report",
            "headers": _hdrs(Subject="Delivery Status Notification (Failure)"),
            "parts": [
                _text("Your message could not be delivered."),
                _text("Action: failed\r\nStatus: 5.1.1\r\n", "message/delivery-status"),
                _rfc822(_wrapped(subject="The message that bounced")),
            ],
        }
        out = _render_attached_messages(report)
        assert MARKER_1 in out
        assert "Subject: The message that bounced" in out
        assert "delivery-status" not in out and "5.1.1" not in out

    def test_deep_mime_containers_preserve_render_order(self):
        inner = _wrapped(
            subject="first",
            extra_parts=[_deep_multipart(_rfc822(_wrapped(subject="nested")))],
        )
        payload = _wrapper(
            _deep_multipart(_rfc822(inner)), _rfc822(_wrapped(subject="last"))
        )

        out = _render_attached_messages(payload)

        assert out.count("--- ATTACHED MESSAGE ") == 3
        assert out.index("Subject: first") < out.index("Subject: nested")
        assert out.index("Subject: nested") < out.index("Subject: last")
        assert "Wrapped plain body" in out
        assert "not shown" not in out

    @pytest.mark.parametrize("deep_mime", [False, True])
    def test_nesting_depth_boundary(self, deep_mime):
        """Levels 1..MAX are rendered; the next level is announced, not shown."""
        payload = None
        for level in range(ATTACHED_MESSAGE_MAX_DEPTH + 2, 0, -1):
            extra = [_rfc822(payload)] if payload else None
            payload = _wrapped(subject=f"level {level}", extra_parts=extra)
            if deep_mime:
                headers = payload["headers"]
                payload = _deep_multipart(payload)
                payload["headers"] = headers
        out = _render_attached_messages(_wrapper(_rfc822(payload)))

        assert f"Subject: level {ATTACHED_MESSAGE_MAX_DEPTH}" in out
        assert f"Subject: level {ATTACHED_MESSAGE_MAX_DEPTH + 1}" not in out
        assert (
            f"--- 1 attached message(s) nested more than "
            f"{ATTACHED_MESSAGE_MAX_DEPTH} deep not shown ---"
        ) in out

    def test_a_digest_over_the_limit_says_how_many_were_left_out(self):
        """A digest routinely carries 10-30 wrapped messages; showing five silently
        would have a caller report that the digest has five."""
        extra = 4
        digest = _wrapper(
            *[
                _rfc822(_wrapped(subject=f"msg {i}"))
                for i in range(ATTACHED_MESSAGE_MAX_COUNT + extra)
            ]
        )
        out = _render_attached_messages(digest)

        assert out.count("--- ATTACHED MESSAGE ") == ATTACHED_MESSAGE_MAX_COUNT
        assert (
            f"--- {extra} more attached message(s) not shown "
            f"(limit {ATTACHED_MESSAGE_MAX_COUNT}) ---"
        ) in out

    def test_a_huge_wrapped_body_is_truncated(self):
        out = _render_attached_messages(_wrapper(_rfc822(_wrapped(body="x" * 60_000))))
        assert "[Content truncated...]" in out
        assert len(out) < 25_000

    def test_a_huge_wrapped_header_is_truncated(self):
        inner = _wrapped()
        inner["headers"] = _hdrs(
            Subject="s" * 500_000,
            To=", ".join(f"r{i}@example.com" for i in range(20_000)),
        )
        out = _render_attached_messages(_wrapper(_rfc822(inner)))
        assert "[truncated]" in out
        assert len(out) < 3 * ATTACHED_MESSAGE_HEADER_LIMIT + 2_000


class TestAttachmentAttribution:
    def test_deep_mime_containers_preserve_metadata_order_and_attribution(self):
        attached = _rfc822(
            _deep_multipart(
                _wrapper(
                    _file("inner.png", "att-inner"),
                    _rfc822(_deep_multipart(_file("nested.png", "att-nested"))),
                )
            )
        )
        attached.update(filename="message.eml", body={"attachmentId": "att-eml"})
        payload = _deep_multipart(
            _wrapper(
                _file("first.png", "att-first"),
                attached,
                _file("last.png", "att-last"),
            )
        )

        attachments = _extract_attachments(payload)

        assert attachments == [
            {
                "filename": filename,
                "mimeType": mime_type,
                "size": size,
                "attachmentId": attachment_id,
                "inAttachedMessage": nested,
            }
            for filename, mime_type, size, attachment_id, nested in [
                ("first.png", "image/png", 2048, "att-first", False),
                ("message.eml", "message/rfc822", 0, "att-eml", False),
                ("inner.png", "image/png", 2048, "att-inner", True),
                ("nested.png", "image/png", 2048, "att-nested", True),
                ("last.png", "image/png", 2048, "att-last", False),
            ]
        ]

    def test_files_inside_a_wrapped_message_are_flagged_not_renamed(self):
        payload = _wrapper(
            _file("wrapper.png", "att-outer"),
            _rfc822(_wrapped(extra_parts=[_file("inner.png", "att-inner")])),
        )
        by_name = {a["filename"]: a for a in _extract_attachments(payload)}

        # Filenames stay exact: forwarding and downloading both match on them.
        assert set(by_name) == {"wrapper.png", "inner.png"}
        assert by_name["wrapper.png"]["inAttachedMessage"] is False
        assert by_name["inner.png"]["inAttachedMessage"] is True


def _nested_payload():
    return _wrapper(_rfc822(_wrapped(extra_parts=[_file("inner.png", "att-inner")])))


def _assert_wrapped_message_shown(result):
    assert "Wrapper body: a message is awaiting moderation." in result
    assert MARKER_1 in result
    assert "Subject: Wrapped subject" in result
    assert "inner.png (image/png, 2.0 KB) [in attached message]" in result
    # the wrapped content sits with the body, above the attachment list
    assert result.index(MARKER_1) < result.index("--- ATTACHMENTS ---")


def _service_returning(payload):
    service = Mock()

    def message_get(**kwargs):
        request = Mock()
        request.execute.return_value = {"id": kwargs["id"], "payload": payload}
        return request

    service.users().messages().get.side_effect = message_get
    return service


@pytest.mark.asyncio
async def test_get_gmail_message_content_shows_the_wrapped_message():
    result = await _unwrap(get_gmail_message_content)(
        service=_service_returning(_nested_payload()),
        message_id="m-1",
        user_google_email="user@example.com",
    )
    _assert_wrapped_message_shown(result)


@pytest.mark.asyncio
async def test_get_gmail_messages_content_batch_shows_the_wrapped_message():
    service = _service_returning(_nested_payload())
    # Force the sequential fallback: the batch path and it share one renderer.
    service.new_batch_http_request.side_effect = RuntimeError("no batch")

    result = await _unwrap(get_gmail_messages_content_batch)(
        service=service, message_ids=["m-1"], user_google_email="user@example.com"
    )
    _assert_wrapped_message_shown(result)


def test_get_gmail_thread_content_formatter_shows_the_wrapped_message():
    thread = {"messages": [{"id": "m-1", "payload": _nested_payload()}]}
    _assert_wrapped_message_shown(_format_thread_content(thread, "t-1"))
