"""Tests for list_spaces listing the members of one space."""

from unittest.mock import Mock

import pytest

from gchat import chat_helpers
from gchat.chat_tools import list_spaces


def _unwrap(tool):
    fn = getattr(tool, "fn", tool)
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.fixture(autouse=True)
def _clear_name_cache():
    chat_helpers._sender_name_cache.clear()
    yield
    chat_helpers._sender_name_cache.clear()


def _services(pages=None):
    chat = Mock()
    chat.spaces.return_value.get.return_value.execute.return_value = {
        "name": "spaces/S",
        "displayName": "Project 8542",
        "spaceType": "SPACE",
    }
    chat.spaces.return_value.members.return_value.list.return_value.execute.side_effect = pages
    people = Mock()
    people.people.return_value.getBatchGet.return_value.execute.return_value = {
        "responses": [
            {
                "requestedResourceName": "people/1",
                "person": {"names": [{"displayName": "Alice Smith"}]},
            }
        ]
    }
    return chat, people


async def _list_members(chat, people, space_id="spaces/S", **kwargs):
    return await _unwrap(list_spaces)(
        chat_service=chat,
        people_service=people,
        user_google_email="me@example.com",
        space_id=space_id,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_labels_assistant_managers_unknown_roles_and_invited_groups():
    pages = [
        {
            "memberships": [
                {
                    "member": {"name": "users/1", "type": "HUMAN"},
                    "role": "ROLE_ASSISTANT_MANAGER",
                    "state": "JOINED",
                },
                {
                    "member": {"name": "users/2", "type": "HUMAN"},
                    "role": "MEMBERSHIP_ROLE_UNSPECIFIED",
                    "state": "JOINED",
                },
                {"groupMember": {"name": "groups/G"}, "state": "INVITED"},
            ]
        },
    ]
    chat, people = _services(pages)

    result = await _list_members(chat, people)

    assert result.splitlines()[1:] == [
        "- Alice Smith (users/1, assistant manager)",
        "- users/2 (unknown)",
        "- Group groups/G (invited)",
    ]


@pytest.mark.asyncio
async def test_lists_people_apps_groups_and_invites_across_pages():
    pages = [
        {
            "memberships": [
                {
                    "member": {"name": "users/1", "type": "HUMAN"},
                    "role": "ROLE_MANAGER",
                    "state": "JOINED",
                },
                {
                    "member": {"name": "users/2", "type": "HUMAN"},
                    "role": "ROLE_MEMBER",
                    "state": "INVITED",
                },
            ],
            "nextPageToken": "next",
        },
        {
            "memberships": [
                {
                    "member": {
                        "name": "users/app",
                        "type": "BOT",
                        "displayName": "Ticket Bot",
                    },
                    "role": "ROLE_MEMBER",
                    "state": "JOINED",
                },
                {
                    "groupMember": {"name": "groups/G"},
                    "role": "ROLE_MEMBER",
                    "state": "JOINED",
                },
            ]
        },
    ]
    chat, people = _services(pages)

    result = await _list_members(chat, people)

    assert result.splitlines() == [
        "Members of 'Project 8542' (ID: spaces/S): 4",
        "- Alice Smith (users/1, manager)",
        "- users/2 (member, invited)",
        "- Ticket Bot [app] (users/app, member)",
        "- Group groups/G",
    ]
    members_list = chat.spaces.return_value.members.return_value.list
    first_call, second_call = members_list.call_args_list[-2:]
    assert first_call.kwargs == {
        "parent": "spaces/S",
        "pageSize": 100,
        "showInvited": True,
        "showGroups": True,
    }
    assert second_call.kwargs["pageToken"] == "next"
    chat.spaces.return_value.list.assert_not_called()
    batch_get = people.people.return_value.getBatchGet
    assert batch_get.call_args.kwargs["resourceNames"] == ["people/1", "people/2"]


@pytest.mark.asyncio
async def test_member_listing_stops_at_page_size():
    def member(i):
        return {
            "member": {"name": f"users/{i}", "type": "HUMAN"},
            "role": "ROLE_MEMBER",
            "state": "JOINED",
        }

    pages = [
        {"memberships": [member(1), member(2)], "nextPageToken": "next"},
        {"memberships": [member(3)], "nextPageToken": "more"},
    ]
    chat, people = _services(pages)

    result = await _list_members(chat, people, page_size=2)

    lines = result.splitlines()
    assert lines[0] == (
        "Members of 'Project 8542' (ID: spaces/S): first 2 shown, "
        "more exist (raise page_size to see more)"
    )
    assert len(lines) == 3
    members_list = chat.spaces.return_value.members.return_value.list
    assert [c.kwargs["pageSize"] for c in members_list.call_args_list] == [3, 3]


@pytest.mark.asyncio
async def test_non_positive_page_size_shows_one_member():
    pages = [
        {
            "memberships": [
                {"member": {"name": "users/1", "type": "HUMAN"}, "role": "ROLE_MEMBER"},
                {"member": {"name": "users/2", "type": "HUMAN"}, "role": "ROLE_MEMBER"},
            ]
        },
    ]
    chat, people = _services(pages)

    result = await _list_members(chat, people, page_size=0)

    assert result.splitlines()[1:] == ["- Alice Smith (users/1, member)"]
    members_list = chat.spaces.return_value.members.return_value.list
    assert members_list.call_args.kwargs["pageSize"] == 2


@pytest.mark.asyncio
async def test_unnamed_space_header_uses_type_fallback_without_extra_lookups():
    pages = [
        {
            "memberships": [
                {"member": {"name": "users/1", "type": "HUMAN"}, "role": "ROLE_MEMBER"},
                {"member": {"name": "users/2", "type": "HUMAN"}, "role": "ROLE_MEMBER"},
            ]
        },
    ]
    chat, people = _services(pages)
    chat.spaces.return_value.get.return_value.execute.return_value = {
        "name": "spaces/D",
        "spaceType": "DIRECT_MESSAGE",
    }

    result = await _list_members(chat, people, space_id="spaces/D")

    assert result.splitlines()[0] == "Members of 'Direct message' (ID: spaces/D): 2"
    chat.spaces.return_value.members.return_value.list.assert_called_once()
    people.people.return_value.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("space_id", [None, "", "  "])
async def test_blank_space_id_still_lists_spaces(space_id):
    chat, people = _services()
    chat.spaces.return_value.list.return_value.execute.return_value = {"spaces": []}

    result = await _list_members(chat, people, space_id=space_id)

    assert result == "No Chat spaces found for type 'all'."
    chat.spaces.return_value.members.assert_not_called()
