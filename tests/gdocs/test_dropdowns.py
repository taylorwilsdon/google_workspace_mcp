"""Native dropdown contracts, request routing and chip-preservation regressions."""

from copy import deepcopy
from pathlib import Path
import json
from unittest.mock import Mock

import pytest
from googleapiclient.discovery import build_from_document
from googleapiclient.discovery_cache import get_static_doc
from googleapiclient.http import HttpMockSequence
from pydantic import TypeAdapter, ValidationError

from gdocs import docs_tools
from gdocs.docs_helpers import validate_operation
from gdocs.managers.batch_operation_manager import BatchOperationManager
from gdocs.operation_schemas import BatchDocOperation, BatchDocOperations


CREATE = {
    "type": "create_dropdown_definition",
    "title": "Status",
    "dropdown_definition_id": "kix.status",
    "options": [
        {
            "display_value": "To do",
            "option_id": "dropdownItem.todo",
            "background_color": "#FF8000",
            "text_color": "#000000",
        },
        {
            "display_value": "Done",
            "option_id": "dropdownItem.done",
            "background_color": "#00ff00",
        },
    ],
}
INSERT = {"type": "insert_dropdown", "dropdown_definition_id": "kix.status", "index": 5}


def service_with_replies(replies=()):
    service = Mock()
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": list(replies)
    }
    service.documents.return_value.get.return_value.execute.return_value = {
        "revisionId": "r1",
        "tabs": [],
    }
    return service


def requests_from(service):
    return service.documents.return_value.batchUpdate.call_args.kwargs["body"][
        "requests"
    ]


@pytest.mark.asyncio
async def test_single_batch_creation_insertion_and_date_chip_are_native_and_ordered():
    operations = [
        CREATE,
        INSERT,
        {"type": "insert_date", "date": "2026-11-17", "end_of_segment": True},
    ]
    original = deepcopy(operations)
    service = service_with_replies()
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc", operations
    )
    assert success, message
    assert operations == original
    requests = requests_from(service)
    assert requests[:2] == [
        {
            "createDropdownDefinition": {
                "dropdownDefinition": {
                    "dropdownDefinitionId": "kix.status",
                    "dropdownDefinitionProperties": {
                        "title": "Status",
                        "options": [
                            {
                                "optionId": "dropdownItem.todo",
                                "displayValue": "To do",
                                "textStyle": {
                                    "backgroundColor": {
                                        "color": {
                                            "rgbColor": {
                                                "red": 1.0,
                                                "green": 128 / 255,
                                                "blue": 0.0,
                                            }
                                        }
                                    },
                                    "foregroundColor": {
                                        "color": {
                                            "rgbColor": {
                                                "red": 0.0,
                                                "green": 0.0,
                                                "blue": 0.0,
                                            }
                                        }
                                    },
                                },
                            },
                            {
                                "optionId": "dropdownItem.done",
                                "displayValue": "Done",
                                "textStyle": {
                                    "backgroundColor": {
                                        "color": {
                                            "rgbColor": {
                                                "red": 0.0,
                                                "green": 1.0,
                                                "blue": 0.0,
                                            }
                                        }
                                    },
                                },
                            },
                        ],
                    },
                }
            }
        },
        {
            "insertDropdown": {
                "dropdownDefinitionId": "kix.status",
                "location": {"index": 5},
            }
        },
    ]
    assert (
        requests[2]["insertDate"]["dateElementProperties"]["timestamp"]
        == "2026-11-17T12:00:00Z"
    )
    assert service.documents.return_value.batchUpdate.call_count == 1
    assert not any("deleteContentRange" in r or "insertText" in r for r in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("append", [False, True])
@pytest.mark.parametrize("segment", [None, "header1", "footer1", "footnote1"])
async def test_tab_and_segment_location(append, segment):
    operation = {
        **INSERT,
        "tab_id": "t.child",
        "selected_option_id": "dropdownItem.done",
    }
    if append:
        operation.pop("index")
        operation["end_of_segment"] = True
    if segment:
        operation["segment_id"] = segment
    service = service_with_replies()
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc", [{**CREATE, "tab_id": "t.child"}, operation]
    )
    assert success, message
    create, insert = requests_from(service)
    assert create["createDropdownDefinition"]["tabId"] == "t.child"
    payload = insert["insertDropdown"]
    assert payload["selectedOptionId"] == "dropdownItem.done"
    location = {"tabId": "t.child"}
    if segment:
        location["segmentId"] = segment
    if not append:
        location["index"] = 5
    assert payload["endOfSegmentLocation" if append else "location"] == location
    assert ("location" in payload) != ("endOfSegmentLocation" in payload)
    assert "tabId" not in payload


