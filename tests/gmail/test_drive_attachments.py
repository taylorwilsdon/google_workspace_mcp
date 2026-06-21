"""Tests for Drive-sourced Gmail attachments and the local-path remote guard.

``_resolve_drive_attachments`` lets a caller reference a Google Drive file by id
instead of a server-local path (which is meaningless when the server runs
remotely). The file is either downloaded and attached as binary, or — with
``as_link`` — surfaced as a share link appended to the body.
"""

import asyncio
from unittest.mock import Mock, patch

from gmail.gmail_tools import (
    _resolve_drive_attachments,
    _append_drive_links_to_body,
)


def _drive_service_mock(meta, *, media=b"", export=b""):
    svc = Mock()
    svc.files().get().execute.return_value = meta
    svc.files().get_media().execute.return_value = media
    svc.files().export().execute.return_value = export
    return svc


def test_drive_attachment_downloaded_as_binary():
    """A binary Drive file is downloaded via get_media and attached as bytes."""
    drive = _drive_service_mock(
        {"name": "report.pdf", "mimeType": "application/pdf"},
        media=b"%PDF-bytes",
    )
    with patch("gmail.gmail_tools.build", return_value=drive), patch(
        "gmail.gmail_tools.get_transport_mode", return_value="streamable-http"
    ):
        resolved, links = asyncio.run(
            _resolve_drive_attachments(Mock(), [{"drive_file_id": "f1"}])
        )

    assert links == []
    assert resolved[0]["_resolved_bytes"] == b"%PDF-bytes"
    assert resolved[0]["filename"] == "report.pdf"
    assert resolved[0]["mime_type"] == "application/pdf"


def test_native_drive_file_exported_to_pdf():
    """Native Docs/Sheets/Slides are exported to PDF (get_media can't fetch them)."""
    drive = _drive_service_mock(
        {"name": "Spec", "mimeType": "application/vnd.google-apps.document"},
        export=b"%PDF-export",
    )
    with patch("gmail.gmail_tools.build", return_value=drive), patch(
        "gmail.gmail_tools.get_transport_mode", return_value="streamable-http"
    ):
        resolved, links = asyncio.run(
            _resolve_drive_attachments(Mock(), [{"drive_file_id": "doc1"}])
        )

    assert resolved[0]["_resolved_bytes"] == b"%PDF-export"
    assert resolved[0]["filename"] == "Spec.pdf"
    assert resolved[0]["mime_type"] == "application/pdf"
    drive.files.return_value.export.assert_called()


def test_drive_attachment_as_link_goes_to_body():
    """as_link surfaces a share link instead of attaching bytes."""
    drive = _drive_service_mock(
        {
            "name": "Deck",
            "mimeType": "application/vnd.google-apps.presentation",
            "webViewLink": "https://drive.google.com/file/d/deck1/view",
        }
    )
    with patch("gmail.gmail_tools.build", return_value=drive), patch(
        "gmail.gmail_tools.get_transport_mode", return_value="streamable-http"
    ):
        resolved, links = asyncio.run(
            _resolve_drive_attachments(
                Mock(), [{"drive_file_id": "deck1", "as_link": True}]
            )
        )

    assert resolved == []  # nothing attached as bytes
    assert links == [{"name": "Deck", "url": "https://drive.google.com/file/d/deck1/view"}]


def test_local_path_rejected_in_remote_mode():
    """A local 'path' attachment becomes an error entry when running remotely."""
    with patch(
        "gmail.gmail_tools.get_transport_mode", return_value="streamable-http"
    ):
        resolved, links = asyncio.run(
            _resolve_drive_attachments(Mock(), [{"path": "/tmp/secret.pdf"}])
        )

    assert links == []
    assert resolved[0].get("error")


def test_local_path_allowed_in_stdio_mode():
    """In stdio mode a local 'path' attachment passes through untouched."""
    with patch("gmail.gmail_tools.get_transport_mode", return_value="stdio"):
        att = {"path": "/tmp/report.pdf"}
        resolved, links = asyncio.run(_resolve_drive_attachments(Mock(), [att]))

    assert links == []
    assert resolved == [att]


def test_append_drive_links_plain_and_html():
    links = [{"name": "Deck", "url": "https://x/deck"}]
    plain = _append_drive_links_to_body("Hi", links, "plain")
    assert "https://x/deck" in plain and "Deck" in plain
    html = _append_drive_links_to_body("<p>Hi</p>", links, "html")
    assert '<a href="https://x/deck">Deck</a>' in html
