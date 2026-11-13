"""Resource IDs that end up in reserved-expansion API paths are refused before any request.

People resource names, Chat space and message names and the Slides presentation ID are
expanded into the URL path without escaping ({+name} in the discovery document). An ID
carrying ':', '?' or '#' could change the method or the query actually sent, so every tool
that takes such an ID must refuse it without touching the Google client.
"""

from unittest.mock import AsyncMock, Mock

import pytest

import auth.service_decorator as service_decorator
from core.utils import UserInputError, check_path_segment
from gchat.chat_helpers import _normalize_space_resource_name
from gchat.chat_tools import create_reaction, download_chat_attachment, send_message
from gcontacts.contacts_tools import (
    get_contact,
    get_contact_group,
    manage_contact,
    manage_contact_group,
    manage_contacts_batch,
)
from gslides.slides_tools import batch_update_presentation, get_presentation

EMAIL = "test@example.com"

BAD_SEGMENTS = [
    "c1:deleteContactPhoto#",
    "g1?deleteContacts=true#",
    "c1#",
    "c1:x",
    "..",
    ".",
    "a/b",
    "a b",
    "c1\n",
    "",
]


@pytest.fixture
def service(monkeypatch):
    """A fake Google client behind the real auth and HTTP error decorators."""
    fake = Mock()
    monkeypatch.setattr(service_decorator, "_user_email_is_managed", lambda: False)
    monkeypatch.setattr(
        service_decorator,
        "_get_auth_context",
        AsyncMock(return_value=(None, None, None)),
    )
    monkeypatch.setattr(service_decorator, "_detect_oauth_version", lambda *args: False)
    monkeypatch.setattr(
        service_decorator,
        "_authenticate_service",
        AsyncMock(return_value=(fake, EMAIL)),
    )
    return fake


def _tool(tool):
    return getattr(tool, "fn", tool)


def _assert_client_untouched(fake):
    """Only the decorator's cleanup may have run: no API resource was built or called."""
    calls = [name for name, _args, _kwargs in fake.mock_calls]
    assert all(name == "close" for name in calls), calls


# --- the shared check -------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "c1234567890",
        "me",
        "myContacts",
        "1a2b3c4d",
        "AAQAGqUsooo",
        "BBBBBBBBBBB.BBBBBBBBBBB",
        "client-custom-name",
        "1AbC_dEf-123xyz",
        "a..b",
    ],
)
def test_check_path_segment_accepts_real_ids(value):
    assert check_path_segment(value, "id") == value


@pytest.mark.parametrize("value", BAD_SEGMENTS + ["...", None, 123])
def test_check_path_segment_refuses_unsafe_ids(value):
    with pytest.raises(UserInputError, match="Invalid id"):
        check_path_segment(value, "id")


@pytest.mark.parametrize(
    "space_id", ["S:x", "spaces/..", "..", "spaces/S:completeImport"]
)
def test_space_normalizer_refuses_unsafe_ids(space_id):
    with pytest.raises(UserInputError, match="space_id"):
        _normalize_space_resource_name(space_id)


# --- People -----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", BAD_SEGMENTS)
@pytest.mark.parametrize("prefix", ["", "people/"])
async def test_get_contact_refuses_bad_id(service, bad, prefix):
    with pytest.raises(UserInputError, match="contact_id"):
        await _tool(get_contact)(user_google_email=EMAIL, contact_id=prefix + bad)
    _assert_client_untouched(service)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["update", "delete"])
@pytest.mark.parametrize(
    "bad", ["c1:deleteContactPhoto#", "people/c1:deleteContactPhoto#", ".."]
)
async def test_manage_contact_refuses_bad_id(service, action, bad):
    with pytest.raises(UserInputError, match="contact_id"):
        await _tool(manage_contact)(
            user_google_email=EMAIL, action=action, contact_id=bad, given_name="x"
        )
    _assert_client_untouched(service)


@pytest.mark.asyncio
@pytest.mark.parametrize("contact_id", ["c123", "people/c123"])
async def test_manage_contact_delete_sends_the_normalised_name(service, contact_id):
    await _tool(manage_contact)(
        user_google_email=EMAIL, action="delete", contact_id=contact_id
    )
    service.people.return_value.deleteContact.assert_called_once_with(
        resourceName="people/c123"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["g1?deleteContacts=true#", "contactGroups/g1:x", ".."])
async def test_get_contact_group_refuses_bad_id(service, bad):
    with pytest.raises(UserInputError, match="group_id"):
        await _tool(get_contact_group)(user_google_email=EMAIL, group_id=bad)
    _assert_client_untouched(service)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"action": "delete", "group_id": "contactGroups/g1?deleteContacts=true#"},
        {"action": "delete", "group_id": "g1?deleteContacts=true#"},
        {"action": "update", "group_id": "g1:x", "name": "n"},
        {"action": "modify_members", "group_id": "..", "add_contact_ids": ["c1"]},
    ],
)
async def test_manage_contact_group_refuses_bad_group_id(service, kwargs):
    with pytest.raises(UserInputError, match="group_id"):
        await _tool(manage_contact_group)(user_google_email=EMAIL, **kwargs)
    _assert_client_untouched(service)


