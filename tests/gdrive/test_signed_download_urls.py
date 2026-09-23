"""get_drive_file_download_url hands out a signed URL instead of downloading, and
says so loudly when it cannot."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

import core.signed_downloads as sd
from gdrive.drive_tools import get_drive_file_download_url

USER = "user@example.com"
URL = "https://mcp.example.com/attachments/signed/TOKEN"


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _service(mime="application/vnd.google-apps.document", name="Plan"):
    service = Mock()
    service.files().get().execute.return_value = {"name": name, "mimeType": mime}
    return service


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_SIGNED_DOWNLOAD_URLS", "true")
    monkeypatch.setattr(sd, "get_transport_mode", lambda: "streamable-http")


@pytest.mark.asyncio
@patch("gdrive.drive_tools._download_file_to_temp", new_callable=AsyncMock)
async def test_native_file_signs_export_type_and_skips_download(download, enabled):
    with patch.object(
        sd, "offer_url", new_callable=AsyncMock, return_value=sd.Offer(URL, 900)
    ) as offer:
        result = await _unwrap(get_drive_file_download_url)(
            service=_service(),
            user_google_email=USER,
            file_id="doc-1",
            export_format="docx",
        )

    assert URL in result and "~15 minutes" in result
    download.assert_not_called()
    kwargs = offer.call_args.kwargs
    assert kwargs["source"] == "drive"
    assert kwargs["ref"]["fid"] == "doc-1"
    assert kwargs["ref"]["emt"].endswith("wordprocessingml.document")
    assert kwargs["filename"] == "Plan.docx"
    assert offer.call_args.args == (USER,)


@pytest.mark.asyncio
@patch("gdrive.drive_tools._download_file_to_temp", new_callable=AsyncMock)
async def test_binary_file_has_no_export_claim(download, enabled):
    with patch.object(
        sd, "offer_url", new_callable=AsyncMock, return_value=sd.Offer(URL, 900)
    ) as offer:
        await _unwrap(get_drive_file_download_url)(
            service=_service("video/mp4", "clip.mp4"),
            user_google_email=USER,
            file_id="v-1",
        )
    assert offer.call_args.kwargs["ref"] == {"fid": "v-1"}
    assert offer.call_args.kwargs["mime_type"] == "video/mp4"


@pytest.mark.asyncio
@patch("gdrive.drive_tools._download_file_to_temp", new_callable=AsyncMock)
async def test_stateless_fallback_is_loud_when_url_cannot_be_minted(
    download, enabled, monkeypatch, tmp_path
):
    import gdrive.drive_tools as drive_tools

    monkeypatch.setattr(drive_tools, "is_stateless_mode", lambda: True)
    blob = tmp_path / "clip.mp4"
    blob.write_bytes(b"x" * 300)
    download.return_value = blob

    with patch.object(
        sd,
        "offer_url",
        new_callable=AsyncMock,
        return_value=sd.Offer(reason=sd.NO_CREDENTIALS),
    ):
        result = await _unwrap(get_drive_file_download_url)(
            service=_service("video/mp4", "clip.mp4"),
            user_google_email=USER,
            file_id="v-1",
        )

    assert "downloaded successfully" not in result
    assert "NO download URL could be issued" in result
    assert sd.NO_CREDENTIALS in result
    assert not blob.exists()


@pytest.mark.asyncio
@patch("gdrive.drive_tools._download_file_to_temp", new_callable=AsyncMock)
async def test_stateless_wording_unchanged_when_feature_off(
    download, monkeypatch, tmp_path
):
    import gdrive.drive_tools as drive_tools

    monkeypatch.delenv("WORKSPACE_MCP_SIGNED_DOWNLOAD_URLS", raising=False)
    monkeypatch.setattr(drive_tools, "is_stateless_mode", lambda: True)
    blob = tmp_path / "clip.mp4"
    blob.write_bytes(b"x" * 300)
    download.return_value = blob

    result = await _unwrap(get_drive_file_download_url)(
        service=_service("video/mp4", "clip.mp4"), user_google_email=USER, file_id="v-1"
    )

    assert result.startswith("File downloaded successfully!")
    assert "signed" not in result.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mime",
    [
        "application/vnd.google-apps.folder",
        "application/vnd.google-apps.form",
        "application/vnd.google-apps.script",
    ],
)
@patch("gdrive.drive_tools._download_file_to_temp", new_callable=AsyncMock)
async def test_items_with_no_bytes_get_no_link(download, enabled, mime):
    """A folder or an unexportable native type would only fail when the link is
    fetched; the normal path reports it now instead."""
    download.side_effect = RuntimeError("fileNotDownloadable")
    with patch.object(sd, "offer_url", new_callable=AsyncMock) as offer:
        with pytest.raises(RuntimeError):
            await _unwrap(get_drive_file_download_url)(
                service=_service(mime, "thing"), user_google_email=USER, file_id="x"
            )
    offer.assert_not_called()
