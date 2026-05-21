import base64
import os
import sys

import pytest


def test_urlsafe_b64decode_already_handles_crlf():
    """Verify Python's urlsafe_b64decode ignores embedded CR/LF without manual stripping."""
    original = b"Testdata"
    b64 = base64.urlsafe_b64encode(original).decode()

    assert base64.urlsafe_b64decode(b64 + "\n") == original
    assert base64.urlsafe_b64decode(b64[:4] + "\r\n" + b64[4:]) == original
    assert base64.urlsafe_b64decode(b64[:4] + "\r\r\n" + b64[4:]) == original


def test_os_open_without_o_binary_corrupts_on_windows(tmp_path):
    """On Windows, os.open without O_BINARY translates LF to CRLF in written bytes."""
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

    tmp = str(tmp_path / "test_no_binary.bin")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)

    with open(tmp, "rb") as f:
        written = f.read()

    if sys.platform == "win32":
        assert written != payload, "Expected corruption without O_BINARY on Windows"
        assert len(written) > len(payload)
    else:
        assert written == payload


def test_os_open_with_o_binary_preserves_bytes(tmp_path):
    """os.open with O_BINARY writes binary data correctly on all platforms."""
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

    tmp = str(tmp_path / "test_with_binary.bin")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)

    fd = os.open(tmp, flags, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)

    with open(tmp, "rb") as f:
        written = f.read()

    assert written == payload


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    """Create an AttachmentStorage that writes to a temp directory."""
    import core.attachment_storage as storage_module

    monkeypatch.setattr(storage_module, "STORAGE_DIR", tmp_path)
    return storage_module.AttachmentStorage()


def test_save_attachment_uses_binary_mode(isolated_storage):
    """Verify that AttachmentStorage.save_attachment writes files in binary mode."""
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    b64_data = base64.urlsafe_b64encode(payload).decode()

    result = isolated_storage.save_attachment(
        b64_data, filename="test.png", mime_type="image/png"
    )

    with open(result.path, "rb") as f:
        saved_bytes = f.read()

    assert saved_bytes == payload, (
        f"Binary corruption detected: wrote {len(payload)} bytes, "
        f"read back {len(saved_bytes)} bytes"
    )


@pytest.mark.parametrize(
    ("filename", "expected_prefix"),
    [
        ("RE: Foo/Bar?.eml", "RE_ Foo_Bar_"),
        ("FW: Client\\Matter*.eml", "FW_ Client_Matter_"),
        ("CON.txt", "_CON_"),
        ("report. ", "report_"),
        ("...   ", "attachment_"),
    ],
)
def test_save_attachment_sanitizes_windows_reserved_filenames(
    isolated_storage, filename, expected_prefix
):
    """Attachment filenames should be safe on Windows and POSIX filesystems."""
    payload = b"safe filename check"
    b64_data = base64.urlsafe_b64encode(payload).decode()

    result = isolated_storage.save_attachment(b64_data, filename=filename)
    saved_name = os.path.basename(result.path)

    assert saved_name.startswith(expected_prefix)
    assert not any(char in saved_name for char in '<>:"/\\|?*')
    assert saved_name == saved_name.rstrip(". ")

    with open(result.path, "rb") as f:
        saved_bytes = f.read()

    assert saved_bytes == payload


def test_save_attachment_metadata_filename_matches_saved_file(isolated_storage):
    """Attachment metadata should report the on-disk filename."""
    payload = b"metadata filename check"
    b64_data = base64.urlsafe_b64encode(payload).decode()

    result = isolated_storage.save_attachment(
        b64_data, filename="RE: Foo?.eml", mime_type="message/rfc822"
    )
    saved_name = os.path.basename(result.path)
    metadata = isolated_storage.get_attachment_metadata(result.file_id)

    assert metadata["filename"] == saved_name
    assert metadata["original_filename"] == "RE: Foo?.eml"
    assert metadata["filename"].startswith("RE_ Foo_")
    assert metadata["filename"].endswith(".eml")
    assert ":" not in metadata["filename"]
    assert "?" not in metadata["filename"]


@pytest.mark.parametrize(
    "payload",
    [
        b"\x89PNG\r\n\x1a\n" + b"\xff" * 200,  # PNG header
        b"%PDF-1.7\n" + b"\x00" * 200,  # PDF header
        bytes(range(256)) * 4,  # All byte values
    ],
)
def test_save_attachment_preserves_various_binary_formats(isolated_storage, payload):
    """Ensure binary integrity for payloads containing LF/CR bytes."""
    b64_data = base64.urlsafe_b64encode(payload).decode()
    result = isolated_storage.save_attachment(b64_data, filename="test.bin")

    with open(result.path, "rb") as f:
        saved_bytes = f.read()

    assert saved_bytes == payload
