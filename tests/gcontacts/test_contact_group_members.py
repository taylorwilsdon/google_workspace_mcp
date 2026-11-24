"""manage_contact_group(action="modify_members") reports what the API did."""

from unittest.mock import MagicMock

import pytest

from gcontacts.contacts_tools import manage_contact_group


def _unwrap(function):
    """Get the tool implementation without authentication/error decorators."""
    while hasattr(function, "__wrapped__"):
        function = function.__wrapped__
    return function


async def _modify(api_result, **kwargs):
    service = MagicMock()
    modify = service.contactGroups.return_value.members.return_value.modify
    modify.return_value.execute.return_value = api_result
    return await _unwrap(manage_contact_group)(
        service=service,
        user_google_email="user@example.com",
        action="modify_members",
        group_id="g1",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_counts_leave_out_contacts_that_were_not_found():
    result = await _modify(
        {"notFoundResourceNames": ["people/c2"]},
        add_contact_ids=["c1", "c2"],
    )

    assert "Added: 1 contacts" in result
    assert "Not found: people/c2" in result


@pytest.mark.asyncio
async def test_counts_leave_out_contacts_that_cannot_leave_their_last_group():
    result = await _modify(
        {
            "notFoundResourceNames": ["people/c4"],
            "canNotRemoveLastContactGroupResourceNames": ["people/c3"],
        },
        remove_contact_ids=["c3", "c4", "c5"],
    )

    assert "Removed: 1 contacts" in result
    assert "Cannot remove (last group): people/c3" in result


@pytest.mark.asyncio
async def test_counts_match_the_request_when_everything_applied():
    result = await _modify({}, add_contact_ids=["c1"], remove_contact_ids=["c2"])

    assert "Added: 1 contacts" in result
    assert "Removed: 1 contacts" in result


@pytest.mark.asyncio
async def test_repeated_ids_count_once():
    result = await _modify(
        {}, add_contact_ids=["c1", "c1", "people/c1"], remove_contact_ids=["c2", "c2"]
    )

    assert "Added: 1 contacts" in result
    assert "Removed: 1 contacts" in result
