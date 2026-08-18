"""An entity-escaped HTML body must be rejected before anything is sent.

A caller that writes "&lt;div&gt;...&lt;/div&gt;" into an html body means the
markup; Gmail renders it as visible tags, so the recipient gets raw markup as
text. The guard fails the call instead, which costs a retry rather than an
unrecoverable send.
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gmail.gmail_tools import (
    _reject_entity_escaped_html_body,
    draft_gmail_message,
    send_gmail_message,
)


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


def test_rejects_fully_escaped_html_body():
    with pytest.raises(UserInputError) as excinfo:
        _reject_entity_escaped_html_body(ESCAPED_BODY, "html")
    assert "entity-escaped" in str(excinfo.value)


@pytest.mark.parametrize(
    "body,body_format",
    [
        # Real markup: the normal html case.
        ('<div dir="rtl"><p>שלום</p></div>', "html"),
        # Escaped tags shown as sample text inside a genuine html body: the
        # body also carries real markup, so it is left alone.
        ('<p>Wrap it in &lt;div dir="rtl"&gt; like this.</p>', "html"),
        # Escaped tag mentioned mid-body rather than opening it.
        ("<p>Use &lt;br&gt; for a line break.</p>", "html"),
        # Same escaped body, but the caller asked for plain: the entities are
        # then plausibly intentional and plain bodies are escaped downstream.
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
async def test_send_gmail_message_rejects_escaped_body():
    mock_service = Mock()
    with pytest.raises(UserInputError):
        await _unwrap(send_gmail_message)(
            service=mock_service,
            user_google_email="user@example.com",
            to="recipient@example.com",
            subject="דוח 2025",
            body=ESCAPED_BODY,
            body_format="html",
            include_signature=False,
        )
    # Rejected before any Gmail API call is made.
    mock_service.users.assert_not_called()


@pytest.mark.asyncio
async def test_draft_gmail_message_rejects_escaped_body():
    mock_service = Mock()
    with pytest.raises(UserInputError):
        await _unwrap(draft_gmail_message)(
            service=mock_service,
            user_google_email="user@example.com",
            to="recipient@example.com",
            subject="דוח 2025",
            body=ESCAPED_BODY,
            body_format="html",
            include_signature=False,
        )
    mock_service.users.assert_not_called()
