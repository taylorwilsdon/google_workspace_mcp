"""Unit tests for naming Google Chat spaces that have no displayName."""

import ssl
from unittest.mock import Mock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from auth.scopes import CHAT_MEMBERSHIPS_READONLY_SCOPE
from gchat import chat_helpers
from gchat.chat_tools import get_messages, list_spaces, search_messages

SELF_EMAIL = "me@example.com"
NAMES = {
    "100": "Me Myself",
    "200": "Alice Smith",
    "300": "Bob Jones",
    "400": "Carol White",
    "500": "Dan Brown",
    "600": "Eve Black",
}
DM_SPACE = {"name": "spaces/DM1", "spaceType": "DIRECT_MESSAGE"}
DM2_SPACE = {"name": "spaces/DM2", "spaceType": "DIRECT_MESSAGE"}
GROUP_SPACE = {"name": "spaces/GC1", "spaceType": "GROUP_CHAT"}
NAMED_SPACE = {
    "name": "spaces/ROOM1",
    "spaceType": "SPACE",
    "displayName": "Engineering",
}


def _membership(space_name, user_id, member_type="HUMAN"):
    return {
        "name": f"{space_name}/members/{user_id}",
        "member": {"name": f"users/{user_id}", "type": member_type},
    }


def _memberships(space_name, *user_ids):
    return [_membership(space_name, uid) for uid in user_ids]


MEMBERS = {
    "spaces/DM1": _memberships("spaces/DM1", "100", "200"),
    "spaces/GC1": [
        *_memberships("spaces/GC1", "100", "200", "300"),
        _membership("spaces/GC1", "app", "BOT"),
    ],
}


def _http_error(status):
    return HttpError(httplib2.Response({"status": status}), b"{}")


def _request(result=None, error=None):
    request = Mock()
    if error is not None:
        request.execute.side_effect = error
    else:
        request.execute.return_value = result
    return request


def _person(resource_name):
    user_id = resource_name.split("/")[1]
    return {"resourceName": resource_name, "names": [{"displayName": NAMES[user_id]}]}


def _people_service(me=None):
    people_service = Mock()

    def get(resourceName, personFields):
        if resourceName == "people/me":
            return _request(_person("people/100") if me is None else me)
        return _request(_person(resourceName))

    def get_batch_get(resourceNames, personFields):
        responses = [
            {"httpStatusCode": 200, "requestedResourceName": r, "person": _person(r)}
            for r in resourceNames
        ]
        return _request({"responses": responses})

    people_service.people().get.side_effect = get
    people_service.people().getBatchGet.side_effect = get_batch_get
    return people_service


def _failing_people_service(error):
    people_service = Mock()
    people_service.people().get.side_effect = lambda **kw: _request(error=error)
    return people_service


def _batch_lookups(people_service):
    return [
        c.kwargs["resourceNames"]
        for c in people_service.people().getBatchGet.call_args_list
    ]


def _chat_service(
    spaces, members=None, members_error=None, messages=None, member_pages=None
):
    # members_error is either one error for every space or a dict per space;
    # member_pages maps a space to its memberships split into pages.
    chat_service = Mock()
    all_members = {**MEMBERS, **(members or {})}

    def list_members(parent, pageToken=None, **kw):
        error = members_error
        if isinstance(members_error, dict):
            error = members_error.get(parent)
        if error is not None:
            return _request(error=error)
        pages = (member_pages or {}).get(parent) or [all_members.get(parent, [])]
        page = int(pageToken or 0)
        response = {"memberships": pages[page]}
        if page + 1 < len(pages):
            response["nextPageToken"] = str(page + 1)
        return _request(response)

    chat_service.spaces().list.side_effect = lambda **kw: _request({"spaces": spaces})
    chat_service.spaces().get.side_effect = lambda name: _request(
        next(s for s in spaces if s["name"] == name)
    )
    chat_service.spaces().messages().list.side_effect = lambda parent, **kw: _request(
        {"messages": (messages or {}).get(parent, [])}
    )
    chat_service.spaces().members().list.side_effect = list_members
    return chat_service


def _message(space_name, text, sender_id="200"):
    return {
        "name": f"{space_name}/messages/M1",
        "text": text,
        "createTime": "2025-01-01T00:00:00Z",
        "sender": {"name": f"users/{sender_id}", "type": "HUMAN"},
    }


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


