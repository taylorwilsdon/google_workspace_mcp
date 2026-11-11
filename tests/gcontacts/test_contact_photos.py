"""Contact photo actions through manage_contact, without live Google calls."""

import base64
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest
from googleapiclient.errors import HttpError

from core.utils import UserInputError
from gcontacts.contacts_tools import manage_contact as wrapped_manage_contact
from tests.gdrive.test_local_file_access import _run_subprocess

manage_contact = inspect.unwrap(
    wrapped_manage_contact.fn
    if hasattr(wrapped_manage_contact, "fn")
    else wrapped_manage_contact
)
PHOTO = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII="
)
PHOTO_BASE64 = base64.b64encode(PHOTO).decode("ascii")
MAX_BYTES = 5 * 1024 * 1024
MUTATIONS = (
    "updateContactPhoto",
    "deleteContactPhoto",
    "createContact",
    "updateContact",
    "deleteContact",
)


@pytest.fixture
def people() -> MagicMock:
    people = MagicMock()
    people.updateContactPhoto.return_value.execute.return_value = {
        "person": {"photos": [{"url": "https://example.com/contact.png"}]}
    }
    people.deleteContactPhoto.return_value.execute.return_value = {}
    return people


@pytest.fixture
def photo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(tmp_path))
    monkeypatch.setenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", "false")
    monkeypatch.setattr("core.utils.is_stateless_mode", lambda: False)
    path = tmp_path / "头像.png"
    path.write_bytes(PHOTO)
    return path


async def _call(people: MagicMock, action: str = "set_photo", **kwargs: Any) -> str:
    kwargs.setdefault("contact_id", "c123")
    return await manage_contact(
        service=MagicMock(people=MagicMock(return_value=people)),
        user_google_email="test@example.com",
        action=action,
        **kwargs,
    )


def _assert_no_mutation(people: MagicMock) -> None:
    for name in MUTATIONS:
        getattr(people, name).assert_not_called()


@pytest.mark.asyncio
async def test_set_base64_normalizes_id_and_returns_photo_url(
    people: MagicMock, photo: Path
) -> None:
    result = await _call(people, photo_base64=PHOTO_BASE64)
    people.updateContactPhoto.assert_called_once_with(
        resourceName="people/c123",
        body={
            "photoBytes": PHOTO_BASE64,
            "personFields": "photos",
            "sources": ["READ_SOURCE_TYPE_CONTACT"],
        },
    )
    people.updateContactPhoto.return_value.execute.assert_called_once()
    people.get.assert_not_called()
    people.updateContact.assert_not_called()
    people.deleteContact.assert_not_called()
    assert "Photo URL: https://example.com/contact.png" in result
    assert PHOTO_BASE64 not in result


@pytest.mark.asyncio
async def test_set_local_file_preserves_bytes_and_full_id(
    people: MagicMock, photo: Path
) -> None:
    await _call(people, contact_id="people/c456", file_path=str(photo))
    kwargs = people.updateContactPhoto.call_args.kwargs
    assert kwargs["resourceName"] == "people/c456"
    assert base64.b64decode(kwargs["body"]["photoBytes"]) == PHOTO


@pytest.mark.asyncio
async def test_delete_photo_does_not_delete_contact(
    people: MagicMock, photo: Path
) -> None:
    result = await _call(people, "delete_photo")
    people.deleteContactPhoto.assert_called_once_with(resourceName="people/c123")
    people.deleteContactPhoto.return_value.execute.assert_called_once()
    people.deleteContact.assert_not_called()
    people.updateContactPhoto.assert_not_called()
    assert result == "Contact photo removed for test@example.com:\nContact ID: c123"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action, kwargs",
    [("set_photo", {"photo_base64": PHOTO_BASE64}), ("delete_photo", {})],
)
async def test_photo_actions_require_contact_id(
    people: MagicMock, photo: Path, action: str, kwargs: dict[str, str]
) -> None:
    with pytest.raises(UserInputError, match="contact_id"):
        await _call(people, action, contact_id=None, **kwargs)
    _assert_no_mutation(people)


