"""Full/incremental sync through the existing list_contacts tool."""

import json
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from core.utils import UserInputError
from gcontacts.contacts_helpers import DEFAULT_PERSON_FIELDS, _format_contact
from gcontacts.contacts_tools import list_contacts

EXPIRED = "Sync token expired. Start a new full sync with request_sync_token=true."


def _unwrap(function):
    """Get the tool implementation without authentication/error decorators."""
    while hasattr(function, "__wrapped__"):
        function = function.__wrapped__
    return function


async def _call(service, **kwargs):
    """Call the actual listing implementation with a synthetic account."""
    return await _unwrap(list_contacts)(
        service=service, user_google_email="user@example.com", **kwargs
    )


def _service(response=None, error=None):
    """Build a People API service double for one listing response or error."""
    service = MagicMock()
    execute = (
        service.people.return_value.connections.return_value.list.return_value.execute
    )
    execute.return_value = response or {}
    execute.side_effect = error
    return service


def _request(service):
    """Return the effective arguments sent to people.connections.list."""
    return service.people.return_value.connections.return_value.list.call_args.kwargs


def _http_error(status, details=None, message="test error"):
    """Construct a real HttpError so Google's structured-details parsing runs."""
    return HttpError(
        Response({"status": str(status)}),
        json.dumps(
            {"error": {"code": status, "message": message, "details": details or []}}
        ).encode(),
    )


@pytest.mark.asyncio
async def test_default_listing_preserves_text_and_request_parameters():
    """Existing callers retain the same rendered listing, sorting and pagination."""
    contact = {
        "resourceName": "people/c1",
        "names": [{"displayName": "Example Person"}],
        "emailAddresses": [{"value": "person@example.com", "type": "work"}],
        "phoneNumbers": [{"value": "+12025550123", "type": "mobile"}],
    }
    service = _service(
        {"connections": [contact], "totalPeople": 4, "nextPageToken": "page-2"}
    )
    result = await _call(
        service, page_token="page-1", sort_order="FIRST_NAME_ASCENDING"
    )
    assert result == (
        "Contacts for user@example.com (1 of 4):\n\n"
        + _format_contact(contact)
        + "\n\nNext page token: page-2"
    )
    assert _request(service) == {
        "resourceName": "people/me",
        "personFields": DEFAULT_PERSON_FIELDS,
        "pageSize": 100,
        "pageToken": "page-1",
        "sortOrder": "FIRST_NAME_ASCENDING",
    }


@pytest.mark.asyncio
async def test_empty_default_listing_preserves_text():
    """An ordinary empty listing still returns the existing text message."""
    assert await _call(_service()) == "No contacts found for user@example.com."


@pytest.mark.asyncio
async def test_full_sync_returns_sync_token_on_last_page():
    """A full sync requests metadata and ends the last page with the sync token."""
    contact = {
        "resourceName": "people/c1",
        "names": [{"displayName": "Example Person"}],
        "metadata": {"sources": [{"type": "CONTACT", "id": "c1"}]},
    }
    service = _service({"connections": [contact], "nextSyncToken": "sync-1"})
    result = await _call(service, request_sync_token=True, page_size=250)
    assert result == (
        "Contacts for user@example.com (1 of 1):\n\n"
        + _format_contact(contact)
        + "\n\nNext sync token: sync-1"
    )
    assert _request(service) == {
        "resourceName": "people/me",
        "personFields": DEFAULT_PERSON_FIELDS + ",metadata",
        "pageSize": 250,
        "requestSyncToken": True,
    }


