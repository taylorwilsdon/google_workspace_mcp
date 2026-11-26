"""Native dropdown contracts, request routing and chip-preservation regressions."""

import json
from collections.abc import Sequence
from copy import deepcopy
from typing import Any
from unittest.mock import Mock

import pytest
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


def service_with_replies(replies: Sequence[dict[str, Any]] = ()) -> Mock:
    service = Mock()
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": list(replies)
    }
    service.documents.return_value.get.return_value.execute.return_value = {
        "revisionId": "r1",
        "tabs": [],
    }
    return service


def requests_from(service: Mock) -> list[dict[str, Any]]:
    return service.documents.return_value.batchUpdate.call_args.kwargs["body"][
        "requests"
    ]


@pytest.mark.asyncio
async def test_single_batch_creation_insertion_and_date_chip_are_native_and_ordered() -> (
    None
):
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
async def test_tab_and_segment_location(append: bool, segment: str | None) -> None:
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
async def test_generated_ids_are_omitted_from_requests_and_returned_in_results() -> (
    None
):
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
        {
            "title": create["title"],
            "dropdown_definition_id": "kix.generated",
            "tab_id": None,
            "options": [
                {"option_id": "dropdownItem.generated", "display_value": "A"},
                {"option_id": "dropdownItem.other", "display_value": "B"},
            ],
        },
        {
            "dropdown_id": "chip-server-id",
            "dropdown_definition_id": "kix.status",
            "selected_option_id": "dropdownItem.todo",
            "tab_id": "t.second",
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("append", [False, True])
async def test_sparse_replies_still_report_the_committed_write(append: bool) -> None:
    insert: dict[str, Any] = {
        "type": "insert_dropdown",
        "dropdown_definition_id": "kix.status",
    }
    insert.update({"end_of_segment": True} if append else {"index": 5})
    service = service_with_replies(
        [{"createDropdownDefinition": {}}, {"insertDropdown": {}}]
    )
    success, message, metadata = await BatchOperationManager(
        service
    ).execute_batch_operations(
        "doc", [{**CREATE, "tab_id": "t.child"}, {**insert, "tab_id": "t.child"}]
    )
    assert success, message
    assert metadata["dropdown_results"] == [
        {
            "title": CREATE["title"],
            "dropdown_definition_id": None,
            "tab_id": "t.child",
            "options": [],
        },
        {
            "dropdown_id": None,
            "dropdown_definition_id": "kix.status",
            "selected_option_id": None,
            "tab_id": "t.child",
        },
    ]


@pytest.mark.asyncio
async def test_generated_definitions_in_one_batch_are_told_apart_by_title() -> None:
    options = [{"display_value": "Yes"}, {"display_value": "No"}]
    replies = [
        {
            "createDropdownDefinition": {
                "dropdownDefinition": {"dropdownDefinitionId": i}
            }
        }
        for i in ("kix.one", "kix.two")
    ]
    service = service_with_replies([{}, {}, *replies])
    success, message, metadata = await BatchOperationManager(
        service
    ).execute_batch_operations(
        "doc",
        [
            {"type": "replace_text", "start_index": 1, "end_index": 2, "text": "X"},
            *[
                {"type": "create_dropdown_definition", "title": t, "options": options}
                for t in ("A set", "B set")
            ],
        ],
    )
    assert success, message
    assert len(requests_from(service)) == 4
    assert [
        (result["title"], result["dropdown_definition_id"])
        for result in metadata["dropdown_results"]
    ] == [("A set", "kix.one"), ("B set", "kix.two")]


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
    {**INSERT, "index": 1.5},
    {**INSERT, "anchor_text": "Status"},
]


