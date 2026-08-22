"""Findings 11 and 44: create_drive_file must not buffer unbounded uploads.

`base64_content` was decoded straight into memory, and a `file://` URL was read with
`path_obj.read_bytes()` -- both unbounded.
"""

import base64
from unittest.mock import Mock, patch

import pytest

import gdrive.drive_tools as drive_tools


def _unwrap(tool):
    fn = getattr(tool, "fn", tool)
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


create_drive_file = _unwrap(drive_tools.create_drive_file)


@pytest.fixture
def drive_service():
    service = Mock()
    service.files().create().execute.return_value = {
        "id": "new-file",
        "name": "uploaded.bin",
        "webViewLink": "https://drive.example/new-file",
    }
    return service


@pytest.fixture(autouse=True)
def _stub_folder_resolution(monkeypatch):
    async def fake_resolve(_service, folder_id):
        return folder_id or "root"

    monkeypatch.setattr(drive_tools, "resolve_folder_id", fake_resolve)


class TestInlineBase64Limit:
    @pytest.mark.asyncio
    async def test_oversized_encoded_payload_is_rejected_before_decoding(
        self, drive_service, monkeypatch
    ):
        monkeypatch.setattr(drive_tools, "MAX_DRIVE_INLINE_BASE64_BYTES", 16)

        def _explode(*_args, **_kwargs):
            raise AssertionError("base64 must not be decoded past the limit")

        monkeypatch.setattr(drive_tools.base64, "b64decode", _explode)

        with pytest.raises(ValueError, match="exceeds the 16 byte limit"):
            await create_drive_file(
                service=drive_service,
                user_google_email="user@example.com",
                file_name="uploaded.bin",
                base64_content=base64.b64encode(b"x" * 128).decode(),
                content_mime_type="application/octet-stream",
            )

    @pytest.mark.asyncio
    async def test_payload_within_limit_uploads(self, drive_service, monkeypatch):
        monkeypatch.setattr(drive_tools, "MAX_DRIVE_INLINE_BASE64_BYTES", 64)

        result = await create_drive_file(
            service=drive_service,
            user_google_email="user@example.com",
            file_name="uploaded.bin",
            base64_content=base64.b64encode(b"small payload").decode(),
            content_mime_type="application/octet-stream",
        )

        assert "new-file" in result


class TestLocalFileLimit:
    @pytest.mark.asyncio
    async def test_oversized_local_file_is_rejected(
        self, drive_service, tmp_path, monkeypatch
    ):
        source = tmp_path / "big.bin"
        source.write_bytes(b"y" * 64)
        monkeypatch.setattr(drive_tools, "MAX_DRIVE_STREAMED_BYTES", 16)
        monkeypatch.setattr(drive_tools, "validate_file_path", lambda _p: source)
        monkeypatch.setattr(drive_tools, "get_transport_mode", lambda: "stdio")

        with pytest.raises(Exception, match="exceeds the 16 byte limit"):
            await create_drive_file(
                service=drive_service,
                user_google_email="user@example.com",
                file_name="big.bin",
                fileUrl=source.as_uri(),
            )

    @pytest.mark.asyncio
    async def test_local_file_is_streamed_not_read_into_memory(
        self, drive_service, tmp_path, monkeypatch
    ):
        """Finding 11: read_bytes() pulled the whole file in; it must not be used."""
        source = tmp_path / "ok.bin"
        source.write_bytes(b"z" * 32)
        monkeypatch.setattr(drive_tools, "MAX_DRIVE_STREAMED_BYTES", 1024)
        monkeypatch.setattr(drive_tools, "validate_file_path", lambda _p: source)
        monkeypatch.setattr(drive_tools, "get_transport_mode", lambda: "stdio")

        media_sources = []

        class _RecordingMedia:
            def __init__(self, fd, **kwargs):  # noqa: ARG002
                media_sources.append(fd)

        with patch.object(drive_tools, "MediaIoBaseUpload", _RecordingMedia):
            result = await create_drive_file(
                service=drive_service,
                user_google_email="user@example.com",
                file_name="ok.bin",
                fileUrl=source.as_uri(),
            )

        assert "new-file" in result
        # The upload was handed the open file, not a BytesIO of its contents.
        assert len(media_sources) == 1
        assert not isinstance(media_sources[0], drive_tools.io.BytesIO)
        assert getattr(media_sources[0], "name", None) == str(source)
