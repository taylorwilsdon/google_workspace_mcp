from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gmail.gmail_tools import list_gmail_thread_messages


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _msg(mid, **headers):
    return {
        "id": mid,
        "threadId": "t1",
        "labelIds": ["INBOX"],
        "internalDate": "1700000000000",
        "snippet": f"snippet {mid}",
        "payload": {"headers": [{"name": k, "value": v} for k, v in headers.items()]},
    }


def _service(messages):
    service = MagicMock()
    get = service.users.return_value.threads.return_value.get
    get.return_value.execute.return_value = {"id": "t1", "messages": messages}
    return service, get


async def _call(service, **kwargs):
    return await _unwrap(list_gmail_thread_messages)(
        service=service, thread_id="t1", user_google_email="u@example.com", **kwargs
    )


@pytest.mark.asyncio
async def test_multi_message_thread_returns_ids_labels_snippet_and_default_headers():
    service, get = _service(
        [
            _msg("m1", From="a@x.com", To="b@x.com", Date="d1", Subject="Hi", Cc="c"),
            _msg("m2", From="b@x.com", To="a@x.com", Date="d2", Subject="Re: Hi"),
        ]
    )

    result = await _call(service)

    assert result["thread_id"] == "t1"
    assert result["message_count"] == 2
    assert [m["id"] for m in result["messages"]] == ["m1", "m2"]
    first = result["messages"][0]
    assert first["label_ids"] == ["INBOX"]
    assert first["internal_date"] == "1700000000000"
    assert first["snippet"] == "snippet m1"
    assert first["headers"] == {
        "From": "a@x.com",
        "To": "b@x.com",
        "Date": "d1",
        "Subject": "Hi",
    }


@pytest.mark.asyncio
async def test_requests_metadata_format_with_only_requested_headers():
    service, get = _service([_msg("m1", **{"Message-ID": "<1@x>"})])

    await _call(service)

    get.assert_called_once_with(
        userId="me",
        id="t1",
        format="metadata",
        metadataHeaders=["From", "To", "Date", "Subject"],
    )


@pytest.mark.asyncio
async def test_custom_headers_are_requested_and_returned():
    service, get = _service(
        [
            _msg(
                "m2",
                **{
                    "Message-ID": "<2@x>",
                    "In-Reply-To": "<1@x>",
                    "References": "<1@x>",
                    "Cc": "c@x.com",
                    "From": "ignored@x.com",
                },
            )
        ]
    )
    names = ["Message-ID", "In-Reply-To", "References", "Cc"]

    result = await _call(service, headers=names)

    assert get.call_args.kwargs["metadataHeaders"] == names
    assert get.call_args.kwargs["format"] == "metadata"
    assert result["messages"][0]["headers"] == {
        "Message-ID": "<2@x>",
        "In-Reply-To": "<1@x>",
        "References": "<1@x>",
        "Cc": "c@x.com",
    }


@pytest.mark.asyncio
async def test_single_message_thread():
    service, _ = _service([_msg("only", From="a@x.com")])

    result = await _call(service)

    assert result["message_count"] == 1
    assert result["messages"][0]["id"] == "only"


@pytest.mark.asyncio
async def test_empty_thread_and_no_bodies_in_output():
    service, _ = _service([])

    result = await _call(service)

    assert result == {"thread_id": "t1", "message_count": 0, "messages": []}


@pytest.mark.asyncio
async def test_missing_optional_fields_default_cleanly():
    service, _ = _service([{"id": "m1", "payload": {}}])

    result = await _call(service)

    assert result["messages"][0] == {
        "id": "m1",
        "label_ids": [],
        "internal_date": None,
        "snippet": "",
        "headers": {},
    }


def test_registered_in_extended_tier():
    import yaml

    tiers_path = Path(__file__).resolve().parents[2] / "core" / "tool_tiers.yaml"
    with open(tiers_path) as f:
        tiers = yaml.safe_load(f)
    assert "list_gmail_thread_messages" in tiers["gmail"]["extended"]