@pytest.mark.parametrize("operation", INVALID)
def test_schema_rejects_bad_input(operation: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(BatchDocOperation).validate_python(operation)


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ({**INSERT, "index": "5"}, {"index": 5, "end_of_segment": False}),
        (
            {**INSERT, "index": None, "end_of_segment": "true"},
            {"index": None, "end_of_segment": True},
        ),
        ({**INSERT, "end_of_segment": "false"}, {"index": 5, "end_of_segment": False}),
    ],
)
def test_schema_coerces_stringified_location_like_other_insertions(
    operation: dict[str, Any], expected: dict[str, Any]
) -> None:
    parsed = TypeAdapter(BatchDocOperation).validate_python(operation)
    assert parsed.model_dump(include={"index", "end_of_segment"}) == expected


@pytest.mark.parametrize(
    ("operation", "error"),
    [
        (
            {k: v for k, v in CREATE.items() if k != "title"},
            "Missing required field: title",
        ),
        (
            {k: v for k, v in CREATE.items() if k != "options"},
            "Missing required field: options",
        ),
        (
            {"type": "insert_dropdown", "index": 5},
            "Missing required field: dropdown_definition_id",
        ),
    ],
)
def test_manager_validation_requires_dropdown_fields(
    operation: dict[str, Any], error: str
) -> None:
    assert validate_operation(operation) == (False, error)


@pytest.mark.parametrize(
    "location", [{}, {"index": 5, "end_of_segment": True}, {"index": None}]
)
def test_manager_validation_requires_exactly_one_dropdown_location(
    location: dict[str, Any],
) -> None:
    operation = {
        "type": "insert_dropdown",
        "dropdown_definition_id": "kix.status",
        **location,
    }
    valid, error = validate_operation(operation)
    assert not valid
    assert error.startswith("Provide exactly one of 'index'")


@pytest.mark.asyncio
async def test_invalid_later_operation_causes_no_write() -> None:
    service = service_with_replies()
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc", [CREATE, {"type": "insert_dropdown", "index": 5}]
    )
    assert not success
    assert "Operation 2: Missing required field: dropdown_definition_id" in message
    service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.parametrize("count", [2, 50])
def test_option_count_boundaries_and_json_encoded_operations(count: int) -> None:
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
async def test_anchored_insertion_targets_nested_tab_and_preserves_existing_date_element() -> (
    None
):
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
async def test_anchored_insert_cannot_mix_with_definition_creation() -> None:
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
async def test_tool_exposes_generated_ids_to_caller() -> None:
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
    assert (
        f' Revision: r1 -> r1.\nDropdown results: [{{"title": "{CREATE["title"]}", '
        '"dropdown_definition_id": "kix.generated", "tab_id": null, "options": []}]'
        "\n To apply formatting"
    ) in result


def test_supported_operations_expose_both_dropdown_actions() -> None:
    supported = BatchOperationManager(Mock()).get_supported_operations()[
        "supported_operations"
    ]
    assert supported["create_dropdown_definition"]["required"] == ["title", "options"]
    assert "selected_option_id" in supported["insert_dropdown"]["optional"]


def test_unsupported_type_error_lists_both_dropdown_actions() -> None:
    with pytest.raises(ValueError) as exc:
        BatchOperationManager(Mock())._build_operation_request({}, "bogus")
    supported = str(exc.value).split("Supported: ")[1].split(", ")
    assert {"create_dropdown_definition", "insert_dropdown"} <= set(supported)


@pytest.mark.parametrize("field", ["title", "display_value"])
@pytest.mark.parametrize("length", [200, 201])
def test_dropdown_text_length_boundaries(field: str, length: int) -> None:
    operation = deepcopy(CREATE)
    if field == "title":
        operation["title"] = "x" * length
    else:
        operation["options"][1]["display_value"] = "x" * length

    adapter = TypeAdapter(BatchDocOperation)
    if length == 200:
        adapter.validate_python(operation)
    else:
        with pytest.raises(ValidationError, match="at most 200 characters"):
            adapter.validate_python(operation)


@pytest.mark.asyncio
@pytest.mark.parametrize("append", [False, True])
async def test_direct_manager_dropdown_preserves_boolean_location_choice(
    append: bool,
) -> None:
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
