"""Tests for resolving semantic anchors to Google Docs indices."""

import sys
import os

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gdocs.docs_anchors import (
    AnchorResolutionError,
    resolve_operation_anchor,
)


def _paragraph(start_index, text, named_style="NORMAL_TEXT"):
    return {
        "startIndex": start_index,
        "endIndex": start_index + len(text),
        "paragraph": {
            "elements": [
                {
                    "startIndex": start_index,
                    "endIndex": start_index + len(text),
                    "textRun": {"content": text},
                }
            ],
            "paragraphStyle": {"namedStyleType": named_style},
        },
    }


# "Intro\n" 1-7, "Results\n" (heading) 7-15, "Numbers went up.\n" 15-32
BODY = {
    "content": [
        _paragraph(1, "Intro\n"),
        _paragraph(7, "Results\n", "HEADING_2"),
        _paragraph(15, "Numbers went up.\n"),
    ]
}


def test_after_heading_resolves_past_the_heading_paragraph():
    assert resolve_operation_anchor(BODY, after_heading="Results") == 15


def test_before_heading_resolves_to_the_heading_start():
    assert resolve_operation_anchor(BODY, before_heading="Results") == 7


def test_heading_match_ignores_case_and_surrounding_whitespace():
    assert resolve_operation_anchor(BODY, after_heading="  results  ") == 15


def test_non_heading_paragraph_is_not_matched_as_a_heading():
    with pytest.raises(AnchorResolutionError, match="was not found"):
        resolve_operation_anchor(BODY, after_heading="Intro")


def test_anchor_text_after_resolves_past_the_match():
    assert (
        resolve_operation_anchor(BODY, anchor_text="Numbers", anchor_position="after")
        == 22
    )


def test_anchor_text_before_resolves_to_the_match_start():
    assert (
        resolve_operation_anchor(BODY, anchor_text="went", anchor_position="before")
        == 23
    )


def test_ambiguous_anchor_is_refused_rather_than_guessed():
    body = {
        "content": [
            _paragraph(1, "Draft\n", "HEADING_1"),
            _paragraph(7, "Draft\n", "HEADING_1"),
        ]
    }
    with pytest.raises(AnchorResolutionError, match="matches 2 locations"):
        resolve_operation_anchor(body, after_heading="Draft")


def test_missing_anchor_text_is_reported():
    with pytest.raises(AnchorResolutionError, match="was not found"):
        resolve_operation_anchor(BODY, anchor_text="nowhere")


def test_offsets_account_for_non_text_elements():
    # An inline object occupies one index before the text run.
    body = {
        "content": [
            {
                "startIndex": 1,
                "endIndex": 8,
                "paragraph": {
                    "elements": [
                        {"startIndex": 1, "endIndex": 2, "inlineObjectElement": {}},
                        {
                            "startIndex": 2,
                            "endIndex": 8,
                            "textRun": {"content": "Alpha\n"},
                        },
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                },
            }
        ]
    }
    assert (
        resolve_operation_anchor(body, anchor_text="Alpha", anchor_position="before")
        == 2
    )


@pytest.mark.asyncio
async def test_batch_update_doc_resolves_an_anchor_into_an_index():
    """The anchor must reach the Docs API as a concrete index."""
    from unittest.mock import Mock
    from gdocs import docs_tools

    def _unwrap(tool):
        fn = tool.fn if hasattr(tool, "fn") else tool
        while hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__
        return fn

    service = Mock()
    service.documents.return_value.get.return_value.execute = Mock(
        return_value={"body": BODY, "revisionId": "rev1"}
    )
    service.documents.return_value.batchUpdate.return_value.execute = Mock(
        return_value={"replies": [{}]}
    )

    result = await _unwrap(docs_tools.batch_update_doc)(
        service=service,
        user_google_email="user@example.com",
        document_id="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd",
        operations=[
            {"type": "insert_text", "after_heading": "Results", "text": "New line\n"}
        ],
    )

    assert "Error" not in result
    requests = service.documents.return_value.batchUpdate.call_args.kwargs["body"][
        "requests"
    ]
    assert requests[0]["insertText"]["location"]["index"] == 15


@pytest.mark.asyncio
async def test_batch_update_doc_refuses_an_ambiguous_anchor():
    from unittest.mock import Mock
    from gdocs import docs_tools

    def _unwrap(tool):
        fn = tool.fn if hasattr(tool, "fn") else tool
        while hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__
        return fn

    body = {
        "content": [
            _paragraph(1, "Draft\n", "HEADING_1"),
            _paragraph(7, "Draft\n", "HEADING_1"),
        ]
    }
    service = Mock()
    service.documents.return_value.get.return_value.execute = Mock(
        return_value={"body": body}
    )
    service.documents.return_value.batchUpdate.return_value.execute = Mock(
        return_value={"replies": [{}]}
    )

    result = await _unwrap(docs_tools.batch_update_doc)(
        service=service,
        user_google_email="user@example.com",
        document_id="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd",
        operations=[
            {"type": "insert_text", "after_heading": "Draft", "text": "New line\n"}
        ],
    )

    assert "matches 2 locations" in result
    service.documents.return_value.batchUpdate.assert_not_called()


def _batch_service(doc):
    from unittest.mock import Mock

    service = Mock()
    service.documents.return_value.get.return_value.execute.return_value = doc
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": [{}, {}]
    }
    return service


