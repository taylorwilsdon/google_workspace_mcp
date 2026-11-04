"""Contact photo actions through manage_contact, without live Google calls."""

import asyncio
import base64
import functools
import inspect
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from googleapiclient.errors import HttpError

from core.utils import UserInputError
from gcontacts.contacts_tools import manage_contact as wrapped_manage_contact


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    return inspect.unwrap(fn)


manage_contact = _unwrap(wrapped_manage_contact)
# A valid 1x1 PNG. Tests verify transport bytes, not Google's image processing.
PHOTO = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII="
)
PHOTO_BASE64 = base64.b64encode(PHOTO).decode("ascii")
MAX_BYTES = 5 * 1024 * 1024


def _run_async(method):
    """Use a private loop without clearing the suite's current event loop."""

    @functools.wraps(method)
    def run(self):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(method(self))
        finally:
            loop.close()

    return run


class TestContactPhotos(unittest.TestCase):
    def setUp(self):
        self.service = MagicMock()
        self.people = self.service.people.return_value
        self.people.updateContactPhoto.return_value.execute.return_value = {
            "person": {"photos": [{"url": "https://example.com/contact.png"}]}
        }
        self.people.deleteContactPhoto.return_value.execute.return_value = {}
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.photo = Path(self.temp.name) / "头像.png"
        self.photo.write_bytes(PHOTO)
        self.env = patch.dict(
            os.environ,
            {
                "ALLOWED_FILE_DIRS": self.temp.name,
                "WORKSPACE_MCP_DISABLE_LOCAL_FILES": "false",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.stateless = patch("core.utils.is_stateless_mode", return_value=False)
        self.stateless.start()
        self.addCleanup(self.stateless.stop)

    async def call(self, action="set_photo", **kwargs):
        kwargs.setdefault("contact_id", "c123")
        return await manage_contact(
            service=self.service,
            user_google_email="test@example.com",
            action=action,
            **kwargs,
        )

    def assert_no_contact_mutation(self):
        self.people.updateContactPhoto.assert_not_called()
        self.people.deleteContactPhoto.assert_not_called()
        self.people.createContact.assert_not_called()
        self.people.updateContact.assert_not_called()
        self.people.deleteContact.assert_not_called()

    @_run_async
    async def test_set_base64_normalizes_id_and_returns_photo_url(self):
        result = await self.call(photo_base64=PHOTO_BASE64)
        self.people.updateContactPhoto.assert_called_once_with(
            resourceName="people/c123",
            body={
                "photoBytes": PHOTO_BASE64,
                "personFields": "photos",
                "sources": ["READ_SOURCE_TYPE_CONTACT"],
            },
        )
        self.people.updateContactPhoto.return_value.execute.assert_called_once()
        self.people.get.assert_not_called()
        self.people.updateContact.assert_not_called()
        self.people.deleteContact.assert_not_called()
        self.assertIn("Photo URL: https://example.com/contact.png", result)
        self.assertNotIn(PHOTO_BASE64, result)

    @_run_async
    async def test_set_local_file_preserves_bytes_and_full_id(self):
        await self.call(contact_id="people/c456", file_path=str(self.photo))
        kwargs = self.people.updateContactPhoto.call_args.kwargs
        self.assertEqual(kwargs["resourceName"], "people/c456")
        self.assertEqual(base64.b64decode(kwargs["body"]["photoBytes"]), PHOTO)

    @_run_async
    async def test_delete_photo_does_not_delete_contact(self):
        result = await self.call("delete_photo")
        self.people.deleteContactPhoto.assert_called_once_with(
            resourceName="people/c123",
            personFields="photos",
            sources=["READ_SOURCE_TYPE_CONTACT"],
        )
        self.people.deleteContactPhoto.return_value.execute.assert_called_once()
        self.people.deleteContact.assert_not_called()
        self.people.updateContactPhoto.assert_not_called()
        self.assertIn("Contact photo removed", result)
        self.assertIn("c123", result)

    @_run_async
    async def test_photo_actions_require_contact_id(self):
        for action in ("set_photo", "delete_photo"):
            with self.subTest(action=action):
                with self.assertRaisesRegex(UserInputError, "contact_id"):
                    kwargs = (
                        {"photo_base64": PHOTO_BASE64} if action == "set_photo" else {}
                    )
                    await self.call(action, contact_id=None, **kwargs)
        self.assert_no_contact_mutation()

    @_run_async
    async def test_set_requires_exactly_one_source(self):
        for kwargs in (
            {},
            {"file_path": str(self.photo), "photo_base64": PHOTO_BASE64},
        ):
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaisesRegex(UserInputError, "exactly one"):
                    await self.call(**kwargs)
        self.assert_no_contact_mutation()

    @_run_async
    async def test_invalid_or_empty_base64_is_rejected(self):
        for value in (
            "",
            "not base64!",
            "a",
            "头像",
            "data:image/png;base64," + PHOTO_BASE64,
        ):
            with self.subTest(value=value[:20]):
                with self.assertRaises(UserInputError):
                    await self.call(photo_base64=value)
        self.assert_no_contact_mutation()

    @_run_async
    async def test_delete_rejects_photo_input(self):
        for kwargs in (
            {"photo_base64": PHOTO_BASE64},
            {"file_path": str(self.photo)},
        ):
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaisesRegex(UserInputError, "only.*set_photo"):
                    await self.call("delete_photo", **kwargs)
        self.assert_no_contact_mutation()

    @_run_async
    async def test_existing_actions_reject_photo_input_before_mutation(self):
        for action in ("create", "update", "delete"):
            with self.subTest(action=action):
                with self.assertRaisesRegex(UserInputError, "only.*set_photo"):
                    await self.call(action, photo_base64=PHOTO_BASE64)
        self.assert_no_contact_mutation()
        self.people.get.assert_not_called()

    @_run_async
    async def test_missing_or_directory_file_is_rejected(self):
        for path in (self.photo.with_name("missing.png"), self.photo.parent):
            with self.subTest(path=str(path)):
                with self.assertRaises(UserInputError):
                    await self.call(file_path=str(path))
        self.assert_no_contact_mutation()

    @_run_async
    async def test_empty_file_is_rejected(self):
        self.photo.write_bytes(b"")
        with self.assertRaises(UserInputError):
            await self.call(file_path=str(self.photo))
        self.assert_no_contact_mutation()

    @_run_async
    async def test_path_outside_allowlist_is_rejected(self):
        with patch("core.utils._get_allowed_file_dirs", return_value=[]):
            with self.assertRaises(UserInputError):
                await self.call(file_path=str(self.photo))
        self.assert_no_contact_mutation()

    @_run_async
    async def test_local_files_disabled_is_enforced_at_runtime(self):
        with patch.dict(os.environ, {"WORKSPACE_MCP_DISABLE_LOCAL_FILES": "true"}):
            with self.assertRaisesRegex(
                UserInputError, "Local file access is disabled"
            ):
                await self.call(file_path=str(self.photo))
        self.assert_no_contact_mutation()

    @_run_async
    async def test_stateless_server_rejects_path_but_accepts_base64(self):
        with patch("core.utils.is_stateless_mode", return_value=True):
            with self.assertRaises(UserInputError):
                await self.call(file_path=str(self.photo))
            await self.call(photo_base64=PHOTO_BASE64)
        self.people.updateContactPhoto.assert_called_once()

    @_run_async
    async def test_large_base64_is_rejected_before_decode(self):
        with patch("gcontacts.contacts_helpers.base64.b64decode") as decode:
            with self.assertRaises(UserInputError):
                await self.call(photo_base64="A" * (4 * ((MAX_BYTES + 2) // 3) + 4))
        decode.assert_not_called()
        self.assert_no_contact_mutation()

    @_run_async
    async def test_large_file_is_rejected(self):
        self.photo.write_bytes(b"x" * (MAX_BYTES + 1))
        with self.assertRaises(UserInputError):
            await self.call(file_path=str(self.photo))
        self.assert_no_contact_mutation()

    @_run_async
    async def test_decoded_limit_is_checked_even_when_encoded_length_fits(self):
        value = base64.b64encode(b"x" * (MAX_BYTES + 1)).decode("ascii")
        with self.assertRaises(UserInputError):
            await self.call(photo_base64=value)
        self.assert_no_contact_mutation()

    @_run_async
    async def test_file_limit_boundary_is_inclusive(self):
        self.photo.write_bytes(b"x" * MAX_BYTES)
        await self.call(file_path=str(self.photo))
        data = self.people.updateContactPhoto.call_args.kwargs["body"]["photoBytes"]
        self.assertEqual(len(base64.b64decode(data)), MAX_BYTES)

    @_run_async
    async def test_photo_input_on_delete_never_reads_a_file(self):
        with patch("gcontacts.contacts_helpers.validate_file_path") as validate:
            with self.assertRaises(UserInputError):
                await self.call("delete", file_path=str(self.photo))
        validate.assert_not_called()
        self.assert_no_contact_mutation()

    @_run_async
    async def test_delete_photo_failure_does_not_fall_back_to_deleting_contact(self):
        error = HttpError(SimpleNamespace(status=403, reason="Forbidden"), b"{}")
        request = self.people.deleteContactPhoto.return_value
        request.execute.side_effect = error
        with self.assertRaises(HttpError):
            await self.call("delete_photo")
        request.execute.assert_called_once()
        self.people.deleteContact.assert_not_called()

    @_run_async
    async def test_api_failure_propagates_without_retry_or_success(self):
        error = HttpError(SimpleNamespace(status=400, reason="Bad Request"), b"{}")
        request = self.people.updateContactPhoto.return_value
        request.execute.side_effect = error
        with self.assertRaises(HttpError):
            await self.call(photo_base64=PHOTO_BASE64)
        request.execute.assert_called_once()
        self.people.deleteContact.assert_not_called()

    @_run_async
    async def test_existing_delete_still_deletes_contact_only(self):
        await self.call("delete")
        self.people.deleteContact.assert_called_once_with(resourceName="people/c123")
        self.people.updateContactPhoto.assert_not_called()
        self.people.deleteContactPhoto.assert_not_called()


if __name__ == "__main__":
    unittest.main()