@pytest.mark.asyncio
@pytest.mark.parametrize("both", [False, True])
async def test_set_requires_exactly_one_source(
    people: MagicMock, photo: Path, both: bool
) -> None:
    kwargs = {"file_path": str(photo), "photo_base64": PHOTO_BASE64} if both else {}
    with pytest.raises(UserInputError, match="exactly one"):
        await _call(people, **kwargs)
    _assert_no_mutation(people)


@pytest.mark.asyncio
async def test_missing_source_names_only_advertised_inputs(
    people: MagicMock, photo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", "true")
    with pytest.raises(UserInputError) as exc:
        await _call(people)
    assert "photo_base64" in str(exc.value)
    assert "file_path" not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        "",
        "not base64!",
        "a",
        "头像",
        "data:image/png;base64," + PHOTO_BASE64,
        PHOTO_BASE64[:8] + "\n" + PHOTO_BASE64[8:],
    ],
)
async def test_invalid_or_empty_base64_is_rejected(
    people: MagicMock, photo: Path, value: str
) -> None:
    with pytest.raises(UserInputError):
        await _call(people, photo_base64=value)
    _assert_no_mutation(people)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "update", "delete", "delete_photo"])
@pytest.mark.parametrize("source", ["photo_base64", "file_path"])
async def test_other_actions_reject_photo_input_before_mutation(
    people: MagicMock, photo: Path, action: str, source: str
) -> None:
    value = PHOTO_BASE64 if source == "photo_base64" else str(photo)
    with patch("gcontacts.contacts_helpers.validate_file_path") as validate:
        with pytest.raises(UserInputError, match="only.*set_photo"):
            await _call(people, action, **{source: value})
    validate.assert_not_called()
    people.get.assert_not_called()
    _assert_no_mutation(people)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, match", [("missing.png", "does not exist"), ("", "regular file")]
)
async def test_missing_or_directory_file_is_rejected(
    people: MagicMock, photo: Path, name: str, match: str
) -> None:
    path = photo.with_name(name) if name else photo.parent
    with pytest.raises(UserInputError, match=match):
        await _call(people, file_path=str(path))
    _assert_no_mutation(people)


@pytest.mark.asyncio
async def test_empty_file_is_rejected(people: MagicMock, photo: Path) -> None:
    photo.write_bytes(b"")
    with pytest.raises(UserInputError):
        await _call(people, file_path=str(photo))
    _assert_no_mutation(people)


@pytest.mark.asyncio
async def test_path_outside_allowlist_is_rejected(
    people: MagicMock, photo: Path
) -> None:
    with patch("core.utils._get_allowed_file_dirs", return_value=[]):
        with pytest.raises(UserInputError):
            await _call(people, file_path=str(photo))
    _assert_no_mutation(people)


