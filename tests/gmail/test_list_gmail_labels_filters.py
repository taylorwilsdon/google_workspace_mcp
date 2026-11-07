"""Tests for the prefix, compact and include_system options on list_gmail_labels.

The defaults must keep returning the exact formatted output existing callers
already parse, so every option test is paired with a default-behavior test.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from gmail.gmail_tools import list_gmail_labels


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _labels_response() -> dict:
    return {
        "labels": [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "SENT", "name": "SENT", "type": "system"},
            {"id": "Label_9", "name": "Clients/Beta Corp", "type": "user"},
            {"id": "Label_3", "name": "Clients/Acme Co", "type": "user"},
            {"id": "Label_7", "name": "Vendors/Gamma LLC", "type": "user"},
        ]
    }


def _build_mock_service(response: dict) -> MagicMock:
    service = MagicMock()
    service.users.return_value.labels.return_value.list.return_value.execute.return_value = response
    return service


async def _call(**kwargs):
    service = _build_mock_service(_labels_response())
    return await _unwrap(list_gmail_labels)(
        service=service, user_google_email="user@example.com", **kwargs
    )


class TestDefaults:
    @pytest.mark.asyncio
    async def test_default_returns_formatted_text_with_every_label(self):
        """Pins the exact default output, so a change to the heading, the
        grouping, the ordering or the whitespace fails here rather than
        silently breaking callers that parse this string."""
        result = await _call()
        assert result == "\n".join(
            [
                "Found 5 labels:",
                "",
                "\U0001f4c2 SYSTEM LABELS:",
                "  • INBOX (ID: INBOX)",
                "  • SENT (ID: SENT)",
                "",
                "\U0001f3f7️  USER LABELS:",
                "  • Clients/Beta Corp (ID: Label_9)",
                "  • Clients/Acme Co (ID: Label_3)",
                "  • Vendors/Gamma LLC (ID: Label_7)",
            ]
        )

    @pytest.mark.asyncio
    async def test_default_no_labels_message_unchanged(self):
        service = _build_mock_service({"labels": []})
        result = await _unwrap(list_gmail_labels)(
            service=service, user_google_email="user@example.com"
        )
        assert result == "No labels found."


class TestPrefix:
    @pytest.mark.asyncio
    async def test_prefix_returns_only_the_matching_subtree(self):
        result = await _call(prefix="Clients/")
        assert "Found 2 labels:" in result
        assert "Clients/Acme Co" in result
        assert "Clients/Beta Corp" in result
        assert "Vendors/Gamma LLC" not in result
        assert "INBOX" not in result

    @pytest.mark.asyncio
    async def test_prefix_is_case_sensitive(self):
        result = await _call(prefix="clients/")
        assert "No labels found with prefix" in result

    @pytest.mark.asyncio
    async def test_prefix_miss_names_the_prefix_in_the_message(self):
        result = await _call(prefix="Nope/")
        assert result == "No labels found with prefix 'Nope/'."


class TestCompact:
    @pytest.mark.asyncio
    async def test_compact_returns_parseable_json_sorted_by_name(self):
        payload = json.loads(await _call(compact=True))
        assert payload["count"] == 5
        assert [label["name"] for label in payload["labels"]] == sorted(
            label["name"] for label in _labels_response()["labels"]
        )
        assert set(payload["labels"][0].keys()) == {"id", "name"}

    @pytest.mark.asyncio
    async def test_compact_preserves_the_id_for_each_name(self):
        payload = json.loads(await _call(compact=True))
        by_name = {label["name"]: label["id"] for label in payload["labels"]}
        assert by_name["Clients/Acme Co"] == "Label_3"
        assert by_name["Vendors/Gamma LLC"] == "Label_7"

    @pytest.mark.asyncio
    async def test_compact_with_empty_result_is_still_valid_json(self):
        payload = json.loads(await _call(compact=True, prefix="Nope/"))
        assert payload == {"count": 0, "labels": []}


class TestIncludeSystem:
    @pytest.mark.asyncio
    async def test_include_system_false_drops_system_labels(self):
        payload = json.loads(await _call(compact=True, include_system=False))
        assert payload["count"] == 3
        assert all(not label["id"].isupper() for label in payload["labels"])

    @pytest.mark.asyncio
    async def test_options_compose(self):
        payload = json.loads(
            await _call(compact=True, include_system=False, prefix="Clients/")
        )
        assert payload["count"] == 2
        assert [label["name"] for label in payload["labels"]] == [
            "Clients/Acme Co",
            "Clients/Beta Corp",
        ]
