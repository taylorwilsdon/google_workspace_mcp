"""Findings 3, 24, 30, 39: attachment storage bounds size and scopes reads to an owner.

`save_attachment` used to decode an arbitrary base64 blob into memory with no ceiling
(finding 3), and every read path keyed on the file id alone, so the id behaved like a
bearer token for whoever held it (findings 24, 30, 39).
"""

import base64

import pytest

import core.attachment_storage as attachment_storage
from core.attachment_storage import (
    MAX_ATTACHMENT_BYTES,
    AttachmentStorage,
    get_attachment_url,
)

OWNER = "owner@example.com"
OTHER = "attacker@example.com"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", tmp_path)
    return AttachmentStorage()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode()


class TestSizeLimit:
    def test_limit_is_fixed_at_50_mib(self):
        assert MAX_ATTACHMENT_BYTES == 50 * 1024 * 1024

    def test_oversized_payload_is_rejected_before_decoding(self, storage, monkeypatch):
        """The encoded length is checked first, so the bytes never materialise."""
        monkeypatch.setattr(attachment_storage, "MAX_ATTACHMENT_BYTES", 16)

        def _explode(_data):
            raise AssertionError("base64 must not be decoded past the limit")

        monkeypatch.setattr(attachment_storage.base64, "urlsafe_b64decode", _explode)

        with pytest.raises(ValueError, match="exceeds the 16 byte limit"):
            storage.save_attachment(_b64(b"x" * 128), owner=OWNER)

    def test_payload_within_the_limit_is_stored(self, storage, monkeypatch):
        monkeypatch.setattr(attachment_storage, "MAX_ATTACHMENT_BYTES", 16)

        saved = storage.save_attachment(_b64(b"0123456789"), owner=OWNER)

        assert storage.get_attachment_path(saved.file_id, owner=OWNER) is not None

    def test_decoded_size_is_checked_too(self, storage, monkeypatch):
        """A short encoded string can still decode to more than the limit."""
        monkeypatch.setattr(attachment_storage, "MAX_ATTACHMENT_BYTES", 4)

        # 8 raw bytes -> 12 encoded chars, under the 4-byte limit's encoded ceiling
        # of 8, so only the post-decode check can catch it.
        with pytest.raises(ValueError, match="exceeds the 4 byte limit"):
            storage.save_attachment(_b64(b"01234567"), owner=OWNER)


class TestOwnership:
    def test_owner_is_required_to_store(self, storage):
        with pytest.raises(ValueError, match="attachment owner is required"):
            storage.save_attachment(_b64(b"data"), owner="")

    def test_owner_can_read_back(self, storage):
        saved = storage.save_attachment(_b64(b"data"), filename="a.txt", owner=OWNER)

        assert storage.get_attachment_metadata(saved.file_id, owner=OWNER) is not None
        assert storage.get_attachment_path(saved.file_id, owner=OWNER) is not None

    def test_other_principal_cannot_read(self, storage):
        saved = storage.save_attachment(_b64(b"data"), filename="a.txt", owner=OWNER)

        assert storage.get_attachment_metadata(saved.file_id, owner=OTHER) is None
        assert storage.get_attachment_path(saved.file_id, owner=OTHER) is None

    def test_owner_matching_ignores_case(self, storage):
        saved = storage.save_attachment(_b64(b"data"), owner="Owner@Example.COM")

        assert storage.get_attachment_path(saved.file_id, owner=OWNER) is not None

    def test_denial_is_indistinguishable_from_a_miss(self, storage):
        """Both return None, so the id space cannot be probed for existence."""
        saved = storage.save_attachment(_b64(b"data"), owner=OWNER)

        denied = storage.get_attachment_metadata(saved.file_id, owner=OTHER)
        missing = storage.get_attachment_metadata("no-such-id", owner=OTHER)

        assert denied is None and missing is None

    def test_owner_is_recorded_in_metadata(self, storage):
        saved = storage.save_attachment(_b64(b"data"), owner="Owner@Example.com")

        metadata = storage.get_attachment_metadata(saved.file_id, owner=OWNER)

        assert metadata["owner"] == OWNER

    def test_from_path_also_requires_an_owner(self, storage, tmp_path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"payload")

        with pytest.raises(ValueError, match="attachment owner is required"):
            storage.save_attachment_from_path(str(src), owner="  ")


class TestAbsoluteUrl:
    def test_url_is_absolute(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_EXTERNAL_URL", "https://mcp.example.com")
        monkeypatch.setattr(
            "auth.oauth_callback_server.ensure_stdio_oauth_callback_available",
            lambda: (True, ""),
        )

        url = get_attachment_url("abc-123")

        assert url == "https://mcp.example.com/attachments/abc-123"

    def test_relative_base_url_is_refused(self, monkeypatch):
        """Finding 38: a relative URL skips the consumer's trusted-origin check."""
        monkeypatch.setenv("WORKSPACE_EXTERNAL_URL", "/mcp")
        monkeypatch.setattr(
            "auth.oauth_callback_server.ensure_stdio_oauth_callback_available",
            lambda: (True, ""),
        )

        with pytest.raises(ValueError, match="absolute attachment URL"):
            get_attachment_url("abc-123")
