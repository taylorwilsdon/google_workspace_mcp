"""search_contacts source param: saved contacts (default) and other contacts."""

from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from auth.permissions import get_scopes_for_permission
from auth.scopes import (
    CONTACTS_OTHER_READONLY_SCOPE,
    TOOL_READONLY_SCOPES_MAP,
    TOOL_SCOPES_MAP,
)
from core.utils import UserInputError
from gcontacts import contacts_tools
from gcontacts.contacts_tools import search_contacts

OTHER_PERSON = {
    "resourceName": "otherContacts/c1",
    "names": [{"displayName": "Dana Reyes"}],
    "emailAddresses": [{"value": "dana@example.com"}],
}


def _unwrap(function: Callable[..., Any]) -> Callable[..., Any]:
    """Get the tool implementation without authentication/error decorators."""
    while hasattr(function, "__wrapped__"):
        function = function.__wrapped__
    return function


@pytest.fixture(autouse=True)
def _cold_search_cache() -> Iterator[None]:
    contacts_tools._search_cache_warmed_up.clear()
    yield
    contacts_tools._search_cache_warmed_up.clear()


async def _search(service: MagicMock, **kwargs: Any) -> str:
    return await _unwrap(search_contacts)(
        service=service, user_google_email="user@example.com", query="dana", **kwargs
    )


@pytest.mark.asyncio
async def test_saved_contacts_stay_the_default() -> None:
    service = MagicMock()
    service.people.return_value.searchContacts.return_value.execute.return_value = {}

    await _search(service)

    service.people.return_value.searchContacts.assert_called()
    service.otherContacts.assert_not_called()


@pytest.mark.asyncio
async def test_other_searches_other_contacts() -> None:
    service = MagicMock()
    search = service.otherContacts.return_value.search
    search.return_value.execute.return_value = {"results": [{"person": OTHER_PERSON}]}

    result = await _search(service, source="other")

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
async def test_saved_warmup_does_not_skip_other_warmup() -> None:
    service = MagicMock()
    service.people.return_value.searchContacts.return_value.execute.return_value = {}
    search = service.otherContacts.return_value.search
    search.return_value.execute.return_value = {}

    await _search(service)
    await _search(service, source="other")

    assert [c.kwargs["query"] for c in search.call_args_list] == ["", "dana"]


@pytest.mark.asyncio
async def test_other_with_no_matches_names_other_contacts() -> None:
    service = MagicMock()
    service.otherContacts.return_value.search.return_value.execute.return_value = {}

    result = await _search(service, source="other")

    assert result == "No other contacts found matching 'dana' for user@example.com."


@pytest.mark.asyncio
async def test_other_source_is_refused_in_service_account_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contacts_tools, "is_service_account_enabled", lambda: True)
    service = MagicMock()
    service.people.return_value.searchContacts.return_value.execute.return_value = {}

    with pytest.raises(UserInputError, match="service-account"):
        await _search(service, source="other")
    service.otherContacts.assert_not_called()

    await _search(service, source="saved")
    assert service.people.return_value.searchContacts.call_args.kwargs["query"] == (
        "dana"
    )


def test_contacts_service_requests_the_other_contacts_scope() -> None:
    assert CONTACTS_OTHER_READONLY_SCOPE in TOOL_SCOPES_MAP["contacts"]
    assert CONTACTS_OTHER_READONLY_SCOPE in TOOL_READONLY_SCOPES_MAP["contacts"]
    assert CONTACTS_OTHER_READONLY_SCOPE in get_scopes_for_permission(
        "contacts", "readonly"
    )
