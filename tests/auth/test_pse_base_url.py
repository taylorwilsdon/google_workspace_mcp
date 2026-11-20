"""GOOGLE_PSE_BASE_URL moves Custom Search requests and keeps the default."""

import pytest
from google.auth.credentials import AnonymousCredentials
from googleapiclient.discovery import Resource
from unittest.mock import MagicMock

from auth.google_auth import build_google_service, get_pse_base_url


_ENV = "GOOGLE_PSE_BASE_URL"


def _list_uri(service: Resource) -> str:
    return service.cse().list(q="test", cx="engine", key="api-key").uri


def test_custom_search_uses_google_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    credentials = AnonymousCredentials()
    service = build_google_service("customsearch", "v1", credentials)

    assert _list_uri(service).startswith(
        "https://customsearch.googleapis.com/customsearch/v1?"
    )
    assert service._http.credentials is credentials


@pytest.mark.parametrize(
    "raw", ["https://search.example.com", " https://search.example.com/ "]
)
def test_custom_search_uses_configured_base_url(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv(_ENV, raw)
    user_credentials = MagicMock()
    service = build_google_service("customsearch", "v1", user_credentials)

    uri = _list_uri(service)
    assert uri.startswith("https://search.example.com/customsearch/v1?")
    assert "key=api-key" in uri and "cx=engine" in uri
    # The user's Google credentials stay with Google.
    assert isinstance(service._http.credentials, AnonymousCredentials)


def test_base_url_does_not_move_other_services(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "https://search.example.com")
    service = build_google_service("gmail", "v1", AnonymousCredentials())

    request = service.users().getProfile(userId="me")
    assert request.uri.startswith("https://gmail.googleapis.com/")


def test_pse_base_url_is_none_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "  ")
    assert get_pse_base_url() is None


@pytest.mark.parametrize(
    "raw",
    [
        "search.example.com",
        "ftp://search.example.com",
        "https://",
        "https:///customsearch",
        "https://search.example.com/?tenant=acme",
        "https://search.example.com/#top",
    ],
)
def test_pse_base_url_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv(_ENV, raw)
    with pytest.raises(ValueError, match=_ENV):
        get_pse_base_url()