@pytest.mark.asyncio
async def test_generated_ids_are_omitted_and_full_replies_keep_request_offsets():
    create = {
        "type": "create_dropdown_definition",
        "title": "Status",
        "options": [{"display_value": "A"}, {"display_value": "B"}],
    }
    definition = {
        "dropdownDefinitionId": "kix.generated",
        "dropdownDefinitionProperties": {
            "title": "Status",
            "options": [
                {"optionId": "dropdownItem.generated", "displayValue": "A"},
                {"optionId": "dropdownItem.other", "displayValue": "B"},
            ],
        },
    }
    chip = {
        "dropdownId": "chip-server-id",
        "dropdownProperties": {
            "dropdownDefinitionId": "kix.status",
            "selectedOptionId": "dropdownItem.todo",
        },
    }
    service = service_with_replies(
        [
            {},
            {},
            {"createDropdownDefinition": {"dropdownDefinition": definition}},
            {"insertDropdown": {"dropdown": chip}},
        ]
    )
    success, message, metadata = await BatchOperationManager(
        service
    ).execute_batch_operations(
        "doc",
        [
            {"type": "replace_text", "start_index": 1, "end_index": 2, "text": "X"},
            create,
            {**INSERT, "tab_id": "t.second"},
        ],
    )
    assert success, message
    assert requests_from(service)[2] == {
        "createDropdownDefinition": {
            "dropdownDefinition": {
                "dropdownDefinitionProperties": {
                    "title": "Status",
                    "options": [{"displayValue": "A"}, {"displayValue": "B"}],
                }
            }
        }
    }
    assert metadata["dropdown_results"] == [
        {"request_index": 2, "tab_id": None, "dropdownDefinition": definition},
        {"request_index": 3, "tab_id": "t.second", "dropdown": chip},
    ]


INVALID = [
    {**CREATE, "options": []},
    {**CREATE, "options": [{"display_value": "A"}]},
    {**CREATE, "options": [{"display_value": "A"}] * 51},
    {**CREATE, "title": ""},
    {**CREATE, "options": [{"display_value": ""}, {"display_value": "B"}]},
    {**CREATE, "dropdown_definition_id": "status"},
    {**CREATE, "dropdown_definition_id": "kix.a"},
    {**CREATE, "dropdown_definition_id": "kix." + "a" * 15},
    {
        **CREATE,
        "options": [
            {"display_value": "A", "option_id": "dropdownItem.same"},
            {"display_value": "B", "option_id": "dropdownItem.same"},
        ],
    },
    *[
        {**CREATE, "options": [{"display_value": "A", **bad}, {"display_value": "B"}]}
        for bad in [
            {"option_id": "bad"},
            {"background_color": "red"},
            {"text_color": "#FFF"},
            {"background_color": "#GG0000"},
            {"text_color": "FF0000"},
            {"bold": True},
        ]
    ],
    {**CREATE, "segment_id": "header1"},
    {**INSERT, "dropdown_definition_id": "bad"},
    {**INSERT, "selected_option_id": "bad"},
    {**INSERT, "end_of_segment": True},
    {**INSERT, "index": None},
    {**INSERT, "index": -1},
    {**INSERT, "index": True},
    {**INSERT, "index": 1.5},
    {**INSERT, "index": "5"},
    {**INSERT, "anchor_text": "Status"},
]


@pytest.mark.parametrize("operation", INVALID)
def test_schema_and_manager_validation_reject_bad_input(operation):
    with pytest.raises(ValidationError):
        TypeAdapter(BatchDocOperation).validate_python(operation)
    assert validate_operation(operation)[0] is False