@pytest.mark.asyncio
async def test_manage_contact_group_delete_keeps_the_callers_delete_contacts(service):
    await _tool(manage_contact_group)(
        user_google_email=EMAIL, action="delete", group_id="g1", delete_contacts=False
    )
    service.contactGroups.return_value.delete.assert_called_once_with(
        resourceName="contactGroups/g1", deleteContacts=False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["add_contact_ids", "remove_contact_ids"])
async def test_manage_contact_group_refuses_bad_member_ids(service, field):
    with pytest.raises(UserInputError, match="contact_id"):
        await _tool(manage_contact_group)(
            user_google_email=EMAIL,
            action="modify_members",
            group_id="g1",
            **{field: ["c1", "c2:x#"]},
        )
    _assert_client_untouched(service)


@pytest.mark.asyncio
async def test_batch_delete_refuses_bad_ids(service):
    with pytest.raises(UserInputError, match="contact_id"):
        await _tool(manage_contacts_batch)(
            user_google_email=EMAIL, action="delete", contact_ids=["c1", "c2?x=1"]
        )
    _assert_client_untouched(service)


@pytest.mark.asyncio
async def test_batch_update_refuses_bad_ids(service):
    with pytest.raises(UserInputError, match="contact_id"):
        await _tool(manage_contacts_batch)(
            user_google_email=EMAIL,
            action="update",
            field="names",
            updates=[
                {"contact_id": "c1", "given_name": "a"},
                {"contact_id": "people/c2:deleteContactPhoto#", "given_name": "b"},
            ],
        )
    _assert_client_untouched(service)


# --- Chat -------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["spaces/S:completeImport", "S?x=1#", "spaces/.."])
async def test_send_message_refuses_bad_space_id(service, bad):
    with pytest.raises(UserInputError, match="space_id"):
        await _tool(send_message)(
            user_google_email=EMAIL, space_id=bad, message_text="t"
        )
    _assert_client_untouched(service)


BAD_MESSAGES = [
    "spaces/S/messages/M:x#",
    "spaces/S/messages/M?updateMask=x#",
    "spaces/S:x/messages/M",
    "spaces/S/messages/..",
    "spaces/../messages/M",
    "spaces/S/messages/M/reactions/R",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", BAD_MESSAGES)
async def test_send_message_edit_refuses_bad_message_name(service, bad):
    with pytest.raises(UserInputError, match="message_name"):
        await _tool(send_message)(
            user_google_email=EMAIL,
            space_id="spaces/S",
            message_text="t",
            message_name=bad,
        )
    _assert_client_untouched(service)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", BAD_MESSAGES)
async def test_create_reaction_refuses_bad_message_id(service, bad):
    with pytest.raises(UserInputError, match="message_id"):
        await _tool(create_reaction)(
            user_google_email=EMAIL, message_id=bad, emoji_unicode="+1"
        )
    _assert_client_untouched(service)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", BAD_MESSAGES)
async def test_download_attachment_refuses_bad_message_id(service, bad):
    with pytest.raises(UserInputError, match="message_id"):
        await _tool(download_chat_attachment)(user_google_email=EMAIL, message_id=bad)
    _assert_client_untouched(service)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message_id",
    [
        "spaces/S/messages/BBBBBBBBBBB.BBBBBBBBBBB",
        "spaces/S/messages/client-custom-name",
    ],
)
async def test_create_reaction_accepts_dotted_and_client_ids(service, message_id):
    reactions = service.spaces.return_value.messages.return_value.reactions.return_value
    reactions.create.return_value.execute.return_value = {
        "name": f"{message_id}/reactions/R"
    }

    await _tool(create_reaction)(
        user_google_email=EMAIL, message_id=message_id, emoji_unicode="+1"
    )

    reactions.create.assert_called_once_with(
        parent=message_id, body={"emoji": {"unicode": "+1"}}
    )


# --- Slides -----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["P1?fields=x#", "P1:x", "..", "a/b"])
async def test_get_presentation_refuses_bad_id(service, bad):
    with pytest.raises(UserInputError, match="presentation_id"):
        await _tool(get_presentation)(user_google_email=EMAIL, presentation_id=bad)
    _assert_client_untouched(service)


@pytest.mark.asyncio
async def test_batch_update_presentation_refuses_bad_id(service):
    with pytest.raises(UserInputError, match="presentation_id"):
        await _tool(batch_update_presentation)(
            user_google_email=EMAIL,
            presentation_id="P1?fields=x#",
            requests=[{"insertText": {"objectId": "s1", "text": "x"}}],
        )
    _assert_client_untouched(service)
