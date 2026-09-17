"""
Unit tests for directory-people tools backed by the Google People API
(listDirectoryPeople and searchDirectoryPeople).

Each tool is invoked through its unwrapped async function to bypass auth
decorators, with a MagicMock standing in for the People API client.
"""

import asyncio
import os
import sys

import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcontacts.contacts_tools import (  # noqa: E402  (path setup above)
    DEFAULT_DIRECTORY_SOURCES,
    DIRECTORY_DEFAULT_PERSON_FIELDS,
    DIRECTORY_MERGE_SOURCE_CONTACT,
    DIRECTORY_SOURCE_DOMAIN_CONTACT,
    DIRECTORY_SOURCE_DOMAIN_PROFILE,
    _resolve_directory_sources,
    list_directory_people as _list_directory_people_wrapped,
    search_directory_people as _search_directory_people_wrapped,
)
from core.utils import UserInputError


def _unwrap(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


list_directory_people = _unwrap(_list_directory_people_wrapped)
search_directory_people = _unwrap(_search_directory_people_wrapped)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _person(name, email):
    return {
        "resourceName": "people/c123",
        "names": [{"displayName": name}],
        "emailAddresses": [{"value": email}],
    }


class TestResolveDirectorySources:
    def test_default_when_none(self):
        assert _resolve_directory_sources(None) == list(DEFAULT_DIRECTORY_SOURCES)

    def test_default_when_empty(self):
        assert _resolve_directory_sources([]) == list(DEFAULT_DIRECTORY_SOURCES)

    def test_profile_aliases(self):
        for alias in ("profile", "Profile", "DOMAIN_PROFILE"):
            assert _resolve_directory_sources([alias]) == [
                DIRECTORY_SOURCE_DOMAIN_PROFILE
            ]

    def test_contact_aliases(self):
        for alias in ("contact", "DOMAIN_CONTACT"):
            assert _resolve_directory_sources([alias]) == [
                DIRECTORY_SOURCE_DOMAIN_CONTACT
            ]

    def test_full_value_passthrough(self):
        assert _resolve_directory_sources([DIRECTORY_SOURCE_DOMAIN_PROFILE]) == [
            DIRECTORY_SOURCE_DOMAIN_PROFILE
        ]

    def test_unknown_source_raises(self):
        with pytest.raises(UserInputError):
            _resolve_directory_sources(["bogus"])


class TestListDirectoryPeople:
    def test_calls_api_with_default_params(self):
        svc = MagicMock()
        svc.people.return_value.listDirectoryPeople.return_value.execute.return_value = {
            "people": [_person("Alice", "alice@example.com")]
        }

        result = run(
            list_directory_people(service=svc, user_google_email="me@example.com")
        )

        kwargs = svc.people.return_value.listDirectoryPeople.call_args.kwargs
        assert kwargs["readMask"] == DIRECTORY_DEFAULT_PERSON_FIELDS
        assert kwargs["sources"] == list(DEFAULT_DIRECTORY_SOURCES)
        assert kwargs["pageSize"] == 100
        assert kwargs["mergeSources"] == [DIRECTORY_MERGE_SOURCE_CONTACT]
        assert "pageToken" not in kwargs
        assert "Alice" in result
        assert "alice@example.com" in result

    def test_merge_disabled_omits_param(self):
        svc = MagicMock()
        svc.people.return_value.listDirectoryPeople.return_value.execute.return_value = {
            "people": []
        }

        run(
            list_directory_people(
                service=svc,
                user_google_email="me@example.com",
                merge_contact_into_profile=False,
            )
        )

        kwargs = svc.people.return_value.listDirectoryPeople.call_args.kwargs
        assert "mergeSources" not in kwargs

    def test_clamps_page_size_to_1000(self):
        svc = MagicMock()
        svc.people.return_value.listDirectoryPeople.return_value.execute.return_value = {
            "people": []
        }

        run(
            list_directory_people(
                service=svc, user_google_email="me@example.com", page_size=5000
            )
        )

        kwargs = svc.people.return_value.listDirectoryPeople.call_args.kwargs
        assert kwargs["pageSize"] == 1000

    def test_rejects_zero_page_size(self):
        svc = MagicMock()
        with pytest.raises(UserInputError):
            run(
                list_directory_people(
                    service=svc,
                    user_google_email="me@example.com",
                    page_size=0,
                )
            )

    def test_passes_page_token(self):
        svc = MagicMock()
        svc.people.return_value.listDirectoryPeople.return_value.execute.return_value = {
            "people": []
        }

        run(
            list_directory_people(
                service=svc,
                user_google_email="me@example.com",
                page_token="tok123",
            )
        )

        kwargs = svc.people.return_value.listDirectoryPeople.call_args.kwargs
        assert kwargs["pageToken"] == "tok123"

    def test_uses_resolved_sources(self):
        svc = MagicMock()
        svc.people.return_value.listDirectoryPeople.return_value.execute.return_value = {
            "people": []
        }

        run(
            list_directory_people(
                service=svc,
                user_google_email="me@example.com",
                sources=["profile"],
            )
        )

        kwargs = svc.people.return_value.listDirectoryPeople.call_args.kwargs
        assert kwargs["sources"] == [DIRECTORY_SOURCE_DOMAIN_PROFILE]

    def test_includes_next_page_token_in_response(self):
        svc = MagicMock()
        svc.people.return_value.listDirectoryPeople.return_value.execute.return_value = {
            "people": [_person("Alice", "alice@example.com")],
            "nextPageToken": "next-tok",
        }

        result = run(
            list_directory_people(service=svc, user_google_email="me@example.com")
        )

        assert "Next page token: next-tok" in result

    def test_empty_result_message(self):
        svc = MagicMock()
        svc.people.return_value.listDirectoryPeople.return_value.execute.return_value = {}

        result = run(
            list_directory_people(service=svc, user_google_email="me@example.com")
        )

        assert "No directory people found" in result


class TestSearchDirectoryPeople:
    def test_passes_query_and_default_params(self):
        svc = MagicMock()
        svc.people.return_value.searchDirectoryPeople.return_value.execute.return_value = {
            "people": [_person("Bob", "bob@example.com")]
        }

        result = run(
            search_directory_people(
                service=svc,
                user_google_email="me@example.com",
                query="bob",
            )
        )

        kwargs = svc.people.return_value.searchDirectoryPeople.call_args.kwargs
        assert kwargs["query"] == "bob"
        assert kwargs["readMask"] == DIRECTORY_DEFAULT_PERSON_FIELDS
        assert kwargs["sources"] == list(DEFAULT_DIRECTORY_SOURCES)
        assert kwargs["pageSize"] == 50
        assert kwargs["mergeSources"] == [DIRECTORY_MERGE_SOURCE_CONTACT]
        assert "Bob" in result

    def test_rejects_empty_query(self):
        svc = MagicMock()
        with pytest.raises(UserInputError):
            run(
                search_directory_people(
                    service=svc,
                    user_google_email="me@example.com",
                    query="   ",
                )
            )

    def test_clamps_page_size_to_500(self):
        svc = MagicMock()
        svc.people.return_value.searchDirectoryPeople.return_value.execute.return_value = {
            "people": []
        }

        run(
            search_directory_people(
                service=svc,
                user_google_email="me@example.com",
                query="x",
                page_size=10000,
            )
        )

        kwargs = svc.people.return_value.searchDirectoryPeople.call_args.kwargs
        assert kwargs["pageSize"] == 500

    def test_merge_disabled_omits_param(self):
        svc = MagicMock()
        svc.people.return_value.searchDirectoryPeople.return_value.execute.return_value = {
            "people": []
        }

        run(
            search_directory_people(
                service=svc,
                user_google_email="me@example.com",
                query="test",
                merge_contact_into_profile=False,
            )
        )

        kwargs = svc.people.return_value.searchDirectoryPeople.call_args.kwargs
        assert "mergeSources" not in kwargs

    def test_passes_page_token(self):
        svc = MagicMock()
        svc.people.return_value.searchDirectoryPeople.return_value.execute.return_value = {
            "people": []
        }

        run(
            search_directory_people(
                service=svc,
                user_google_email="me@example.com",
                query="test",
                page_token="tok123",
            )
        )

        kwargs = svc.people.return_value.searchDirectoryPeople.call_args.kwargs
        assert kwargs["pageToken"] == "tok123"

    def test_rejects_zero_page_size(self):
        svc = MagicMock()
        with pytest.raises(UserInputError):
            run(
                search_directory_people(
                    service=svc,
                    user_google_email="me@example.com",
                    query="test",
                    page_size=0,
                )
            )

    def test_empty_result_message(self):
        svc = MagicMock()
        svc.people.return_value.searchDirectoryPeople.return_value.execute.return_value = {}

        result = run(
            search_directory_people(
                service=svc,
                user_google_email="me@example.com",
                query="nobody",
            )
        )

        assert "No directory people found matching 'nobody'" in result
