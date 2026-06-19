"""Integration tests for `get_gmail_thread_content`'s `include_analysis` flag.

Verifies that the flag toggles the return shape correctly:
- False (default) → returns a str (backward compatible, existing behavior)
- True → returns {"content": str, "analysis": dict}

The analysis dict is sourced from `_analyze_thread_ownership_impl`, which has
its own dedicated behavioral tests in test_thread_ownership_helpers.py.
These tests focus on the wire-up between the tool wrapper and the helper:
shape correctness, flag default, content parity across both modes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gmail.gmail_tools import get_gmail_thread_content


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function.
    Matches the pattern used in test_body_format.py."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _fake_thread_response() -> dict:
    """A minimal but realistic Gmail threads.get(format='full') response."""
    return {
        "id": "t1",
        "messages": [
            {
                "id": "m1",
                "labelIds": ["INBOX"],
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Alex <alex@alexreynolds.com>"},
                        {"name": "To", "value": "vendor@example.com"},
                        {"name": "Date", "value": "Mon, 14 Apr 2026 09:00:00 -0400"},
                        {"name": "Subject", "value": "Test thread"},
                    ],
                    "body": {"data": ""},
                    "mimeType": "text/plain",
                },
            },
            {
                "id": "m2",
                "labelIds": ["INBOX"],
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Vendor <vendor@example.com>"},
                        {"name": "To", "value": "alex@alexreynolds.com"},
                        {"name": "Date", "value": "Tue, 15 Apr 2026 10:00:00 -0400"},
                        {"name": "Subject", "value": "Re: Test thread"},
                    ],
                    "body": {"data": ""},
                    "mimeType": "text/plain",
                },
            },
        ],
    }


def _build_mock_service(thread_response: dict) -> MagicMock:
    """Build a MagicMock service that responds to .users().threads().get().execute()
    with the given thread_response. Matches the shape that
    `@require_google_service` injects."""
    service = MagicMock()
    service.users.return_value.threads.return_value.get.return_value.execute.return_value = thread_response
    return service


@pytest.mark.asyncio
async def test_default_returns_string_unchanged_behavior():
    """Without the flag, return type is str — preserves backward compatibility
    for every existing caller."""
    service = _build_mock_service(_fake_thread_response())

    # get_gmail_thread_content is decorated; call the underlying function by
    # extracting it from the FastMCP tool wrapper. The repo's pattern for
    # testing decorated tools: call the impl directly when possible, otherwise
    # mock the service and let the decorators pass through.
    result = await _unwrap(get_gmail_thread_content)(
        service=service,
        thread_id="t1",
        user_google_email="alex@alexreynolds.com",
    )

    assert isinstance(result, str)
    assert "Thread ID: t1" in result
    assert "alex@alexreynolds.com" in result or "Alex" in result


@pytest.mark.asyncio
async def test_include_analysis_true_returns_dict_with_both_keys():
    """With the flag, return shape is a dict carrying both content and analysis."""
    service = _build_mock_service(_fake_thread_response())

    result = await _unwrap(get_gmail_thread_content)(
        service=service,
        thread_id="t1",
        user_google_email="alex@alexreynolds.com",
        include_analysis=True,
    )

    assert isinstance(result, dict)
    assert set(result.keys()) == {"content", "analysis"}
    assert isinstance(result["content"], str)
    assert isinstance(result["analysis"], dict)


@pytest.mark.asyncio
async def test_analysis_keys_match_helper_contract():
    """The analysis dict matches _analyze_thread_ownership_impl's documented shape."""
    service = _build_mock_service(_fake_thread_response())

    result = await _unwrap(get_gmail_thread_content)(
        service=service,
        thread_id="t1",
        user_google_email="alex@alexreynolds.com",
        include_analysis=True,
    )

    expected_keys = {
        "thread_id",
        "thread_subject",
        "last_sender",
        "last_timestamp",
        "ball_in_court_of",
        "message_count_by_sender",
        "participants",
        "excluded_drafts",
        "message_count",
    }
    assert set(result["analysis"].keys()) == expected_keys


@pytest.mark.asyncio
async def test_analysis_ball_in_court_correct_for_vendor_last():
    """Semantic sanity check: vendor sent last → ball is on the user (us).
    The 'user' value means 'user needs to respond' — i.e., the ball is in
    the authenticated user's court because an external party was last to
    send."""
    service = _build_mock_service(_fake_thread_response())

    result = await _unwrap(get_gmail_thread_content)(
        service=service,
        thread_id="t1",
        user_google_email="alex@alexreynolds.com",
        include_analysis=True,
    )

    assert result["analysis"]["ball_in_court_of"] == "user"
    assert result["analysis"]["last_sender"] == "Vendor <vendor@example.com>"
    assert result["analysis"]["message_count"] == 2


@pytest.mark.asyncio
async def test_content_parity_across_flag_values():
    """The content string must be IDENTICAL whether the flag is on or off.
    The flag only adds the analysis key; it must not change content
    formatting."""
    thread = _fake_thread_response()
    service_a = _build_mock_service(thread)
    service_b = _build_mock_service(thread)

    str_result = await _unwrap(get_gmail_thread_content)(
        service=service_a,
        thread_id="t1",
        user_google_email="alex@alexreynolds.com",
    )
    dict_result = await _unwrap(get_gmail_thread_content)(
        service=service_b,
        thread_id="t1",
        user_google_email="alex@alexreynolds.com",
        include_analysis=True,
    )

    assert str_result == dict_result["content"]
