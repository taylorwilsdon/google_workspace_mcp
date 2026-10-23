"""Tests for list_spaces listing the members of one space."""

import json
from unittest.mock import Mock

import httplib2
import pytest
from fastmcp.exceptions import ToolError
from googleapiclient.errors import HttpError

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


def _services(pages=None, members_error=None):
    chat = Mock()
    chat.spaces.return_value.get.return_value.execute.return_value = {
        "name": "spaces/S",
        "displayName": "Project 8542",
        "spaceType": "SPACE",
    }
    members_list = chat.spaces.return_value.members.return_value.list
    if members_error is not None:
        members_list.return_value.execute.side_effect = members_error
    else:
        members_list.return_value.execute.side_effect = pages
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


async def _list_members(chat, people, space_id="spaces/S"):
    return await _unwrap(list_spaces)(
        chat_service=chat,
        people_service=people,
        user_google_email="me@example.com",
        space_id=space_id,
    )


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
        "- Group groups/G (member)",
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


@pytest.mark.asyncio
async def test_missing_memberships_scope_is_explained():
    detail = {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
    }
    body = {"error": {"code": 403, "message": "Forbidden", "details": [detail]}}
    error = HttpError(httplib2.Response({"status": 403}), json.dumps(body).encode())
    chat, people = _services(members_error=error)

    with pytest.raises(ToolError, match="chat.memberships.readonly"):
        await _list_members(chat, people)


@pytest.mark.asyncio
@pytest.mark.parametrize("space_id", [None, "", "  "])
async def test_blank_space_id_still_lists_spaces(space_id):
    chat, people = _services()
    chat.spaces.return_value.list.return_value.execute.return_value = {"spaces": []}

    result = await _list_members(chat, people, space_id=space_id)

    assert result == "No Chat spaces found for type 'all'."
    chat.spaces.return_value.members.assert_not_called()
