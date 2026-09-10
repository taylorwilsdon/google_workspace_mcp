"""
Unit tests for the generic Google api_call tool.

Tests input validation, query/body preparation, response formatting, and the
end-to-end call against a mocked authorized transport.
"""

import json
import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import auth.scopes as scopes
from core.utils import UserInputError
from gapi.api_tools import (
    _api_call_impl,
    _format_response,
    _prepare_request,
    _redact_url,
)


def _mock_service(status=200, content=b'{"ok": true}'):
    """Build a fake service whose ._http.request returns (response, content)."""
    service = Mock()
    response = Mock()
    response.status = status
    service._http.request.return_value = (response, content)
    return service


# --- _prepare_request validation -------------------------------------------------


def test_rejects_unknown_method():
    with pytest.raises(UserInputError):
        _prepare_request("FETCH", "https://gmail.googleapis.com/x", None, None)


def test_rejects_non_https():
    with pytest.raises(UserInputError):
        _prepare_request("GET", "http://gmail.googleapis.com/x", None, None)


def test_rejects_non_google_host():
    with pytest.raises(UserInputError):
        _prepare_request("GET", "https://evil.example.com/x", None, None)


def test_accepts_googleapis_host():
    method, url, body, headers = _prepare_request(
        "get", "https://gmail.googleapis.com/gmail/v1/users/me/labels", None, None
    )
    assert method == "GET"  # upper-cased
    assert url.endswith("/labels")
    assert body is None


def test_merges_query_into_existing_querystring():
    _, url, _, _ = _prepare_request(
        "GET",
        "https://www.googleapis.com/drive/v3/files?corpora=user",
        {"q": "name = 'x'", "pageSize": 10},
        None,
    )
    assert "corpora=user" in url
    assert "pageSize=10" in url
    assert url.count("?") == 1


def test_body_serialized_as_json():
    _, _, body, headers = _prepare_request(
        "POST", "https://gmail.googleapis.com/x", None, {"a": 1}
    )
    assert json.loads(body) == {"a": 1}
    assert headers["Content-Type"] == "application/json"


def test_string_body_passed_through():
    _, _, body, _ = _prepare_request(
        "POST", "https://gmail.googleapis.com/x", None, "raw-string"
    )
    assert body == "raw-string"


# --- read-only mode --------------------------------------------------------------


@pytest.fixture
def read_only_mode():
    """Enable read-only mode for the duration of a test, then restore."""
    previous = scopes.is_read_only_mode()
    scopes.set_read_only(True)
    try:
        yield
    finally:
        scopes.set_read_only(previous)


def test_read_only_blocks_mutating_methods(read_only_mode):
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(UserInputError):
            _prepare_request(method, "https://gmail.googleapis.com/x", None, None)


def test_read_only_allows_get(read_only_mode):
    method, _, _, _ = _prepare_request(
        "GET", "https://gmail.googleapis.com/x", None, None
    )
    assert method == "GET"


# --- URL redaction ---------------------------------------------------------------


def test_redact_url_strips_query():
    redacted = _redact_url("https://gmail.googleapis.com/v1/x?access_token=secret&q=1")
    assert redacted == "https://gmail.googleapis.com/v1/x"
    assert "secret" not in redacted


# --- _format_response -------------------------------------------------------------


def test_format_pretty_prints_json():
    out = _format_response(200, b'{"a":1}')
    assert out.startswith("HTTP 200")
    assert '"a": 1' in out  # reformatted with indent


def test_format_falls_back_to_raw_text():
    out = _format_response(204, b"not json")
    assert out == "HTTP 204\nnot json"


def test_format_truncates_large_bodies():
    big = b'"' + b"x" * 200_000 + b'"'
    out = _format_response(200, big)
    assert "truncated" in out


# --- _api_call_impl end-to-end ----------------------------------------------------


@pytest.mark.asyncio
async def test_api_call_invokes_authorized_transport():
    service = _mock_service(status=200, content=b'{"labels": []}')
    result = await _api_call_impl(
        service,
        "user@example.com",
        "GET",
        "https://gmail.googleapis.com/gmail/v1/users/me/labels",
    )
    assert result.startswith("HTTP 200")
    # The request went through the borrowed authorized transport.
    service._http.request.assert_called_once()
    called_url = service._http.request.call_args[0][0]
    assert called_url.endswith("/labels")


@pytest.mark.asyncio
async def test_api_call_sends_body_and_method():
    service = _mock_service(status=200, content=b"{}")
    await _api_call_impl(
        service,
        "user@example.com",
        "POST",
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        body={"raw": "abc"},
    )
    kwargs = service._http.request.call_args.kwargs
    assert kwargs["method"] == "POST"
    assert json.loads(kwargs["body"]) == {"raw": "abc"}
