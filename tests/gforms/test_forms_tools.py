"""
Unit tests for Google Forms MCP tools

Tests the batch_update_form tool with mocked API responses
"""

import pytest
from unittest.mock import Mock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# Import internal implementation functions (not decorated tool wrappers)
from gforms.forms_tools import _batch_update_form_impl, _serialize_form_item, get_form


@pytest.mark.asyncio
async def test_batch_update_form_multiple_requests():
    """Test batch update with multiple requests returns formatted results"""
    mock_service = Mock()
    mock_response = {
        "replies": [
            {"createItem": {"itemId": "item001", "questionId": ["q001"]}},
            {"createItem": {"itemId": "item002", "questionId": ["q002"]}},
        ],
        "writeControl": {"requiredRevisionId": "rev123"},
    }

    mock_service.forms().batchUpdate().execute.return_value = mock_response

    requests = [
        {
            "createItem": {
                "item": {
                    "title": "What is your name?",
                    "questionItem": {
                        "question": {"textQuestion": {"paragraph": False}}
                    },
                },
                "location": {"index": 0},
            }
        },
        {
            "createItem": {
                "item": {
                    "title": "What is your email?",
                    "questionItem": {
                        "question": {"textQuestion": {"paragraph": False}}
                    },
                },
                "location": {"index": 1},
            }
        },
    ]

    result = await _batch_update_form_impl(
        service=mock_service,
        form_id="test_form_123",
        requests=requests,
    )

    assert "Batch Update Completed" in result
    assert "test_form_123" in result
    assert "Requests Applied: 2" in result
    assert "Replies Received: 2" in result
    assert "item001" in result
    assert "item002" in result


@pytest.mark.asyncio
async def test_batch_update_form_single_request():
    """Test batch update with a single request"""
    mock_service = Mock()
    mock_response = {
        "replies": [
            {"createItem": {"itemId": "item001", "questionId": ["q001"]}},
        ],
    }

    mock_service.forms().batchUpdate().execute.return_value = mock_response

    requests = [
        {
            "createItem": {
                "item": {
                    "title": "Favourite colour?",
                    "questionItem": {
                        "question": {
                            "choiceQuestion": {
                                "type": "RADIO",
                                "options": [
                                    {"value": "Red"},
                                    {"value": "Blue"},
                                ],
                            }
                        }
                    },
                },
                "location": {"index": 0},
            }
        },
    ]

    result = await _batch_update_form_impl(
        service=mock_service,
        form_id="single_form_456",
        requests=requests,
    )

    assert "single_form_456" in result
    assert "Requests Applied: 1" in result
    assert "Replies Received: 1" in result


@pytest.mark.asyncio
async def test_batch_update_form_empty_replies():
    """Test batch update when API returns no replies"""
    mock_service = Mock()
    mock_response = {
        "replies": [],
    }

    mock_service.forms().batchUpdate().execute.return_value = mock_response

    requests = [
        {
            "updateFormInfo": {
                "info": {"description": "Updated description"},
                "updateMask": "description",
            }
        },
    ]

    result = await _batch_update_form_impl(
        service=mock_service,
        form_id="info_form_789",
        requests=requests,
    )

    assert "info_form_789" in result
    assert "Requests Applied: 1" in result
    assert "Replies Received: 0" in result


@pytest.mark.asyncio
async def test_batch_update_form_no_replies_key():
    """Test batch update when API response lacks replies key"""
    mock_service = Mock()
    mock_response = {}

    mock_service.forms().batchUpdate().execute.return_value = mock_response

    requests = [
        {
            "updateSettings": {
                "settings": {"quizSettings": {"isQuiz": True}},
                "updateMask": "quizSettings.isQuiz",
            }
        },
    ]

    result = await _batch_update_form_impl(
        service=mock_service,
        form_id="quiz_form_000",
        requests=requests,
    )

    assert "quiz_form_000" in result
    assert "Requests Applied: 1" in result
    assert "Replies Received: 0" in result


@pytest.mark.asyncio
async def test_batch_update_form_url_in_response():
    """Test that the edit URL is included in the response"""
    mock_service = Mock()
    mock_response = {
        "replies": [{}],
    }

    mock_service.forms().batchUpdate().execute.return_value = mock_response

    requests = [
        {"updateFormInfo": {"info": {"title": "New Title"}, "updateMask": "title"}}
    ]

    result = await _batch_update_form_impl(
        service=mock_service,
        form_id="url_form_abc",
        requests=requests,
    )

    assert "https://docs.google.com/forms/d/url_form_abc/edit" in result


