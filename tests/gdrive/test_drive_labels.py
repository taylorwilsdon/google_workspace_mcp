"""Tests for user-level Google Drive label tools."""

import json
from unittest.mock import Mock

import pytest

from gdrive import drive_tools


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.mark.asyncio
async def test_list_drive_labels_uses_user_applier_view():
    service = Mock()
    service.labels.return_value.list.return_value.execute.return_value = {
        "labels": [{"id": "label-1", "properties": {"title": "Sensitivity"}}]
    }

    result = await _unwrap(drive_tools.list_drive_labels)(
        service=service,
        user_google_email="user@example.com",
    )

    params = service.labels.return_value.list.call_args.kwargs
    assert params == {
        "view": "LABEL_VIEW_FULL",
        "publishedOnly": True,
        "minimumRole": "APPLIER",
        "pageSize": 100,
    }
    assert json.loads(result)["labels"][0]["id"] == "label-1"


@pytest.mark.asyncio
async def test_list_drive_file_labels_preserves_field_values():
    service = Mock()
    service.files.return_value.listLabels.return_value.execute.return_value = {
        "labels": [{"id": "label-1", "fields": {"field-1": {"valueType": "selection"}}}]
    }

    result = await _unwrap(drive_tools.list_drive_file_labels)(
        service=service,
        user_google_email="user@example.com",
        file_id="file-1",
    )

    service.files.return_value.listLabels.assert_called_once_with(
        fileId="file-1", maxResults=100
    )
    assert json.loads(result)["labels"][0]["fields"]["field-1"]


@pytest.mark.asyncio
async def test_modify_drive_file_labels_sets_selection_value():
    service = Mock()
    service.files.return_value.modifyLabels.return_value.execute.return_value = {
        "modifiedLabels": [{"id": "label-1"}]
    }
    modifications = [
        {
            "labelId": "label-1",
            "fieldModifications": [
                {
                    "fieldId": "field-1",
                    "setSelectionValues": ["confidential-choice"],
                }
            ],
        }
    ]

    result = await _unwrap(drive_tools.modify_drive_file_labels)(
        service=service,
        user_google_email="user@example.com",
        file_id="file-1",
        label_modifications=modifications,
    )

    service.files.return_value.modifyLabels.assert_called_once_with(
        fileId="file-1", body={"labelModifications": modifications}
    )
    assert json.loads(result)["modifiedLabels"][0]["id"] == "label-1"


@pytest.mark.asyncio
async def test_modify_drive_file_labels_removes_label():
    service = Mock()
    service.files.return_value.modifyLabels.return_value.execute.return_value = {
        "modifiedLabels": [{"id": "label-1"}]
    }
    modifications = [{"labelId": "label-1", "removeLabel": True}]

    await _unwrap(drive_tools.modify_drive_file_labels)(
        service=service,
        user_google_email="user@example.com",
        file_id="file-1",
        label_modifications=modifications,
    )

    service.files.return_value.modifyLabels.assert_called_once_with(
        fileId="file-1", body={"labelModifications": modifications}
    )


@pytest.mark.parametrize(
    "modifications",
    [
        [],
        [{}],
        [{"labelId": "label-1", "unknown": True}],
        [
            {
                "labelId": "label-1",
                "fieldModifications": [{"fieldId": "field-1"}],
            }
        ],
        [
            {
                "labelId": "label-1",
                "removeLabel": True,
                "fieldModifications": [{"fieldId": "field-1", "unsetValues": True}],
            }
        ],
    ],
)
def test_validate_label_modifications_rejects_unsafe_shapes(modifications):
    with pytest.raises(ValueError):
        drive_tools._validate_label_modifications(modifications)


@pytest.mark.parametrize(
    "modification",
    [
        {"labelId": "label-1", "removeLabel": "true"},
        {
            "labelId": "label-1",
            "fieldModifications": [
                {"fieldId": "field-1", "setSelectionValues": "choice"}
            ],
        },
        {
            "labelId": "label-1",
            "fieldModifications": [{"fieldId": "field-1", "setSelectionValues": [1]}],
        },
        {
            "labelId": "label-1",
            "fieldModifications": [{"fieldId": "field-1", "unsetValues": "true"}],
        },
        {
            "labelId": "label-1",
            "fieldModifications": [{"fieldId": "field-1", "setIntegerValues": [True]}],
        },
    ],
)
def test_validate_label_modifications_rejects_invalid_value_types(modification):
    with pytest.raises(ValueError):
        drive_tools._validate_label_modifications([modification])
