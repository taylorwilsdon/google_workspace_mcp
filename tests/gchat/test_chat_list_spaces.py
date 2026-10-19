"""Unit tests for the Google Chat list_spaces space_type filter."""

from unittest.mock import Mock

import pytest

from gchat.chat_tools import list_spaces


def _unwrap(tool):
    fn = getattr(tool, "fn", tool)
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


async def _list_spaces_request(space_type):
    service = Mock()
    service.spaces().list().execute.return_value = {"spaces": []}

    await _unwrap(list_spaces)(
        chat_service=service,
        people_service=Mock(),
        user_google_email="me@example.com",
        space_type=space_type,
    )

    return service.spaces().list.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "space_type, expected_filter",
    [
        ("room", 'spaceType = "SPACE"'),
        ("dm", 'spaceType = "DIRECT_MESSAGE"'),
    ],
)
async def test_list_spaces_quotes_space_type_filter(space_type, expected_filter):
    request = await _list_spaces_request(space_type)

    assert request["filter"] == expected_filter


@pytest.mark.asyncio
async def test_list_spaces_sends_no_filter_for_all_space_types():
    request = await _list_spaces_request("all")

    assert "filter" not in request
