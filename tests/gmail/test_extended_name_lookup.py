"""GMAIL_EXTENDED_NAME_LOOKUP gates the opt-in name scopes and People tiers."""

import base64
import importlib
from unittest.mock import Mock

import pytest

import auth.scopes as scopes
from auth.scopes import CONTACTS_OTHER_READONLY_SCOPE, DIRECTORY_READONLY_SCOPE
from core.config import is_extended_name_lookup_enabled
from gmail.gmail_tools import send_gmail_message

EXTRA = {CONTACTS_OTHER_READONLY_SCOPE, DIRECTORY_READONLY_SCOPE}


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.mark.parametrize(
    "value,expected",
    [(None, False), ("", False), ("0", False), ("1", True), (" TRUE ", True)],
)
def test_flag_parsing(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("GMAIL_EXTENDED_NAME_LOOKUP", raising=False)
    else:
        monkeypatch.setenv("GMAIL_EXTENDED_NAME_LOOKUP", value)
    assert is_extended_name_lookup_enabled() is expected


class TestScopes:
    def teardown_method(self):
        importlib.reload(scopes)

    def test_default_requests_no_new_scopes(self, monkeypatch):
        monkeypatch.delenv("GMAIL_EXTENDED_NAME_LOOKUP", raising=False)
        importlib.reload(scopes)
        assert not EXTRA & set(scopes.CONTACTS_SCOPES)
        assert not EXTRA & set(scopes.TOOL_READONLY_SCOPES_MAP["contacts"])

    def test_flag_on_requests_both_scopes(self, monkeypatch):
        monkeypatch.setenv("GMAIL_EXTENDED_NAME_LOOKUP", "1")
        importlib.reload(scopes)
        assert EXTRA <= set(scopes.CONTACTS_SCOPES)
        assert EXTRA <= set(scopes.TOOL_READONLY_SCOPES_MAP["contacts"])


def _gmail_service():
    service = Mock()
    service.users().messages().send().execute.return_value = {"id": "sent123"}
    service.users().settings().sendAs().list().execute.return_value = {"sendAs": []}
    return service


@pytest.mark.asyncio
async def test_flag_off_skips_other_contacts_and_directory(monkeypatch):
    monkeypatch.delenv("GMAIL_EXTENDED_NAME_LOOKUP", raising=False)
    gmail = _gmail_service()
    people = Mock()
    people.people().searchContacts().execute.return_value = {"results": []}

    result = await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="grace@example.org",
        to="stranger@example.com",
        subject="Hello",
        body="Hi",
        include_signature=False,
    )

    people.otherContacts().search.assert_not_called()
    people.people().searchDirectoryPeople.assert_not_called()
    raw = base64.urlsafe_b64decode(
        gmail.users().messages().send.call_args.kwargs["body"]["raw"]
    ).decode()
    assert "To: stranger@example.com" in raw
    assert "could not be resolved" not in result


@pytest.mark.asyncio
async def test_flag_off_note_names_only_contacts_scope(monkeypatch):
    monkeypatch.delenv("GMAIL_EXTENDED_NAME_LOOKUP", raising=False)
    result = await _unwrap(send_gmail_message)(
        service=_gmail_service(),
        people_service=None,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Hello",
        body="Hi",
        include_signature=False,
    )
    assert "contacts.readonly" in result
    assert "contacts.other.readonly" not in result
    assert "directory.readonly" not in result