@pytest.mark.asyncio
async def test_invalid_later_operation_causes_no_write():
    service = service_with_replies()
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc", [CREATE, {**INSERT, "selected_option_id": "bad"}]
    )
    assert not success
    assert "Operation 2" in message
    service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.parametrize("count", [2, 50])
def test_option_count_boundaries_and_json_encoded_operations(count):
    operations = TypeAdapter(BatchDocOperations).validate_python(
        json.dumps(
            [
                {
                    **CREATE,
                    "options": [{"display_value": str(i)} for i in range(count)],
                },
                INSERT,
            ]
        )
    )
    assert len(operations[0].options) == count


@pytest.mark.asyncio
async def test_anchored_insertion_targets_nested_tab_and_preserves_existing_date_element():
    operation = {
        "type": "insert_dropdown",
        "dropdown_definition_id": "kix.status",
        "anchor_text": "Status:",
        "tab_id": "t.child",
    }
    doc = {
        "tabs": [
            {
                "tabProperties": {"tabId": "t.parent"},
                "documentTab": {"body": {"content": []}},
                "childTabs": [
                    {
                        "tabProperties": {"tabId": "t.child"},
                        "documentTab": {
                            "body": {
                                "content": [
                                    {
                                        "startIndex": 1,
                                        "endIndex": 12,
                                        "paragraph": {
                                            "elements": [
                                                {
                                                    "startIndex": 1,
                                                    "endIndex": 3,
                                                    "textRun": {"content": "😀"},
                                                },
                                                {
                                                    "startIndex": 3,
                                                    "endIndex": 4,
                                                    "dateElement": {
                                                        "dateElementId": "native-date"
                                                    },
                                                },
                                                {
                                                    "startIndex": 4,
                                                    "endIndex": 12,
                                                    "textRun": {
                                                        "content": "Status: \\n"
                                                    },
                                                },
                                            ]
                                        },
                                    }
                                ]
                            }
                        },
                    }
                ],
            }
        ]
    }
    original = deepcopy(doc)
    service = service_with_replies()
    service.documents.return_value.get.return_value.execute.return_value = doc
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc", [operation]
    )
    assert success, message
    assert requests_from(service) == [
        {
            "insertDropdown": {
                "dropdownDefinitionId": "kix.status",
                "location": {"index": 11, "tabId": "t.child"},
            }
        }
    ]
    assert doc == original
    assert "index" not in operation


@pytest.mark.asyncio
async def test_anchored_insert_cannot_mix_with_definition_creation():
    service = service_with_replies()
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc",
        [
            CREATE,
            {
                "type": "insert_dropdown",
                "dropdown_definition_id": "kix.status",
                "anchor_text": "Status",
            },
        ],
    )
    assert not success
    assert "cannot be mixed" in message
    service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_tool_exposes_generated_ids_to_caller():
    service = service_with_replies(
        [
            {
                "createDropdownDefinition": {
                    "dropdownDefinition": {"dropdownDefinitionId": "kix.generated"}
                }
            }
        ]
    )
    fn = docs_tools.batch_update_doc
    fn = fn.fn if hasattr(fn, "fn") else fn
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    result = await fn(
        service=service,
        user_google_email="test@example.com",
        document_id="document-id-long-enough",
        operations=[TypeAdapter(BatchDocOperation).validate_python(CREATE)],
    )
    assert '"dropdownDefinitionId": "kix.generated"' in result
    assert '"request_index": 0' in result


def test_supported_operations_expose_both_dropdown_actions():
    supported = BatchOperationManager(Mock()).get_supported_operations()[
        "supported_operations"
    ]
    assert supported["create_dropdown_definition"]["required"] == ["title", "options"]
    assert "selected_option_id" in supported["insert_dropdown"]["optional"]