async def _list_spaces(chat_service, people_service=None):
    return await _unwrap(list_spaces)(
        chat_service=chat_service,
        people_service=people_service or _people_service(),
        user_google_email=SELF_EMAIL,
    )


@pytest.mark.asyncio
async def test_list_spaces_labels_group_chat_with_only_caller_left():
    members = {
        "spaces/GC1": [
            _membership("spaces/GC1", "100"),
            _membership("spaces/GC1", "app", "BOT"),
        ]
    }

    result = await _list_spaces(_chat_service([GROUP_SPACE], members=members))

    assert "- Group chat (ID: spaces/GC1, Type: GROUP_CHAT)" in result


@pytest.mark.asyncio
async def test_list_spaces_keeps_display_name_without_lookups():
    chat_service = _chat_service([NAMED_SPACE])
    people_service = _people_service()

    result = await _list_spaces(chat_service, people_service)

    assert "- Engineering (ID: spaces/ROOM1, Type: SPACE)" in result
    chat_service.spaces().members().list.assert_not_called()
    people_service.people().get.assert_not_called()
    people_service.people().getBatchGet.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other_ids, expected_name",
    [
        (
            ["200", "300", "400", "500"],
            "Alice Smith, Bob Jones, Carol White and 1 other",
        ),
        (
            ["200", "300", "400", "500", "600"],
            "Alice Smith, Bob Jones, Carol White and 2 others",
        ),
    ],
)
async def test_list_spaces_names_large_group_chat_after_first_three_members(
    other_ids, expected_name
):
    members = {"spaces/GC1": _memberships("spaces/GC1", "100", *other_ids)}
    people_service = _people_service()

    result = await _list_spaces(
        _chat_service([GROUP_SPACE], members=members), people_service
    )

    assert f"- {expected_name} (ID: spaces/GC1, Type: GROUP_CHAT)" in result
    assert _batch_lookups(people_service) == [
        ["people/200", "people/300", "people/400"]
    ]


@pytest.mark.asyncio
async def test_list_spaces_resolves_member_names_in_one_batch():
    members = {"spaces/DM2": _memberships("spaces/DM2", "100", "300")}
    people_service = _people_service()

    result = await _list_spaces(
        _chat_service([DM_SPACE, DM2_SPACE, GROUP_SPACE], members=members),
        people_service,
    )

    assert "- Alice Smith (ID: spaces/DM1, Type: DIRECT_MESSAGE)" in result
    assert "- Bob Jones (ID: spaces/DM2, Type: DIRECT_MESSAGE)" in result
    assert "- Alice Smith, Bob Jones (ID: spaces/GC1, Type: GROUP_CHAT)" in result
    assert people_service.people().get.call_count == 1  # people/me only
    assert _batch_lookups(people_service) == [["people/200", "people/300"]]


@pytest.mark.asyncio
async def test_list_spaces_splits_batch_lookup_into_chunks(monkeypatch):
    monkeypatch.setattr(chat_helpers, "_PEOPLE_BATCH_SIZE", 2)
    members = {
        "spaces/GC1": _memberships("spaces/GC1", "100", "200", "300", "400"),
        "spaces/DM2": _memberships("spaces/DM2", "100", "500"),
    }
    people_service = _people_service()

    result = await _list_spaces(
        _chat_service([GROUP_SPACE, DM2_SPACE], members=members), people_service
    )

    assert "- Alice Smith, Bob Jones, Carol White (ID: spaces/GC1" in result
    assert "- Dan Brown (ID: spaces/DM2" in result
    assert _batch_lookups(people_service) == [
        ["people/200", "people/300"],
        ["people/400", "people/500"],
    ]


@pytest.mark.asyncio
async def test_list_spaces_uses_cached_names_without_batch_lookup():
    chat_helpers._sender_name_cache["users/200"] = "Alice (cached)"
    people_service = _people_service()

    result = await _list_spaces(_chat_service([DM_SPACE]), people_service)

    assert "- Alice (cached) (ID: spaces/DM1" in result
    people_service.people().getBatchGet.assert_not_called()


