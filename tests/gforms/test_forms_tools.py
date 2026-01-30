"""
Unit tests for Google Forms MCP tools

Tests the batch_update_form tool with mocked API responses
"""

import pytest
from unittest.mock import Mock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# Import the internal implementation function (not the decorated one)
from gforms.forms_tools import _batch_update_form_impl


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