@pytest.mark.asyncio
async def test_two_anchored_insertions_keep_their_original_targets():
    from gdocs.managers.batch_operation_manager import BatchOperationManager

    service = _batch_service({"body": BODY})
    operations = [
        {"type": "insert_text", "anchor_text": "Intro", "text": " FIRST"},
        {"type": "insert_text", "anchor_text": "Numbers", "text": " SECOND"},
    ]
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc123", operations
    )
    assert success, message
    requests = service.documents.return_value.batchUpdate.call_args.kwargs["body"][
        "requests"
    ]
    text = "Intro\nResults\nNumbers went up.\n"
    for request in requests:
        insertion = request["insertText"]
        offset = insertion["location"]["index"] - 1
        text = text[:offset] + insertion["text"] + text[offset:]
    assert text == "Intro FIRST\nResults\nNumbers SECOND went up.\n"
    assert operations[0]["anchor_text"] == "Intro"
    assert "index" not in operations[0]


@pytest.mark.asyncio
async def test_anchored_insertions_cannot_follow_unresolved_mutations():
    from gdocs.managers.batch_operation_manager import BatchOperationManager

    service = _batch_service({"body": BODY})
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc123",
        [
            {"type": "delete_text", "start_index": 1, "end_index": 7},
            {"type": "insert_text", "anchor_text": "Numbers", "text": " SECOND"},
        ],
    )
    assert not success
    assert "cannot be mixed" in message
    service.documents.return_value.batchUpdate.assert_not_called()


INSERTIONS = [
    {"type": "insert_text", "text": "X"},
    {"type": "insert_table", "rows": 1, "columns": 1},
    {"type": "insert_page_break"},
    {"type": "insert_section_break"},
    {"type": "insert_image", "image_uri": "https://example.com/image.png"},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", INSERTIONS)
async def test_public_insertion_schemas_preserve_anchors_through_execution(operation):
    from pydantic import TypeAdapter
    from gdocs.operation_schemas import BatchDocOperation
    from gdocs.managers.batch_operation_manager import BatchOperationManager

    model = TypeAdapter(BatchDocOperation).validate_python(
        {**operation, "after_heading": "Results"}
    )
    assert {
        "after_heading",
        "before_heading",
        "anchor_text",
        "anchor_position",
    } <= model.model_json_schema()["properties"].keys()
    service = _batch_service({"body": BODY})
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc123", [model.model_dump(exclude_none=True)]
    )
    assert success, message
    requests = service.documents.return_value.batchUpdate.call_args.kwargs["body"][
        "requests"
    ]
    assert next(iter(requests[0].values()))["location"]["index"] == 15


@pytest.mark.parametrize("operation", INSERTIONS)
@pytest.mark.parametrize(
    "location",
    [
        {},
        {"index": 1, "after_heading": "Results"},
        {"end_of_segment": True, "anchor_text": "Intro"},
        {"before_heading": "Results", "anchor_text": "Intro"},
    ],
)
def test_insertion_location_validation_matches_public_schema(operation, location):
    from pydantic import TypeAdapter, ValidationError
    from gdocs.operation_schemas import BatchDocOperation
    from gdocs.docs_helpers import validate_operation

    op = {**operation, **location}
    with pytest.raises(ValidationError, match="exactly one"):
        TypeAdapter(BatchDocOperation).validate_python(op)
    valid, message = validate_operation(op)
    assert not valid
    assert "exactly one" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("position, expected_index", [("before", 4), ("after", 10)])
async def test_non_bmp_before_anchor_uses_utf16_in_generated_location(
    position, expected_index
):
    from gdocs.managers.batch_operation_manager import BatchOperationManager

    body = {"content": [_paragraph(1, "😀 target\n")]}
    body["content"][0]["endIndex"] = 11
    body["content"][0]["paragraph"]["elements"][0]["endIndex"] = 11
    service = _batch_service({"body": body})
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc123",
        [
            {
                "type": "insert_text",
                "anchor_text": "target",
                "anchor_position": position,
                "text": "X",
            }
        ],
    )
    assert success, message
    requests = service.documents.return_value.batchUpdate.call_args.kwargs["body"][
        "requests"
    ]
    assert requests[0]["insertText"]["location"]["index"] == expected_index
