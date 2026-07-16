"""Tab-selection guardrails for document structure inspection."""

import json
from unittest.mock import Mock

import pytest

from gdocs import docs_tools


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _service_with_document(document):
    service = Mock()
    service.documents.return_value.get.return_value.execute.return_value = document
    return service


def _payload(result):
    return json.loads(result.split("\n\n", 1)[1].rsplit("\n\nLink:", 1)[0])


@pytest.mark.asyncio
async def test_single_document_tab_is_selected_automatically():
    service = _service_with_document(
        {
            "title": "Single tab",
            "body": {"content": []},
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.0", "title": "Main"},
                    "documentTab": {
                        "body": {
                            "content": [
                                {
                                    "startIndex": 1,
                                    "endIndex": 13,
                                    "paragraph": {
                                        "elements": [
                                            {"textRun": {"content": "Tab content\n"}}
                                        ]
                                    },
                                }
                            ]
                        },
                        "namedRanges": {},
                        "headers": {},
                        "footers": {},
                        "documentStyle": {},
                    },
                }
            ],
        }
    )

    result = await _unwrap(docs_tools.inspect_doc_structure)(
        service=service,
        user_google_email="user@example.com",
        document_id="d" * 25,
        detailed=True,
    )

    parsed = _payload(result)
    assert parsed["inspected_tab_id"] == "t.0"
    assert parsed["tab_selection"] == "automatic_single_document_tab"
    assert parsed["total_length"] == 13
    assert parsed["elements"][0]["text_preview"] == "Tab content\n"
    assert parsed["tabs"] == [{"title": "Main", "tab_id": "t.0"}]


@pytest.mark.asyncio
async def test_multiple_document_tabs_require_explicit_tab_id():
    service = _service_with_document(
        {
            "title": "Multiple tabs",
            "body": {"content": []},
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.0", "title": "Main"},
                    "documentTab": {"body": {"content": []}},
                },
                {
                    "tabProperties": {"tabId": "t.1", "title": "Appendix"},
                    "documentTab": {"body": {"content": []}},
                },
            ],
        }
    )

    result = await _unwrap(docs_tools.inspect_doc_structure)(
        service=service,
        user_google_email="user@example.com",
        document_id="d" * 25,
        detailed=True,
    )

    assert result.startswith("Error:")
    assert "multiple document tabs" in result
    assert "t.0 (Main)" in result
    assert "t.1 (Appendix)" in result
    assert "tab_id" in result


@pytest.mark.asyncio
async def test_explicit_tab_id_remains_supported_for_multiple_tabs():
    service = _service_with_document(
        {
            "title": "Multiple tabs",
            "body": {"content": []},
            "tabs": [
                {
                    "tabProperties": {"tabId": "t.0", "title": "Main"},
                    "documentTab": {"body": {"content": []}},
                },
                {
                    "tabProperties": {"tabId": "t.1", "title": "Appendix"},
                    "documentTab": {
                        "body": {
                            "content": [
                                {
                                    "startIndex": 1,
                                    "endIndex": 10,
                                    "paragraph": {
                                        "elements": [
                                            {"textRun": {"content": "Appendix\n"}}
                                        ]
                                    },
                                }
                            ]
                        }
                    },
                },
            ],
        }
    )

    result = await _unwrap(docs_tools.inspect_doc_structure)(
        service=service,
        user_google_email="user@example.com",
        document_id="d" * 25,
        detailed=True,
        tab_id="t.1",
    )

    parsed = _payload(result)
    assert parsed["inspected_tab_id"] == "t.1"
    assert "tab_selection" not in parsed
    assert parsed["total_length"] == 10


@pytest.mark.asyncio
async def test_detailed_inspection_exposes_text_run_styles():
    service = _service_with_document(
        {
            "title": "Formatting",
            "body": {
                "content": [
                    {
                        "startIndex": 1,
                        "endIndex": 16,
                        "paragraph": {
                            "elements": [
                                {
                                    "startIndex": 1,
                                    "endIndex": 6,
                                    "textRun": {
                                        "content": "Bold ",
                                        "textStyle": {"bold": True},
                                    },
                                },
                                {
                                    "startIndex": 6,
                                    "endIndex": 15,
                                    "textRun": {
                                        "content": "and blue",
                                        "textStyle": {
                                            "italic": True,
                                            "foregroundColor": {
                                                "color": {
                                                    "rgbColor": {
                                                        "red": 0.1,
                                                        "green": 0.2,
                                                        "blue": 0.9,
                                                    }
                                                }
                                            },
                                        },
                                    },
                                },
                                {
                                    "startIndex": 15,
                                    "endIndex": 16,
                                    "textRun": {"content": "\n", "textStyle": {}},
                                },
                            ]
                        },
                    }
                ]
            },
            "tabs": [],
        }
    )

    result = await _unwrap(docs_tools.inspect_doc_structure)(
        service=service,
        user_google_email="user@example.com",
        document_id="d" * 25,
        detailed=True,
    )

    runs = _payload(result)["elements"][0]["text_runs"]
    assert runs[0] == {
        "start_index": 1,
        "end_index": 6,
        "text": "Bold ",
        "text_style": {"bold": True},
    }
    assert runs[1]["text_style"]["italic"] is True
    assert runs[1]["text_style"]["foregroundColor"]["color"]["rgbColor"] == {
        "red": 0.1,
        "green": 0.2,
        "blue": 0.9,
    }