@pytest.mark.asyncio
async def test_local_files_disabled_is_enforced_at_runtime(
    people: MagicMock, photo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", "true")
    with pytest.raises(UserInputError, match="Local file access is disabled"):
        await _call(people, file_path=str(photo))
    _assert_no_mutation(people)


@pytest.mark.asyncio
async def test_stateless_server_rejects_path_but_accepts_base64(
    people: MagicMock, photo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("core.utils.is_stateless_mode", lambda: True)
    with pytest.raises(UserInputError):
        await _call(people, file_path=str(photo))
    await _call(people, photo_base64=PHOTO_BASE64)
    people.updateContactPhoto.assert_called_once()


@pytest.mark.asyncio
async def test_large_base64_is_rejected_before_decode(
    people: MagicMock, photo: Path
) -> None:
    with patch("gcontacts.contacts_helpers.base64.b64decode") as decode:
        with pytest.raises(UserInputError):
            await _call(people, photo_base64="A" * (4 * ((MAX_BYTES + 2) // 3) + 4))
    decode.assert_not_called()
    _assert_no_mutation(people)


@pytest.mark.asyncio
async def test_large_file_is_rejected(people: MagicMock, photo: Path) -> None:
    photo.write_bytes(b"x" * (MAX_BYTES + 1))
    with pytest.raises(UserInputError):
        await _call(people, file_path=str(photo))
    _assert_no_mutation(people)


@pytest.mark.asyncio
async def test_file_read_stops_past_the_limit(people: MagicMock, photo: Path) -> None:
    opened = mock_open(read_data=b"x")
    with patch("pathlib.Path.open", opened):
        await _call(people, file_path=str(photo))
    opened.return_value.read.assert_called_once_with(MAX_BYTES + 1)


@pytest.mark.asyncio
async def test_decoded_limit_is_checked_even_when_encoded_length_fits(
    people: MagicMock, photo: Path
) -> None:
    value = base64.b64encode(b"x" * (MAX_BYTES + 1)).decode("ascii")
    with pytest.raises(UserInputError):
        await _call(people, photo_base64=value)
    _assert_no_mutation(people)


@pytest.mark.asyncio
async def test_file_limit_boundary_is_inclusive(people: MagicMock, photo: Path) -> None:
    photo.write_bytes(b"x" * MAX_BYTES)
    await _call(people, file_path=str(photo))
    data = people.updateContactPhoto.call_args.kwargs["body"]["photoBytes"]
    assert len(base64.b64decode(data)) == MAX_BYTES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action, method, kwargs",
    [
        ("delete_photo", "deleteContactPhoto", {}),
        ("set_photo", "updateContactPhoto", {"photo_base64": PHOTO_BASE64}),
    ],
)
async def test_api_failure_propagates_without_fallback(
    people: MagicMock, photo: Path, action: str, method: str, kwargs: dict[str, str]
) -> None:
    request = getattr(people, method).return_value
    request.execute.side_effect = HttpError(
        SimpleNamespace(status=403, reason="Forbidden"), b"{}"
    )
    with pytest.raises(HttpError):
        await _call(people, action, **kwargs)
    request.execute.assert_called_once()
    people.deleteContact.assert_not_called()


@pytest.mark.asyncio
async def test_existing_delete_still_deletes_contact_only(
    people: MagicMock, photo: Path
) -> None:
    await _call(people, "delete")
    people.deleteContact.assert_called_once_with(resourceName="people/c123")
    people.updateContactPhoto.assert_not_called()
    people.deleteContactPhoto.assert_not_called()


SCHEMA_CODE = """
import asyncio, json
from core.server import server, set_transport_mode
set_transport_mode('streamable-http')
import gcontacts.contacts_tools
from fastmcp import Client

async def main():
    async with Client(server) as client:
        tool = {t.name: t for t in await client.list_tools()}['manage_contact']
        print('SHIPPED:' + json.dumps({
            'description': tool.description or '',
            'properties': {
                name: prop.get('description', '')
                for name, prop in tool.input_schema['properties'].items()
            },
        }))

asyncio.run(main())
"""


def _shipped(env: dict[str, str]) -> dict[str, Any]:
    return json.loads(_run_subprocess(SCHEMA_CODE, env).split("SHIPPED:", 1)[1])


def test_local_files_disabled_hides_file_path_from_shipped_schema() -> None:
    shipped = _shipped({"WORKSPACE_MCP_DISABLE_LOCAL_FILES": "true"})
    assert "photo_base64" in shipped["properties"]
    texts = [
        shipped["description"],
        *shipped["properties"],
        *shipped["properties"].values(),
    ]
    assert [text for text in texts if "file_path" in text] == []


def test_local_files_enabled_advertises_file_path() -> None:
    assert "file_path" in _shipped({})["properties"]
