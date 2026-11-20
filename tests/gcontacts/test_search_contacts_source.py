"""search_contacts source param: saved contacts (default) and other contacts."""

from unittest.mock import MagicMock, call

import pytest

from auth.permissions import get_scopes_for_permission
from auth.scopes import CONTACTS_OTHER_READONLY_SCOPE, TOOL_SCOPES_MAP
from gcontacts import contacts_tools
from gcontacts.contacts_tools import search_contacts

OTHER_PERSON = {
    "resourceName": "otherContacts/c1",
    "names": [{"displayName": "Dana Reyes"}],
    "emailAddresses": [{"value": "dana@example.com"}],
}


def _unwrap(function):
    """Get the tool implementation without authentication/error decorators."""
    while hasattr(function, "__wrapped__"):
        function = function.__wrapped__
    return function


@pytest.fixture(autouse=True)
def _cold_search_cache():
    contacts_tools._search_cache_warmed_up.clear()
    yield
    contacts_tools._search_cache_warmed_up.clear()


async def _search(service, **kwargs):
    return await _unwrap(search_contacts)(
        service=service, user_google_email="user@example.com", query="dana", **kwargs
    )


@pytest.mark.asyncio
async def test_saved_contacts_stay_the_default():
    service = MagicMock()
    service.people.return_value.searchContacts.return_value.execute.return_value = {}

    await _search(service)

    service.people.return_value.searchContacts.assert_called()
    service.otherContacts.assert_not_called()


@pytest.mark.asyncio
async def test_other_searches_other_contacts():
    service = MagicMock()
    search = service.otherContacts.return_value.search
    search.return_value.execute.return_value = {"results": [{"person": OTHER_PERSON}]}

    result = await _search(service, source="other")

    # Warm-up with an empty query first, as the People API asks, then the search.
    assert search.call_args_list[0].kwargs["query"] == ""
    assert search.call_args_list[-1] == call(
        query="dana",
        readMask="names,emailAddresses,phoneNumbers",
        pageSize=30,
    )
    service.people.return_value.searchContacts.assert_not_called()
    assert "Dana Reyes" in result
    assert "dana@example.com" in result


@pytest.mark.asyncio
async def test_other_with_no_matches_names_other_contacts():
    service = MagicMock()
    service.otherContacts.return_value.search.return_value.execute.return_value = {}

    result = await _search(service, source="other")

    assert result == "No other contacts found matching 'dana' for user@example.com."


def test_contacts_service_requests_the_other_contacts_scope():
    assert CONTACTS_OTHER_READONLY_SCOPE in TOOL_SCOPES_MAP["contacts"]
    assert CONTACTS_OTHER_READONLY_SCOPE in get_scopes_for_permission(
        "contacts", "readonly"
    )
