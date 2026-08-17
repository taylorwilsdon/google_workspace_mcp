"""Unit tests for Workspace directory and Google Group lookups.

Both capabilities are folded into existing contacts tools: directory search is a
``source`` on ``search_contacts``, and Google Group membership is what
``get_contact_group`` returns when it is given a group email address.
"""

from unittest.mock import MagicMock, call

import pytest

from core.utils import UserInputError
from gcontacts.contacts_tools import (
    get_contact_group as _get_contact_group_wrapped,
    search_contacts as _search_contacts_wrapped,
)


def _unwrap(fn):
    """Strip registration, authentication, and HTTP error decorators."""
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


get_contact_group = _unwrap(_get_contact_group_wrapped)
search_contacts = _unwrap(_search_contacts_wrapped)

DEFAULT_READ_MASK = "names,nicknames,emailAddresses,phoneNumbers,organizations"
DIRECTORY_SOURCES = [
    "DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE",
    "DIRECTORY_SOURCE_TYPE_DOMAIN_CONTACT",
]


def _person(resource_name, display_name, email):
    return {
        "resourceName": resource_name,
        "names": [{"displayName": display_name}],
        "emailAddresses": [{"value": email}],
    }


# =============================================================================
# search_contacts — directory sources
# =============================================================================


class TestSearchContactsDirectory:
    @pytest.mark.asyncio
    async def test_directory_source_queries_directory_only(self):
        service = MagicMock()
        service.people.return_value.searchDirectoryPeople.return_value.execute.return_value = {
            "people": [_person("people/123", "Ada Lovelace", "ada@example.com")]
        }

        result = await search_contacts(
            service=service,
            user_google_email="caller@example.com",
            query="Ada",
            page_size=75,
            source="directory",
        )

        service.people.return_value.searchDirectoryPeople.assert_called_once_with(
            query="Ada",
            readMask=DEFAULT_READ_MASK,
            sources=DIRECTORY_SOURCES,
            pageSize=50,
        )
        service.people.return_value.searchContacts.assert_not_called()
        assert "Ada Lovelace" in result
        assert "ada@example.com" in result
        assert "Contact ID: 123" in result

    @pytest.mark.asyncio
    async def test_all_source_merges_both_searches_and_dedupes(self):
        service = MagicMock()
        service.people.return_value.searchContacts.return_value.execute.return_value = {
            "results": [
                {"person": _person("people/123", "Ada Lovelace", "ada@example.com")},
                {"person": _person("people/456", "Alan Turing", "alan@example.com")},
            ]
        }
        service.people.return_value.searchDirectoryPeople.return_value.execute.return_value = {
            "people": [
                _person("people/123", "Ada Lovelace", "ada@example.com"),
                _person("people/789", "Grace Hopper", "grace@example.com"),
            ]
        }

        result = await search_contacts(
            service=service,
            user_google_email="caller@example.com",
            query="a",
            source="all",
        )

        assert "(3 found)" in result
        assert result.count("Ada Lovelace") == 1
        assert "Grace Hopper" in result

    @pytest.mark.asyncio
    async def test_contacts_source_leaves_directory_untouched(self):
        service = MagicMock()
        service.people.return_value.searchContacts.return_value.execute.return_value = {
            "results": [{"person": _person("people/1", "Ada", "ada@example.com")}]
        }

        result = await search_contacts(
            service=service,
            user_google_email="caller@example.com",
            query="Ada",
            page_size=75,
        )

        service.people.return_value.searchContacts.assert_any_call(
            query="Ada", readMask=DEFAULT_READ_MASK, pageSize=30
        )
        service.people.return_value.searchDirectoryPeople.assert_not_called()
        assert "Ada" in result

    @pytest.mark.asyncio
    async def test_empty_directory_search_explains_contact_sharing(self):
        service = MagicMock()
        service.people.return_value.searchDirectoryPeople.return_value.execute.return_value = {
            "people": []
        }

        result = await search_contacts(
            service=service,
            user_google_email="caller@example.com",
            query="Nobody",
            source="directory",
        )

        assert "No directory profiles found matching 'Nobody'" in result
        assert "contact sharing" in result

    @pytest.mark.asyncio
    async def test_empty_contacts_search_keeps_original_message(self):
        service = MagicMock()
        service.people.return_value.searchContacts.return_value.execute.return_value = {
            "results": []
        }

        result = await search_contacts(
            service=service,
            user_google_email="caller@example.com",
            query="Nobody",
        )

        assert result == "No contacts found matching 'Nobody' for caller@example.com."

    @pytest.mark.asyncio
    async def test_rejects_non_positive_page_size(self):
        with pytest.raises(UserInputError, match="page_size must be >= 1"):
            await search_contacts(
                service=MagicMock(),
                user_google_email="caller@example.com",
                query="Ada",
                page_size=0,
                source="directory",
            )