@pytest.mark.asyncio
async def test_list_spaces_shows_member_ids_when_batch_lookup_fails():
    people_service = _people_service()
    people_service.people().getBatchGet.side_effect = lambda **kw: _request(
        error=ssl.SSLError("record layer failure")
    )

    result = await _list_spaces(_chat_service([DM_SPACE]), people_service)

    assert "- users/200 (ID: spaces/DM1, Type: DIRECT_MESSAGE)" in result


@pytest.mark.asyncio
async def test_list_spaces_names_member_by_email_when_person_has_no_name():
    people_service = _people_service()
    people_service.people().getBatchGet.side_effect = lambda **kw: _request(
        {
            "responses": [
                {
                    "httpStatusCode": 200,
                    "requestedResourceName": "people/200",
                    "person": {
                        "resourceName": "people/200",
                        "emailAddresses": [{"value": "alice@example.com"}],
                    },
                }
            ]
        }
    )

    result = await _list_spaces(_chat_service([DM_SPACE]), people_service)

    assert "- alice@example.com (ID: spaces/DM1, Type: DIRECT_MESSAGE)" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [_http_error(403), TimeoutError("timed out")])
async def test_list_spaces_falls_back_once_when_caller_lookup_fails(error):
    chat_service = _chat_service([DM_SPACE, DM2_SPACE, GROUP_SPACE, NAMED_SPACE])
    people_service = _failing_people_service(error)

    result = await _list_spaces(chat_service, people_service)

    assert "- Direct message (ID: spaces/DM2, Type: DIRECT_MESSAGE)" in result
    assert "- Group chat (ID: spaces/GC1, Type: GROUP_CHAT)" in result
    assert "- Engineering (ID: spaces/ROOM1, Type: SPACE)" in result
    assert people_service.people().get.call_count == 1
    chat_service.spaces().members().list.assert_not_called()


@pytest.mark.asyncio
async def test_list_spaces_does_not_name_space_after_caller_when_self_id_is_missing():
    chat_service = _chat_service([DM_SPACE])

    result = await _list_spaces(chat_service, _people_service(me={}))

    assert "- Direct message (ID: spaces/DM1, Type: DIRECT_MESSAGE)" in result
    chat_service.spaces().members().list.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429])
async def test_list_spaces_stops_listing_members_once_forbidden_or_rate_limited(
    status,
):
    dms = [{**DM_SPACE, "name": f"spaces/DM{i}"} for i in range(1, 4)]
    chat_service = _chat_service([*dms, NAMED_SPACE], members_error=_http_error(status))

    result = await _list_spaces(chat_service)

    assert "- Direct message (ID: spaces/DM3, Type: DIRECT_MESSAGE)" in result
    assert "- Engineering (ID: spaces/ROOM1, Type: SPACE)" in result
    assert chat_service.spaces().members().list.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [_http_error(404), TimeoutError("timed out")])
async def test_list_spaces_keeps_listing_members_after_space_specific_error(error):
    chat_service = _chat_service(
        [DM_SPACE, DM2_SPACE],
        members={"spaces/DM2": _memberships("spaces/DM2", "100", "200")},
        members_error={"spaces/DM1": error},
    )

    result = await _list_spaces(chat_service)

    assert "- Direct message (ID: spaces/DM1, Type: DIRECT_MESSAGE)" in result
    assert "- Alice Smith (ID: spaces/DM2, Type: DIRECT_MESSAGE)" in result


@pytest.mark.asyncio
async def test_list_spaces_counts_members_across_all_pages():
    member_pages = {
        "spaces/GC1": [
            _memberships("spaces/GC1", "100", "200", "300"),
            _memberships("spaces/GC1", "400", "500"),
        ]
    }
    chat_service = _chat_service([GROUP_SPACE], member_pages=member_pages)

    result = await _list_spaces(chat_service)

    assert "- Alice Smith, Bob Jones, Carol White and 1 other (ID: spaces/GC1" in result
    page_tokens = [
        c.kwargs.get("pageToken")
        for c in chat_service.spaces().members().list.call_args_list
    ]
    assert page_tokens == [None, "1"]


@pytest.mark.asyncio
async def test_list_spaces_stops_listing_members_after_consecutive_network_errors():
    dms = [{**DM_SPACE, "name": f"spaces/DM{i}"} for i in range(1, 6)]
    chat_service = _chat_service(dms, members_error=TimeoutError("timed out"))

    result = await _list_spaces(chat_service)

    assert "- Direct message (ID: spaces/DM5, Type: DIRECT_MESSAGE)" in result
    assert chat_service.spaces().members().list.call_count == 2


