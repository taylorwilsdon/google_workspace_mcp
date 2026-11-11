"""get_drive_file_content returns long text a page at a time."""

import base64
from unittest.mock import Mock, patch

import pytest

from core.utils import UserInputError
from gdrive.drive_tools import _paged_content_cache, get_drive_file_content

_HEADER = (
    'File: "notes.txt" (ID: file123, Type: text/plain)\n'
    "Link: https://drive.google.com/file/file123\n\n--- CONTENT ---\n"
)


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class _FakeDownloader:
    def __init__(self, fh, data):
        fh.write(data)
        fh.seek(0)

    def next_chunk(self):
        return None, True


async def _get_content(
    content: bytes,
    mime_type: str = "text/plain",
    *,
    version: str | None = None,
    can_download: bool = True,
    downloads: list | None = None,
    user_google_email: str = "user@example.com",
    resolve_calls: list | None = None,
    version_after_download: str | None = None,
    **kwargs,
):
    metadata = {
        "name": "notes.txt",
        "mimeType": mime_type,
        "webViewLink": "https://drive.google.com/file/file123",
        "capabilities": {"canDownload": can_download},
    }
    if version is not None:
        metadata["version"] = version

    def downloader(fh, req, chunksize=None):
        if downloads is not None:
            downloads.append(req)
        return _FakeDownloader(fh, content)

    async def resolve(service, file_id, extra_fields=None):
        if resolve_calls is not None:
            resolve_calls.append(extra_fields)
        return "file123", metadata

    service = Mock()
    service.files().get_media.return_value = "req"
    service.files().get().execute.return_value = {
        "version": version_after_download or version
    }
    with (
        patch("gdrive.drive_tools.resolve_drive_item", side_effect=resolve),
        patch("core.file_limits.MediaIoBaseDownload", side_effect=downloader),
    ):
        return await _unwrap(get_drive_file_content)(
            service=service,
            user_google_email=user_google_email,
            file_id="file123",
            **kwargs,
        )


