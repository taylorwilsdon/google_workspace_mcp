"""
Unit tests for Google Directory (People API) tools.

Tests the formatting helper directly, and the tool functions by unwrapping the
decorator chain to the raw coroutine and running the Google API call
synchronously against a mock service (no network, no auth).
"""

import sys
import os
import asyncio
import inspect
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gdirectory import directory_tools
from gdirectory.directory_tools import (
    _format_directory_person,
    search_directory_people,
    list_directory_people,
    DIRECTORY_SOURCES,
)


# ---- helpers ----------------------------------------------------------------
def _run(tool, *args):
    """Call a decorated People API tool: unwrap to the raw coroutine and run the
    Google API call synchronously (asyncio.to_thread just invokes the callable)."""
    raw = inspect.unwrap(tool)

    async def _fake_to_thread(fn, *a, **k):
        return fn(*a, **k)

    with patch.object(directory_tools.asyncio, "to_thread", _fake_to_thread):
        return asyncio.run(raw(*args))


def _search_service(people, next_page_token=None):
    resp = {"people": people}
    if next_page_token:
        resp["nextPageToken"] = next_page_token
    svc = MagicMock()
    svc.people.return_value.searchDirectoryPeople.return_value.execute.return_value = (
        resp
    )
    return svc


def _search_mock(svc):
    return svc.people.return_value.searchDirectoryPeople


def _list_service(people, next_page_token=None):
    resp = {"people": people}
    if next_page_token:
        resp["nextPageToken"] = next_page_token
    svc = MagicMock()
    svc.people.return_value.listDirectoryPeople.return_value.execute.return_value = resp
    return svc


def _list_mock(svc):
    return svc.people.return_value.listDirectoryPeople


def _person(name, emails, resource="people/c1"):
    return {
        "names": [{"displayName": name}],
        "emailAddresses": [{"value": e} for e in emails],
        "resourceName": resource,
    }


# ---- _format_directory_person -----------------------------------------------
def test_format_directory_person_basic():
    out = _format_directory_person(_person("Dana Diaz", ["dana@corp.com"]))
    assert "Dana Diaz" in out
    assert "dana@corp.com" in out
    assert "people/c1" in out


def test_format_directory_person_multiple_emails():
    out = _format_directory_person(_person("Multi", ["a@corp.com", "b@corp.com"]))
    assert "a@corp.com" in out
    assert "b@corp.com" in out


# ---- search_directory_people ------------------------------------------------
def test_search_directory_people_returns_results():
    svc = _search_service([_person("Erin East", ["erin@corp.com"], "people/c2")])
    out = _run(search_directory_people, svc, "user@corp.com", "erin", 5)
    assert "Erin East" in out
    assert "erin@corp.com" in out
    kwargs = _search_mock(svc).call_args.kwargs
    assert kwargs["readMask"] == "names,emailAddresses"
    assert kwargs["sources"] == ["DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE"]


def test_search_directory_people_empty_results():
    svc = _search_service([])
    out = _run(search_directory_people, svc, "user@corp.com", "nobody", 5)
    assert "No directory people found" in out


def test_search_directory_people_pagination():
    svc = _search_service(
        [_person("Erin East", ["erin@corp.com"], "people/c2")],
        next_page_token="NPT456",
    )
    out = _run(search_directory_people, svc, "user@corp.com", "erin", 5, "pgtok")
    assert _search_mock(svc).call_args.kwargs["pageToken"] == "pgtok"
    assert "NPT456" in out


def test_search_directory_people_multiple_emails():
    svc = _search_service([_person("Multi", ["a@corp.com", "b@corp.com"], "people/c3")])
    out = _run(search_directory_people, svc, "user@corp.com", "multi", 5)
    assert "a@corp.com" in out
    assert "b@corp.com" in out


# ---- list_directory_people --------------------------------------------------
def test_list_directory_people_returns_results():
    svc = _list_service([_person("Frank Frost", ["frank@corp.com"], "people/c4")])
    out = _run(list_directory_people, svc, "user@corp.com", 5)
    assert "Frank Frost" in out
    assert "frank@corp.com" in out
    kwargs = _list_mock(svc).call_args.kwargs
    assert kwargs["sources"] == ["DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE"]
    assert kwargs["readMask"] == "names,emailAddresses"


def test_list_directory_people_pagination():
    svc = _list_service(
        [_person("Frank Frost", ["frank@corp.com"], "people/c4")],
        next_page_token="NPT789",
    )
    out = _run(list_directory_people, svc, "user@corp.com", 5, "listtok")
    assert _list_mock(svc).call_args.kwargs["pageToken"] == "listtok"
    assert "NPT789" in out


def test_list_directory_people_empty():
    svc = _list_service([])
    out = _run(list_directory_people, svc, "user@corp.com", 5)
    assert "No directory people found" in out


# DIRECTORY_SOURCES is the single domain-profile source used by both tools.
def test_directory_sources_constant():
    assert DIRECTORY_SOURCES == ["DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE"]