# =============================================================================
# get_contact_group — Google Group membership
# =============================================================================


class TestGetContactGroupGoogleGroup:
    @staticmethod
    def _cloudidentity_service(pages):
        service = MagicMock()
        service.groups.return_value.lookup.return_value.execute.return_value = {
            "name": "groups/123"
        }
        list_request = service.groups.return_value.memberships.return_value.list
        list_request.return_value.execute.side_effect = pages
        return service

    @pytest.mark.asyncio
    async def test_group_email_paginates_and_formats_roles(self):
        cloudidentity_service = self._cloudidentity_service(
            [
                {
                    "memberships": [
                        {
                            "preferredMemberKey": {"id": "owner@example.com"},
                            "roles": [{"name": "OWNER"}],
                        }
                    ],
                    "nextPageToken": "page-2",
                },
                {
                    "memberships": [
                        {
                            "preferredMemberKey": {"id": "member@example.com"},
                            "roles": [],
                        }
                    ]
                },
            ]
        )
        people_service = MagicMock()

        result = await get_contact_group(
            people_service=people_service,
            cloudidentity_service=cloudidentity_service,
            user_google_email="caller@example.com",
            group_id="team@example.com",
            max_members=3,
        )

        cloudidentity_service.groups.return_value.lookup.assert_called_once_with(
            groupKey_id="team@example.com"
        )
        assert (
            cloudidentity_service.groups.return_value.memberships.return_value.list.call_args_list
            == [
                call(parent="groups/123", pageSize=3),
                call(parent="groups/123", pageSize=2, pageToken="page-2"),
            ]
        )
        people_service.contactGroups.assert_not_called()
        assert "owner@example.com (OWNER)" in result
        assert "member@example.com (MEMBER)" in result
        assert "Truncated" not in result

    @pytest.mark.asyncio
    async def test_remaining_page_is_reported_as_truncated(self):
        cloudidentity_service = self._cloudidentity_service(
            [
                {
                    "memberships": [
                        {"preferredMemberKey": {"id": "one@example.com"}},
                        {"preferredMemberKey": {"id": "two@example.com"}},
                    ],
                    "nextPageToken": "more-results",
                }
            ]
        )

        result = await get_contact_group(
            people_service=MagicMock(),
            cloudidentity_service=cloudidentity_service,
            user_google_email="caller@example.com",
            group_id="team@example.com",
            max_members=2,
        )

        assert "(2 shown)" in result
        assert "Truncated at max_members" in result

    @pytest.mark.asyncio
    async def test_invisible_membership_explains_visibility_setting(self):
        cloudidentity_service = self._cloudidentity_service([{"memberships": []}])

        result = await get_contact_group(
            people_service=MagicMock(),
            cloudidentity_service=cloudidentity_service,
            user_google_email="caller@example.com",
            group_id="private@example.com",
        )

        assert "no members visible to caller@example.com" in result
        assert "Who can view members" in result

    @pytest.mark.asyncio
    async def test_contact_group_id_uses_people_api(self):
        people_service = MagicMock()
        people_service.contactGroups.return_value.get.return_value.execute.return_value = {
            "name": "Friends",
            "groupType": "USER_CONTACT_GROUP",
            "memberCount": 1,
            "memberResourceNames": ["people/c1"],
        }
        cloudidentity_service = MagicMock()

        result = await get_contact_group(
            people_service=people_service,
            cloudidentity_service=cloudidentity_service,
            user_google_email="caller@example.com",
            group_id="abc123",
        )

        people_service.contactGroups.return_value.get.assert_called_once_with(
            resourceName="contactGroups/abc123",
            maxMembers=100,
            groupFields="name,groupType,memberCount,metadata",
        )
        cloudidentity_service.groups.assert_not_called()
        assert "Name: Friends" in result

    @pytest.mark.asyncio
    async def test_rejects_non_positive_max_members(self):
        with pytest.raises(UserInputError, match="max_members must be >= 1"):
            await get_contact_group(
                people_service=MagicMock(),
                cloudidentity_service=MagicMock(),
                user_google_email="caller@example.com",
                group_id="team@example.com",
                max_members=0,
            )