@pytest.fixture
def limit(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_CONTENT_CHARS", "100")


@pytest.mark.asyncio
async def test_long_text_is_cut_off_with_a_note_on_how_to_read_on(limit):
    text = "".join(f"row {i:03d}\n" for i in range(30))  # 240 characters

    result = await _get_content(text.encode())

    assert result == _HEADER + text[:96] + (
        "\n\n[Truncated: showing characters 0 to 96 of 240; 144 more follow "
        "(default page size 100, set by WORKSPACE_MCP_MAX_CONTENT_CHARS). Call "
        "again with offset=96 to read on, or pass full_content=True for the whole "
        "file.]"
    )


@pytest.mark.asyncio
async def test_full_content_skips_the_limit(limit):
    text = "".join(f"row {i:03d}\n" for i in range(30))

    assert await _get_content(text.encode(), full_content=True) == _HEADER + text
    assert (
        await _get_content(text.encode(), offset=96, full_content=True)
        == _HEADER + text[96:]
    )


@pytest.mark.asyncio
async def test_full_content_is_not_kept(limit):
    await _get_content(("x" * 240).encode(), version="7", full_content=True)

    assert _paged_content_cache.get(("user@example.com", "file123", "7", None)) is None


@pytest.mark.asyncio
async def test_offset_reads_on_from_where_the_last_page_ended(limit):
    text = "".join(f"row {i:03d}\n" for i in range(30))

    second = await _get_content(text.encode(), offset=96)
    last = await _get_content(text.encode(), offset=192)

    assert second.startswith(_HEADER + text[96:192] + "\n\n[Truncated: ")
    assert "offset=192" in second
    assert last == _HEADER + text[192:]


@pytest.mark.asyncio
async def test_text_at_the_limit_is_returned_whole(limit):
    text = "x" * 100

    assert await _get_content(text.encode()) == _HEADER + text


@pytest.mark.asyncio
async def test_default_limit_applies_when_unset(monkeypatch):
    monkeypatch.delenv("WORKSPACE_MCP_MAX_CONTENT_CHARS", raising=False)
    text = "word " * 20_000  # 100,000 characters

    result = await _get_content(text.encode())

    assert "[Truncated: showing characters 0 to 40,000 of 100,000;" in result


@pytest.mark.asyncio
async def test_zero_turns_the_limit_off(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_CONTENT_CHARS", "0")
    text = "word " * 20_000

    assert await _get_content(text.encode()) == _HEADER + text


@pytest.mark.asyncio
async def test_negative_offset_is_rejected(limit):
    with pytest.raises(UserInputError, match="offset must be >= 0"):
        await _get_content(b"abc", offset=-1)


@pytest.mark.asyncio
async def test_base64_image_over_the_limit_points_at_the_download_url(limit):
    """Part of a base64 image is no use, so it is replaced, not paged."""
    bmp = b"BM" + b"\x00" * 200

    result = await _get_content(bmp, mime_type="image/bmp")

    assert result.endswith(
        "[Image is 202 bytes, 296 characters as base64, over the default page "
        "size of 100 (set by WORKSPACE_MCP_MAX_CONTENT_CHARS). Pass "
        "full_content=True to get it anyway, or use get_drive_file_download_url "
        "to get the file instead.]"
    )


@pytest.mark.asyncio
async def test_full_content_returns_a_base64_image_over_the_page_size(limit):
    bmp = b"BM" + b"\x00" * 200

    result = await _get_content(bmp, mime_type="image/bmp", full_content=True)

    assert result.endswith(
        "[base64_image:image/bmp]" + base64.b64encode(bmp).decode("ascii")
    )


@pytest.mark.asyncio
async def test_base64_image_under_the_limit_ignores_offset(limit):
    bmp = b"BM" + b"\x00" * 40

    result = await _get_content(bmp, mime_type="image/bmp", offset=10)

    assert result.endswith(
        "[base64_image:image/bmp]" + base64.b64encode(bmp).decode("ascii")
    )


@pytest.mark.asyncio
async def test_attached_image_ignores_offset(limit):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

    result = await _get_content(png, mime_type="image/png", offset=5)

    text, image = result.content
    assert text.text == _HEADER.replace("text/plain", "image/png") + (
        "[Image attached as image content - 58 bytes]"
    )
    assert image.mime_type == "image/png"


@pytest.mark.asyncio
async def test_later_svg_pages_do_not_repeat_its_images(limit):
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 40).decode()
    svg = (
        f'<svg><image href="data:image/png;base64,{png}"/>' + "<g/>" * 60 + "</svg>"
    ).encode()

    first = await _get_content(svg, mime_type="image/svg+xml")
    second = await _get_content(svg, mime_type="image/svg+xml", offset=100)

    assert len(first.content) == 2  # the page's text, then the image
    assert "[Truncated: " in first.content[0].text
    assert isinstance(second, str)  # text only


_LONG_TEXT = "".join(f"row {i:03d}\n" for i in range(30))  # 240 characters


def _versioned_header(version: str) -> str:
    return _HEADER.replace("text/plain)", f"text/plain, Version: {version})")


@pytest.mark.asyncio
async def test_every_page_names_the_files_version(limit):
    for offset in (0, 96):
        result = await _get_content(_LONG_TEXT.encode(), version="7", offset=offset)

        assert result.startswith(_versioned_header("7"))


@pytest.mark.asyncio
async def test_reading_on_reuses_the_text_instead_of_downloading_again(limit):
    downloads = []

    pages = [
        await _get_content(
            _LONG_TEXT.encode(), version="7", downloads=downloads, offset=offset
        )
        for offset in (0, 96, 192)
    ]

    assert len(downloads) == 1
    assert pages[2] == _versioned_header("7") + _LONG_TEXT[192:]


@pytest.mark.asyncio
async def test_a_new_version_is_downloaded_again(limit):
    downloads = []

    await _get_content(_LONG_TEXT.encode(), version="7", downloads=downloads)
    changed = "changed\n" + _LONG_TEXT
    result = await _get_content(
        changed.encode(), version="8", downloads=downloads, offset=96
    )

    assert len(downloads) == 2
    assert result.startswith(
        _versioned_header("8") + changed[96:192] + "\n\n[Truncated: "
    )


@pytest.mark.asyncio
async def test_the_first_page_is_always_downloaded(limit):
    downloads = []

    for _ in range(2):
        await _get_content(_LONG_TEXT.encode(), version="7", downloads=downloads)

    assert len(downloads) == 2


@pytest.mark.asyncio
async def test_text_is_not_kept_without_a_version(limit):
    downloads = []

    await _get_content(_LONG_TEXT.encode(), downloads=downloads)
    await _get_content(_LONG_TEXT.encode(), downloads=downloads, offset=96)

    assert len(downloads) == 2


@pytest.mark.asyncio
async def test_text_that_fits_on_one_page_is_not_kept(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_CONTENT_CHARS", "1000")

    await _get_content(_LONG_TEXT.encode(), version="7")

    assert _paged_content_cache.get(("user@example.com", "file123", "7", None)) is None


@pytest.mark.asyncio
async def test_text_is_not_kept_in_stateless_mode(limit):
    downloads = []

    with patch("gdrive.drive_tools.is_stateless_mode", return_value=True):
        await _get_content(_LONG_TEXT.encode(), version="7", downloads=downloads)
        await _get_content(
            _LONG_TEXT.encode(), version="7", downloads=downloads, offset=96
        )

    assert len(downloads) == 2


@pytest.mark.asyncio
async def test_the_files_version_and_download_capability_are_fetched(limit):
    resolve_calls = []

    await _get_content(_LONG_TEXT.encode(), resolve_calls=resolve_calls)

    fields = [field.strip() for field in resolve_calls[0].split(",")]
    assert "version" in fields
    assert "capabilities(canDownload)" in fields


@pytest.mark.asyncio
async def test_kept_text_is_not_read_once_download_is_restricted(limit):
    downloads = []

    await _get_content(_LONG_TEXT.encode(), version="7", downloads=downloads)
    await _get_content(
        _LONG_TEXT.encode(),
        version="7",
        can_download=False,
        downloads=downloads,
        offset=96,
    )

    assert len(downloads) == 2


@pytest.mark.asyncio
async def test_text_kept_for_one_user_is_not_read_by_another(limit):
    downloads = []

    await _get_content(_LONG_TEXT.encode(), version="7", downloads=downloads)
    await _get_content(
        _LONG_TEXT.encode(),
        version="7",
        downloads=downloads,
        user_google_email="other@example.com",
        offset=96,
    )

    assert len(downloads) == 2


@pytest.mark.asyncio
async def test_zero_cache_bytes_keeps_nothing(limit, monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_PAGED_CONTENT_CACHE_BYTES", "0")
    downloads = []

    await _get_content(_LONG_TEXT.encode(), version="7", downloads=downloads)
    await _get_content(_LONG_TEXT.encode(), version="7", downloads=downloads, offset=96)

    assert len(downloads) == 2


@pytest.mark.asyncio
async def test_a_base64_image_is_not_kept(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_CONTENT_CHARS", "100")
    downloads = []
    bmp = b"BM" + b"\x00" * 200

    await _get_content(bmp, mime_type="image/bmp", version="7", downloads=downloads)
    await _get_content(
        bmp, mime_type="image/bmp", version="7", downloads=downloads, offset=10
    )

    assert len(downloads) == 2


@pytest.mark.asyncio
async def test_a_page_read_across_a_version_change_asks_for_a_restart(limit):
    for offset in (0, 96):
        result = await _get_content(
            _LONG_TEXT.encode(),
            version="7",
            version_after_download="8",
            offset=offset,
        )

        assert result == (
            'File "notes.txt" (ID: file123) changed while it was being read. '
            "Call again with offset=0 to read the current version from the start."
        )
    assert _paged_content_cache.get(("user@example.com", "file123", "7", None)) is None


@pytest.mark.asyncio
async def test_a_single_page_read_skips_the_version_recheck(limit):
    result = await _get_content(b"short", version="7", version_after_download="8")

    assert result == _versioned_header("7") + "short"
