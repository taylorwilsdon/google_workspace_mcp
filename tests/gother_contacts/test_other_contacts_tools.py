"""
Unit tests for Google Other Contacts (People API) tools.

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

from gother_contacts import other_contacts_tools
from gother_contacts.other_contacts_tools import (
    _format_other_contact,
    search_other_contacts,
    list_other_contacts,
)


# ---- helpers ----------------------------------------------------------------
def _run(tool, *args):
    """Call a decorated People API tool: unwrap to the raw coroutine and run the
    Google API call synchronously (asyncio.to_thread just invokes the callable)."""
    raw = inspect.unwrap(tool)

    async def _fake_to_thread(fn, *a, **k):
        return fn(*a, **k)

    with patch.object(other_contacts_tools.asyncio, "to_thread", _fake_to_thread):
        return asyncio.run(raw(*args))


def _search_service(results):
    svc = MagicMock()
    svc.otherContacts.return_value.search.return_value.execute.return_value = {
        "results": results
    }
    return svc


def _search_mock(svc):
    return svc.otherContacts.return_value.search


def _list_service(other_contacts, next_page_token=None):
    resp = {"otherContacts": other_contacts}
    if next_page_token:
        resp["nextPageToken"] = next_page_token
    svc = MagicMock()
    svc.otherContacts.return_value.list.return_value.execute.return_value = resp
    return svc


def _list_mock(svc):
    return svc.otherContacts.return_value.list


def _person(name, emails, resource="otherContacts/c1"):
    return {
        "names": [{"displayName": name}],
        "emailAddresses": [{"value": e} for e in emails],
        "resourceName": resource,
    }


# ---- _format_other_contact --------------------------------------------------
def test_format_other_contact_basic():
    out = _format_other_contact(_person("Alice Adams", ["alice@example.com"]))
    assert "Alice Adams" in out
    assert "alice@example.com" in out
    assert "otherContacts/c1" in out


def test_format_other_contact_multiple_emails():
    out = _format_other_contact(_person("Multi", ["a@example.com", "b@example.com"]))
    assert "a@example.com" in out
    assert "b@example.com" in out


def test_format_other_contact_no_emails():
    out = _format_other_contact({"names": [{"displayName": "No Mail"}]})
    assert "No Mail" in out
    assert "(none)" in out


def test_format_other_contact_unknown_name():
    out = _format_other_contact({"emailAddresses": [{"value": "x@example.com"}]})
    assert "Unknown" in out
    assert "x@example.com" in out


# ---- search_other_contacts --------------------------------------------------
def test_search_other_contacts_returns_results():
    svc = _search_service(
        [
            {
                "person": _person(
                    "Alice Adams", ["alice@example.com"], "otherContacts/c1"
                )
            },
            {"person": _person("Bob Brown", ["bob@example.com"], "otherContacts/c2")},
        ]
    )
    out = _run(search_other_contacts, svc, "user@example.com", "test", 5)
    assert "Alice Adams" in out
    assert "alice@example.com" in out
    assert "Bob Brown" in out
    assert "bob@example.com" in out
    assert _search_mock(svc).call_args.kwargs["readMask"] == "names,emailAddresses"


def test_search_other_contacts_empty_results():
    svc = _search_service([])
    out = _run(search_other_contacts, svc, "user@example.com", "nobody", 5)
    assert "No other contacts found" in out


def test_search_other_contacts_page_size_capped_at_30():
    svc = _search_service([])
    _run(search_other_contacts, svc, "user@example.com", "test", 100)
    assert _search_mock(svc).call_args.kwargs["pageSize"] == 30


def test_search_other_contacts_no_page_token():
    params = inspect.signature(inspect.unwrap(search_other_contacts)).parameters
    assert "page_token" not in params
    assert "page_size" in params


def test_search_other_contacts_multiple_emails():
    svc = _search_service(
        [
            {
                "person": _person(
                    "Multi", ["a@example.com", "b@example.com"], "otherContacts/c3"
                )
            }
        ]
    )
    out = _run(search_other_contacts, svc, "user@example.com", "multi", 5)
    assert "a@example.com" in out
    assert "b@example.com" in out


def test_search_other_contacts_warms_cache_first():
    # Unique email so the module-global warmup cache is cold for this test.
    svc = _search_service([])
    _run(search_other_contacts, svc, "warmup-probe@example.com", "q", 5)
    calls = _search_mock(svc).call_args_list
    assert len(calls) == 2  # empty-query warmup, then the real search
    assert calls[0].kwargs["query"] == ""
    assert calls[0].kwargs["pageSize"] == 1
    assert calls[-1].kwargs["query"] == "q"


# ---- list_other_contacts ----------------------------------------------------
def test_list_other_contacts_returns_results():
    svc = _list_service(
        [_person("Carol Clark", ["carol@example.com"], "otherContacts/c4")],
        next_page_token="TOKEN123",
    )
    out = _run(list_other_contacts, svc, "user@example.com", 5)
    assert "Carol Clark" in out
    assert "carol@example.com" in out
    assert "TOKEN123" in out
    assert _list_mock(svc).call_args.kwargs["readMask"] == "names,emailAddresses"


def test_list_other_contacts_pagination():
    svc = _list_service([])
    _run(list_other_contacts, svc, "user@example.com", 5, "abc123")
    assert _list_mock(svc).call_args.kwargs["pageToken"] == "abc123"


def test_list_other_contacts_empty():
    svc = _list_service([])
    out = _run(list_other_contacts, svc, "user@example.com", 5)
    assert "No other contacts found" in out
