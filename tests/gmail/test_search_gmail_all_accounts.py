"""search_gmail_messages with user_google_email="all" (a unified inbox)."""

from email.utils import format_datetime, parsedate_to_datetime

import pytest

import gmail.gmail_tools as gmail_tools
from auth.multi_account import AccountService
from core.utils import UserInputError
from gmail.gmail_tools import search_gmail_messages
from tests.gmail.test_search_gmail_messages_headers import (
    _build_service,
    _headers,
    _list_response,
    _unwrap,
)


def _account(email, received, next_page_token=None, date_headers=None):
    """An account whose search returns ``received``: [(id, RFC 2822 time)]."""
    date_headers = date_headers or {}
    ids = [mid for mid, _ in received]
    responses = {}
    for mid, when in received:
        epoch_ms = int(parsedate_to_datetime(when).timestamp() * 1000)
        responses[(mid, "metadata")] = {
            "id": mid,
            "internalDate": str(epoch_ms),
            "payload": {
                "headers": _headers(
                    Subject=f"subject {mid}", Date=date_headers.get(mid, when)
                )
            },
        }
    return AccountService(
        email=email,
        service=_build_service(
            list_response=_list_response(ids, next_page_token),
            message_responses=responses,
        ),
    )


async def _search(accounts, **kwargs):
    return await _unwrap(search_gmail_messages)(
        service=accounts, query="in:inbox", user_google_email="all", **kwargs
    )


@pytest.mark.asyncio
async def test_results_from_every_account_are_interleaved_newest_first():
    accounts = [
        _account(
            "personal@example.com",
            [
                ("p-new", "Tue, 13 Oct 2026 09:00:00 -0400"),
                ("p-old", "Sun, 11 Oct 2026 09:00:00 -0400"),
            ],
        ),
        _account("work@example.com", [("w-mid", "Mon, 12 Oct 2026 12:00:00 +0000")]),
    ]

    result = await _search(accounts)

    assert result.index("p-new") < result.index("w-mid") < result.index("p-old")
    assert "across personal@example.com, work@example.com" in result
    assert "Account: work@example.com\n     Message ID: w-mid" in result
    assert "Subject: subject w-mid" in result
    assert "https://mail.google.com/mail/u/work@example.com/#all/w-mid" in result
    assert "More matches" not in result


@pytest.mark.asyncio
async def test_order_follows_received_time_not_the_date_header():
    future = format_datetime(parsedate_to_datetime("Fri, 1 Jan 2038 00:00:00 +0000"))
    accounts = [
        _account(
            "personal@example.com",
            [("spam", "Sun, 11 Oct 2026 09:00:00 -0400")],
            date_headers={"spam": future},
        ),
        _account("work@example.com", [("real", "Tue, 13 Oct 2026 09:00:00 -0400")]),
    ]

    result = await _search(accounts)

    assert result.index("real") < result.index("spam")


@pytest.mark.asyncio
async def test_page_size_caps_the_merged_list_and_names_accounts_with_more():
    accounts = [
        _account("personal@example.com", [("p1", "Tue, 13 Oct 2026 09:00:00 -0400")]),
        _account(
            "work@example.com",
            [("w1", "Mon, 12 Oct 2026 09:00:00 -0400")],
            next_page_token="more",
        ),
        _account("school@example.com", [("s1", "Sat, 10 Oct 2026 09:00:00 -0400")]),
    ]

    result = await _search(accounts, page_size=2)

    assert "Found 2 messages" in result
    assert "s1" not in result
    assert "More matches exist in school@example.com, work@example.com" in result
    list_kwargs = accounts[0].service.users().messages().list.call_args.kwargs
    assert list_kwargs == {"userId": "me", "q": "in:inbox", "maxResults": 2}


@pytest.mark.asyncio
async def test_a_signed_out_account_is_named_and_the_rest_still_returned():
    accounts = [
        AccountService(email="old@example.com", error="signed out"),
        _account("personal@example.com", [("p1", "Tue, 13 Oct 2026 09:00:00 -0400")]),
    ]

    result = await _search(accounts)

    assert "p1" in result
    assert "Accounts not included:\n- old@example.com: signed out" in result


@pytest.mark.asyncio
async def test_header_failure_keeps_the_accounts_results(monkeypatch):
    async def failing_headers(*args, **kwargs):
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(gmail_tools, "_fetch_search_result_headers", failing_headers)
    accounts = [
        _account("personal@example.com", [("p1", "Tue, 13 Oct 2026 09:00:00 -0400")])
    ]

    result = await _search(accounts)

    assert "Message ID: p1" in result
    assert "Headers: unavailable" in result
    assert "Accounts not included" not in result


@pytest.mark.asyncio
async def test_page_token_is_refused():
    with pytest.raises(UserInputError):
        await _search([], page_token="abc")


@pytest.mark.asyncio
async def test_undated_rows_keep_their_place_in_their_accounts_list(monkeypatch):
    real_fetch = gmail_tools._fetch_search_result_headers

    async def flaky_headers(service, ids, internal_dates=None):
        if ids == ["w-new"]:
            raise RuntimeError("metadata unavailable")
        return await real_fetch(service, ids, internal_dates)

    monkeypatch.setattr(gmail_tools, "_fetch_search_result_headers", flaky_headers)
    accounts = [
        _account(
            "personal@example.com",
            [
                ("p-11", "Sun, 11 Oct 2026 09:00:00 -0400"),
                ("p-10", "Sat, 10 Oct 2026 09:00:00 -0400"),
            ],
        ),
        _account("work@example.com", [("w-new", "Tue, 13 Oct 2026 09:00:00 -0400")]),
    ]

    result = await _search(accounts, page_size=2)

    assert "w-new" in result and "p-11" in result
    assert "p-10" not in result
