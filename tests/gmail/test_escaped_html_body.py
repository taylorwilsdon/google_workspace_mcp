"""Tests for rejecting an entity-escaped HTML body before send or draft."""

import os
import sys
import time
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gmail.gmail_helpers import _reject_entity_escaped_html_body
from gmail.gmail_tools import draft_gmail_message, send_gmail_message


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


ESCAPED_BODY = (
    '&lt;div dir="rtl" style="text-align:right"&gt;\n'
    "&lt;p&gt;עידית שלום,&lt;/p&gt;\n"
    "&lt;p&gt;תודה על הפנייה.&lt;/p&gt;\n"
    "&lt;/div&gt;"
)


NUMERIC_ESCAPED_BODY = (
    '&#60;div dir="rtl"&#62;&#60;p&#62;שלום&#60;/p&#62;&#60;/div&#62;'
)
HEX_ESCAPED_BODY = "&#x3C;p&#x3e;Hello&#x3c;/p&#X3E;"


@pytest.mark.parametrize(
    "body",
    [
        ESCAPED_BODY,
        NUMERIC_ESCAPED_BODY,
        HEX_ESCAPED_BODY,
        "&LT;p&GT;Hi&LT;/p&GT;",
        # Only '<' escaped: a literal '>' still terminates the opening tag.
        "&lt;p>Hello&lt;/p>",
        # Attributes between the tag name and the escaped '>'.
        '&lt;a href="https://example.com/?a=1&amp;b=2"&gt;link&lt;/a&gt;',
        "&lt;br/&gt;Line",
    ],
)
def test_rejects_fully_escaped_html_body(body):
    with pytest.raises(UserInputError) as excinfo:
        _reject_entity_escaped_html_body(body, "html")
    assert "entity-escaped" in str(excinfo.value)
    assert "'<p>...</p>'" in str(excinfo.value)


@pytest.mark.parametrize(
    "body,body_format",
    [
        # Real markup: the normal html case.
        ('<div dir="rtl"><p>שלום</p></div>', "html"),
        # Escaped tags as sample text inside real markup.
        ('<p>Wrap it in &lt;div dir="rtl"&gt; like this.</p>', "html"),
        # Escaped tag mentioned mid-body rather than opening it.
        ("<p>Use &lt;br&gt; for a line break.</p>", "html"),
        ("<p>Use &#60;br&#62; for a line break.</p>", "html"),
        # Opens with an unrelated numeric reference, not an escaped '<'.
        ("&#169; 2025 Example", "html"),
        # An escaped '<' with no closing '>' or '&gt;' is prose.
        ("&lt;hello world", "html"),
        ("&lt;b and c are both small", "html"),
        # Plain bodies are never checked.
        (ESCAPED_BODY, "plain"),
        # Plain-text body with no markup at all.
        ("Hi Idit,\n\nThanks for the note.", "html"),
        ("", "html"),
        (None, "html"),
    ],
)
def test_allows_legitimate_bodies(body, body_format):
    _reject_entity_escaped_html_body(body, body_format)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [ESCAPED_BODY, NUMERIC_ESCAPED_BODY])
async def test_send_gmail_message_rejects_escaped_body(body):
    mock_service = Mock()
    with pytest.raises(UserInputError):
        await _unwrap(send_gmail_message)(
            service=mock_service,
            user_google_email="user@example.com",
            to="recipient@example.com",
            subject="דוח 2025",
            body=body,
            body_format="html",
            include_signature=False,
        )
    # Rejected before any Gmail API call is made.
    mock_service.users.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [ESCAPED_BODY, NUMERIC_ESCAPED_BODY])
async def test_draft_gmail_message_rejects_escaped_body(body):
    mock_service = Mock()
    with pytest.raises(UserInputError):
        await _unwrap(draft_gmail_message)(
            service=mock_service,
            user_google_email="user@example.com",
            to="recipient@example.com",
            subject="דוח 2025",
            body=body,
            body_format="html",
            include_signature=False,
        )
    mock_service.users.assert_not_called()


@pytest.mark.parametrize(
    "body",
    ["&lt;a" + " " * 50_000, "&lt;a x" + " " * 50_000],
    ids=["tag-name-then-spaces", "attribute-then-spaces"],
)
def test_unclosed_escaped_tag_is_matched_in_linear_time(body):
    """A long unclosed escaped '<' must not backtrack quadratically."""
    start = time.perf_counter()
    _reject_entity_escaped_html_body(body, "html")
    assert time.perf_counter() - start < 0.5