def test_request_shapes_match_google_client_discovery_schema():
    """Validate wire fields against the public discovery schema captured on 2026-11-17."""
    schemas = json.loads(
        (Path(__file__).parent / "fixtures/dropdown_discovery.json").read_text()
    )["schemas"]

    def validate(value, schema):
        if "$ref" in schema:
            schema = schemas[schema["$ref"]]
        if schema["type"] == "object":
            assert isinstance(value, dict)
            for key, child in value.items():
                assert key in schema["properties"], key
                validate(child, schema["properties"][key])
        elif schema["type"] == "array":
            for child in value:
                validate(child, schema["items"])
        elif schema["type"] == "string":
            assert isinstance(value, str)
        elif schema["type"] in ("number", "integer"):
            assert isinstance(value, (int, float))

    manager = BatchOperationManager(Mock())
    for operation in [
        CREATE,
        INSERT,
        {**CREATE, "tab_id": "t.child"},
        {
            "type": "insert_dropdown",
            "dropdown_definition_id": "kix.status",
            "selected_option_id": "dropdownItem.done",
            "tab_id": "t.child",
            "segment_id": "header1",
            "end_of_segment": True,
        },
    ]:
        request, _ = manager._build_operation_request(operation, operation["type"])
        validate(request, schemas["Request"])


def test_pinned_client_serializes_new_requests_even_with_older_bundled_discovery():
    # The pinned client predates dropdown schemas. batchUpdate body serialization
    # must still pass these new requests through unchanged to Google's server.
    service = build_from_document(
        get_static_doc("docs", "v1"),
        http=HttpMockSequence(
            [
                ({"status": "200"}, b'{"replies": [{}, {}]}'),
            ]
        ),
    )
    manager = BatchOperationManager(service)
    requests = [
        manager._build_operation_request(op, op["type"])[0] for op in [CREATE, INSERT]
    ]
    request = service.documents().batchUpdate(
        documentId="test-document", body={"requests": requests}
    )
    assert json.loads(request.body) == {"requests": requests}
    assert request.execute() == {"replies": [{}, {}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["title", "display_value"])
@pytest.mark.parametrize("length", [200, 201])
async def test_dropdown_text_length_boundaries_before_writes(field, length):
    operation = deepcopy(CREATE)
    value = "x" * length
    if field == "title":
        operation["title"] = value
    else:
        operation["options"][1]["display_value"] = value

    adapter = TypeAdapter(BatchDocOperation)
    if length == 200:
        adapter.validate_python(operation)
        assert validate_operation(operation) == (True, "")
    else:
        with pytest.raises(ValidationError, match="at most 200 characters"):
            adapter.validate_python(operation)
        assert validate_operation(operation)[0] is False

    service = service_with_replies()
    # Direct manager callers must also reject an invalid later operation without
    # sending any earlier writes from the same batch.
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc", [{"type": "insert_text", "text": "Status: ", "index": 1}, operation]
    )
    if length == 200:
        assert success, message
        definition = requests_from(service)[1]["createDropdownDefinition"][
            "dropdownDefinition"
        ]["dropdownDefinitionProperties"]
        actual = (
            definition["title"]
            if field == "title"
            else definition["options"][1]["displayValue"]
        )
        assert actual == value
        service.documents.return_value.batchUpdate.assert_called_once()
    else:
        assert not success
        assert "Operation 2" in message
        assert "at most 200 characters" in message
        service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["false", "true", "0", "1", 0, 1])
async def test_dropdown_rejects_non_boolean_append_flags_before_writes(value):
    operation = {**INSERT, "end_of_segment": value}
    with pytest.raises(ValidationError, match="valid boolean"):
        TypeAdapter(BatchDocOperation).validate_python(operation)
    assert validate_operation(operation)[0] is False
    service = service_with_replies()
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc", [CREATE, operation]
    )
    assert not success
    assert "valid boolean" in message
    service.documents.return_value.batchUpdate.assert_not_called()
    assert operation["end_of_segment"] == value


@pytest.mark.asyncio
@pytest.mark.parametrize("append", [False, True])
async def test_direct_manager_dropdown_preserves_boolean_location_choice(append):
    operation = {
        "type": "insert_dropdown",
        "dropdown_definition_id": "kix.status",
        "end_of_segment": append,
    }
    if not append:
        operation["index"] = 5
    service = service_with_replies()
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc", [operation]
    )
    assert success, message
    location = {"endOfSegmentLocation": {}} if append else {"location": {"index": 5}}
    assert requests_from(service) == [
        {"insertDropdown": {"dropdownDefinitionId": "kix.status", **location}}
    ]