@pytest.mark.asyncio
async def test_batch_update_form_mixed_reply_types():
    """Test batch update with createItem replies containing different fields"""
    mock_service = Mock()
    mock_response = {
        "replies": [
            {"createItem": {"itemId": "item_a", "questionId": ["qa"]}},
            {},
            {"createItem": {"itemId": "item_c"}},
        ],
    }

    mock_service.forms().batchUpdate().execute.return_value = mock_response

    requests = [
        {"createItem": {"item": {"title": "Q1"}, "location": {"index": 0}}},
        {
            "updateFormInfo": {
                "info": {"description": "Desc"},
                "updateMask": "description",
            }
        },
        {"createItem": {"item": {"title": "Q2"}, "location": {"index": 1}}},
    ]

    result = await _batch_update_form_impl(
        service=mock_service,
        form_id="mixed_form_xyz",
        requests=requests,
    )

    assert "Requests Applied: 3" in result
    assert "Replies Received: 3" in result
    assert "item_a" in result
    assert "item_c" in result


def test_serialize_form_item_choice_question_includes_ids_and_options():
    """Choice question items should expose questionId/options/type metadata."""
    item = {
        "itemId": "item_123",
        "title": "Favorite color?",
        "questionItem": {
            "question": {
                "questionId": "q_123",
                "required": True,
                "choiceQuestion": {
                    "type": "RADIO",
                    "options": [{"value": "Red"}, {"value": "Blue"}],
                },
            }
        },
    }

    serialized = _serialize_form_item(item, 1)

    assert serialized["index"] == 1
    assert serialized["itemId"] == "item_123"
    assert serialized["type"] == "RADIO"
    assert serialized["questionId"] == "q_123"
    assert serialized["required"] is True
    assert serialized["options"] == [{"value": "Red"}, {"value": "Blue"}]


def test_serialize_form_item_grid_includes_row_and_column_structure():
    """Grid question groups should expose row labels/IDs and column options."""
    item = {
        "itemId": "grid_item_1",
        "title": "Weekly chores",
        "questionGroupItem": {
            "questions": [
                {
                    "questionId": "row_q1",
                    "required": True,
                    "rowQuestion": {"title": "Laundry"},
                },
                {
                    "questionId": "row_q2",
                    "required": False,
                    "rowQuestion": {"title": "Dishes"},
                },
            ],
            "grid": {"columns": {"options": [{"value": "Never"}, {"value": "Often"}]}},
        },
    }

    serialized = _serialize_form_item(item, 2)

    assert serialized["index"] == 2
    assert serialized["type"] == "GRID"
    assert serialized["grid"]["columns"] == [{"value": "Never"}, {"value": "Often"}]
    assert serialized["grid"]["rows"] == [
        {"title": "Laundry", "questionId": "row_q1", "required": True},
        {"title": "Dishes", "questionId": "row_q2", "required": False},
    ]


@pytest.mark.asyncio
async def test_get_form_returns_structured_item_metadata():
    """get_form should include question IDs, options, and grid structure."""
    mock_service = Mock()
    mock_service.forms().get().execute.return_value = {
        "formId": "form_1",
        "info": {"title": "Survey", "description": "Test survey"},
        "items": [
            {
                "itemId": "item_1",
                "title": "Favorite fruit?",
                "questionItem": {
                    "question": {
                        "questionId": "q_1",
                        "required": True,
                        "choiceQuestion": {
                            "type": "RADIO",
                            "options": [{"value": "Apple"}, {"value": "Banana"}],
                        },
                    }
                },
            },
            {
                "itemId": "item_2",
                "title": "Household chores",
                "questionGroupItem": {
                    "questions": [
                        {
                            "questionId": "row_1",
                            "required": True,
                            "rowQuestion": {"title": "Laundry"},
                        }
                    ],
                    "grid": {"columns": {"options": [{"value": "Never"}]}},
                },
            },
        ],
    }

    # Bypass decorators and call the core implementation directly.
    result = await get_form.__wrapped__.__wrapped__(
        mock_service, "user@example.com", "form_1"
    )

    assert "- Items (structured):" in result
    assert '"questionId": "q_1"' in result
    assert '"options": [' in result
    assert '"Apple"' in result
    assert '"type": "GRID"' in result
    assert '"columns": [' in result
    assert '"rows": [' in result