@pytest.mark.asyncio
async def test_list_spaces_keeps_listing_members_between_isolated_network_errors():
    dms = [{**DM_SPACE, "name": f"spaces/DM{i}"} for i in range(1, 6)]
    members = {
        f"spaces/DM{i}": _memberships(f"spaces/DM{i}", "100", "200") for i in (2, 4)
    }
    errors = {f"spaces/DM{i}": TimeoutError("timed out") for i in (1, 3, 5)}
    chat_service = _chat_service(dms, members=members, members_error=errors)

    result = await _list_spaces(chat_service)

    assert "- Alice Smith (ID: spaces/DM4, Type: DIRECT_MESSAGE)" in result
    assert chat_service.spaces().members().list.call_count == 5


@pytest.mark.parametrize("tool", [list_spaces, get_messages, search_messages])
def test_space_naming_tools_do_not_require_memberships_scope(tool):
    # Tokens granted before the scope was added must keep working.
    assert CHAT_MEMBERSHIPS_READONLY_SCOPE not in tool._required_google_scopes


@pytest.mark.asyncio
async def test_get_messages_names_direct_message_space():
    chat_service = _chat_service(
        [DM_SPACE], messages={"spaces/DM1": [_message("spaces/DM1", "hi")]}
    )

    result = await _unwrap(get_messages)(
        chat_service=chat_service,
        people_service=_people_service(),
        user_google_email=SELF_EMAIL,
        space_id="spaces/DM1",
    )

    assert "Messages from 'Alice Smith' (ID: spaces/DM1)" in result


@pytest.mark.asyncio
async def test_search_messages_names_each_direct_message_space_once():
    chat_service = _chat_service(
        [NAMED_SPACE, DM_SPACE],
        messages={
            "spaces/ROOM1": [_message("spaces/ROOM1", "deploy done", sender_id="300")],
            "spaces/DM1": [
                _message("spaces/DM1", "deploy tonight?"),
                _message("spaces/DM1", "deploy moved", sender_id="100"),
            ],
        },
    )

    result = await _unwrap(search_messages)(
        chat_service=chat_service,
        people_service=_people_service(),
        user_google_email=SELF_EMAIL,
        query="deploy",
    )

    assert "Alice Smith in 'Alice Smith': deploy tonight?" in result
    assert "Me Myself in 'Alice Smith': deploy moved" in result
    assert "Bob Jones in 'Engineering': deploy done" in result
    assert chat_service.spaces().members().list.call_count == 1


@pytest.mark.asyncio
async def test_search_messages_names_space_after_sender_lookup_failed():
    chat_service = _chat_service(
        [DM_SPACE], messages={"spaces/DM1": [_message("spaces/DM1", "deploy?")]}
    )
    people_service = _people_service()

    def get(resourceName, personFields):
        if resourceName == "people/me":
            return _request(_person("people/100"))
        return _request(error=TimeoutError("timed out"))

    people_service.people().get.side_effect = get

    result = await _unwrap(search_messages)(
        chat_service=chat_service,
        people_service=people_service,
        user_google_email=SELF_EMAIL,
        query="deploy",
    )

    assert "users/200 in 'Alice Smith': deploy?" in result


@pytest.mark.asyncio
async def test_resolve_sender_falls_back_to_email_when_person_has_no_name():
    people_service = Mock()
    people_service.people().get.side_effect = lambda **kw: _request(
        {"emailAddresses": [{"value": "alice@example.com"}]}
    )

    name = await chat_helpers._resolve_sender(people_service, {"name": "users/200"})

    assert name == "alice@example.com"


@pytest.mark.asyncio
async def test_search_messages_names_explicit_direct_message_space():
    chat_service = _chat_service(
        [DM_SPACE], messages={"spaces/DM1": [_message("spaces/DM1", "deploy?")]}
    )

    result = await _unwrap(search_messages)(
        chat_service=chat_service,
        people_service=_people_service(),
        user_google_email=SELF_EMAIL,
        query="deploy",
        space_id="spaces/DM1",
    )

    assert "Alice Smith in 'Alice Smith': deploy?" in result