@pytest.mark.asyncio
async def test_full_sync_pagination_keeps_parameters_and_empty_page_cursor():
    """An empty intermediate page retains its cursor and consistent parameters."""
    service = _service({"nextPageToken": "page-2"})
    first = await _call(service, request_sync_token=True, page_size=250)
    first_params = _request(service).copy()
    service.people.return_value.connections.return_value.list.return_value.execute.return_value = {
        "nextSyncToken": "sync-1"
    }
    final = await _call(
        service, request_sync_token=True, page_size=250, page_token="page-2"
    )
    assert (
        first == "No contacts returned for user@example.com.\n\nNext page token: page-2"
    )
    assert _request(service) == {**first_params, "pageToken": "page-2"}
    assert (
        final == "No contacts returned for user@example.com.\n\nNext sync token: sync-1"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("request_sync_token", [False, True])
async def test_incremental_sync_marks_tombstones_and_requests_new_cursor(
    request_sync_token,
):
    """A sync token marks deleted contacts and returns a fresh cursor."""
    deleted = {"resourceName": "people/c1", "metadata": {"deleted": True}}
    service = _service({"connections": [deleted], "nextSyncToken": "sync-new"})
    result = await _call(
        service, sync_token="sync-old", request_sync_token=request_sync_token
    )
    assert "Contact ID: c1\nDeleted: yes" in result
    assert result.endswith("Next sync token: sync-new")
    assert _request(service) == {
        "resourceName": "people/me",
        "personFields": DEFAULT_PERSON_FIELDS + ",metadata",
        "pageSize": 100,
        "syncToken": "sync-old",
        "requestSyncToken": True,
    }


@pytest.mark.asyncio
async def test_incremental_pagination_keeps_original_sync_token():
    """Incremental continuation requests reuse the initial token and page size."""
    service = _service({"nextPageToken": "next"})
    await _call(service, sync_token="original", page_size=250)
    original_params = _request(service).copy()
    await _call(service, sync_token="original", page_size=250, page_token="next")
    assert _request(service) == {**original_params, "pageToken": "next"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,details",
    [
        (410, None),
        (
            400,
            [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "EXPIRED_SYNC_TOKEN",
                    "domain": "people.googleapis.com",
                }
            ],
        ),
    ],
)
async def test_expired_sync_token_requires_explicit_full_sync(status, details):
    """Structured HTTP 400 expiry and legacy HTTP 410 both signal a reset."""
    service = _service(error=_http_error(status, details))
    assert await _call(service, sync_token="expired", page_size=250) == EXPIRED
    service.people.return_value.connections.return_value.list.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,sync_token",
    [(400, "sync"), (403, "sync"), (429, "sync"), (500, "sync"), (410, None)],
)
async def test_other_errors_are_not_reported_as_expired_sync(status, sync_token):
    """Errors unrelated to an expired incremental cursor propagate unchanged."""
    service = _service(error=_http_error(status))
    with pytest.raises(HttpError):
        await _call(service, request_sync_token=True, sync_token=sync_token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "details",
    [
        [],
        [{"reason": "INVALID_ARGUMENT"}],
        ["EXPIRED_SYNC_TOKEN"],
        "EXPIRED_SYNC_TOKEN",
    ],
)
async def test_expiry_text_without_structured_reason_does_not_reset(details):
    """Free text or malformed details cannot turn another HTTP 400 into a reset."""
    error = _http_error(400, details, message="Not an EXPIRED_SYNC_TOKEN error")
    with pytest.raises(HttpError) as caught:
        await _call(_service(error=error), sync_token="sync")
    assert caught.value is error


@pytest.mark.asyncio
async def test_expiry_reason_without_incremental_token_does_not_reset():
    """A full sync cannot silently swallow a reason-coded error as token expiry."""
    error = _http_error(400, [{"reason": "EXPIRED_SYNC_TOKEN"}])
    with pytest.raises(HttpError) as caught:
        await _call(_service(error=error), request_sync_token=True)
    assert caught.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize("page_size", [0, -1])
async def test_invalid_page_size_does_not_call_google(page_size):
    """Reject invalid page sizes before making an external request."""
    service = _service()
    with pytest.raises(UserInputError, match="page_size"):
        await _call(service, request_sync_token=True, page_size=page_size)
    service.people.assert_not_called()


@pytest.mark.asyncio
async def test_oversized_page_size_is_capped():
    """Page sizes above the People API maximum are capped."""
    service = _service()
    await _call(service, request_sync_token=True, page_size=1001)
    assert _request(service)["pageSize"] == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sync_options", [{"request_sync_token": True}, {"sync_token": "sync"}]
)
async def test_sync_rejects_sort_order_before_calling_google(sync_options):
    """Reject unsupported sorting in either full or incremental sync mode."""
    service = _service()
    with pytest.raises(UserInputError, match="sort_order"):
        await _call(service, sort_order="FIRST_NAME_ASCENDING", **sync_options)
    service.people.assert_not_called()


@pytest.mark.asyncio
async def test_sync_ignores_empty_sort_order():
    """An empty sort_order is a no-op in sync mode, as it is for plain listings."""
    service = _service({"nextSyncToken": "sync-1"})
    await _call(service, request_sync_token=True, sort_order="")
    assert "sortOrder" not in _request(service)


@pytest.mark.asyncio
@pytest.mark.parametrize("sync_token", ["", "   "])
async def test_empty_sync_token_does_not_fall_back_to_full_listing(sync_token):
    """Reject an empty cursor instead of silently fetching an ordinary listing."""
    service = _service()
    with pytest.raises(UserInputError, match="sync_token"):
        await _call(service, sync_token=sync_token)
    service.people.assert_not_called()
