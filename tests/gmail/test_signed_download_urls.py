"""Gmail tools hand out signed download URLs instead of fetching the bytes, and say
so loudly when they cannot."""

import base64
from unittest.mock import AsyncMock, Mock, patch

import pytest

import core.signed_downloads as sd
from gmail.gmail_tools import _export_full_message, get_gmail_attachment_content

USER = "user@example.com"
URL = "https://mcp.example.com/attachments/signed/TOKEN"
HEADERS = {"Subject": "Quarterly numbers", "From": "cfo@example.com"}


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _service(payload=b"bytes", filename="report.pdf"):
    service = Mock()
    service.users().messages().attachments().get().execute.return_value = {
        "size": len(payload),
        "data": base64.urlsafe_b64encode(payload).decode(),
    }
    service.users().messages().get().execute.return_value = {
        "payload": {
            "parts": [
                {
                    "filename": filename,
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "att-1", "size": len(payload)},
                }
            ]
        }
    }
    return service


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_SIGNED_DOWNLOAD_URLS", "true")
    monkeypatch.setattr(sd, "get_transport_mode", lambda: "streamable-http")
    monkeypatch.delenv("WORKSPACE_MCP_MAX_FILE_BYTES", raising=False)


@pytest.mark.asyncio
async def test_attachment_returns_signed_url_without_downloading(enabled):
    service = _service()
    with patch.object(
        sd, "offer_url", new_callable=AsyncMock, return_value=sd.Offer(URL, 540)
    ) as offer:
        result = await _unwrap(get_gmail_attachment_content)(
            service=service,
            message_id="msg-1",
            attachment_id="att-1",
            user_google_email=USER,
        )

    assert URL in result and "~9 minutes" in result
    assert "Filename: report.pdf" in result
    service.users().messages().attachments().get().execute.assert_not_called()
    assert offer.call_args.kwargs["source"] == "gmail"
    assert offer.call_args.kwargs["ref"] == {"mid": "msg-1", "aid": "att-1"}
    assert offer.call_args.kwargs["filename"] == "report.pdf"
    assert offer.call_args.args == (USER,)


@pytest.mark.asyncio
async def test_return_base64_bypasses_signed_url(enabled, monkeypatch):
    """Callers who ask for inline bytes cannot reach a URL; give them the bytes."""
    monkeypatch.setattr("auth.oauth_config.is_stateless_mode", lambda: True)
    with patch.object(sd, "offer_url", new_callable=AsyncMock) as offer:
        result = await _unwrap(get_gmail_attachment_content)(
            service=_service(b"hello"),
            message_id="msg-1",
            attachment_id="att-1",
            user_google_email=USER,
            return_base64=True,
        )
    offer.assert_not_called()
    assert base64.b64encode(b"hello").decode() in result
    assert "NO download URL" not in result


@pytest.mark.asyncio
async def test_stateless_fallback_is_loud_when_url_cannot_be_minted(
    enabled, monkeypatch
):
    monkeypatch.setattr("auth.oauth_config.is_stateless_mode", lambda: True)
    with patch.object(
        sd,
        "offer_url",
        new_callable=AsyncMock,
        return_value=sd.Offer(reason=sd.NO_CREDENTIALS),
    ):
        result = await _unwrap(get_gmail_attachment_content)(
            service=_service(),
            message_id="msg-1",
            attachment_id="att-1",
            user_google_email=USER,
        )
    assert "downloaded successfully" not in result
    assert "NO download URL could be issued" in result
    assert sd.NO_CREDENTIALS in result


@pytest.mark.asyncio
async def test_stateless_wording_unchanged_when_feature_off(monkeypatch):
    monkeypatch.delenv("WORKSPACE_MCP_SIGNED_DOWNLOAD_URLS", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_MAX_FILE_BYTES", raising=False)
    monkeypatch.setattr("auth.oauth_config.is_stateless_mode", lambda: True)
    result = await _unwrap(get_gmail_attachment_content)(
        service=_service(),
        message_id="msg-1",
        attachment_id="att-1",
        user_google_email=USER,
    )
    assert result.startswith("Attachment downloaded successfully!")
    assert "signed" not in result.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body_format, extension",
    [("raw", ".eml"), ("html", ".html"), ("text", ".txt")],
)
async def test_full_export_offers_signed_url_and_skips_fetch(
    enabled, body_format, extension
):
    service = Mock()
    with patch.object(
        sd, "offer_url", new_callable=AsyncMock, return_value=sd.Offer(URL, 900)
    ) as offer:
        result = await _export_full_message(
            service, "msg-1", HEADERS, body_format, user_google_email=USER
        )

    assert "FULL MESSAGE EXPORT (download link)" in result and URL in result
    assert "Content is NOT included" in result
    service.users.assert_not_called()  # the route fetches at download time
    kwargs = offer.call_args.kwargs
    assert kwargs["source"] == "gmail_message"
    assert kwargs["ref"] == {"mid": "msg-1", "fmt": body_format}
    # The route adds the extension of what it actually rendered.
    assert kwargs["filename"] == "Quarterly numbers"
    assert extension  # kept in the parametrization for the id
    if body_format == "html":
        assert "plain text if the message has no HTML part" in result


@pytest.mark.asyncio
async def test_full_export_falls_back_to_upstream_path(enabled, monkeypatch):
    """No URL: upstream's stateless inline delivery, complete and untruncated."""
    import gmail.gmail_tools as gmail_tools

    monkeypatch.setattr(gmail_tools, "is_stateless_mode", lambda: True)
    service = Mock()
    service.users().messages().get().execute.return_value = {"raw": "SGVsbG8gd29ybGQ="}

    with patch.object(
        sd,
        "offer_url",
        new_callable=AsyncMock,
        return_value=sd.Offer(reason=sd.NO_CREDENTIALS),
    ):
        result = await _export_full_message(
            service, "msg-3", HEADERS, "raw", user_google_email=USER
        )

    assert "Hello world" in result
    assert "signed URL" not in result


class _Saved:
    path, file_id = "/nonexistent/Quarterly numbers.eml", "file-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("stateless", [True, False])
async def test_full_export_fallback_is_loud_when_url_cannot_be_minted(
    enabled, monkeypatch, stateless
):
    """Same contract as the attachment and Drive tools: signed links on, offer
    refused, so the fallback (inline or stored) says no signed URL was issued."""
    import gmail.gmail_tools as gmail_tools

    monkeypatch.setattr(gmail_tools, "is_stateless_mode", lambda: stateless)
    monkeypatch.setattr(gmail_tools, "get_transport_mode", lambda: "streamable-http")
    storage = Mock()
    storage.save_attachment_bytes.return_value = _Saved()
    monkeypatch.setattr(gmail_tools, "get_attachment_storage", lambda: storage)
    monkeypatch.setattr(gmail_tools, "get_attachment_url", lambda fid: f"/a/{fid}")
    service = Mock()
    service.users().messages().get().execute.return_value = {"raw": "SGVsbG8gd29ybGQ="}

    with patch.object(
        sd,
        "offer_url",
        new_callable=AsyncMock,
        return_value=sd.Offer(reason=sd.NO_CREDENTIALS),
    ):
        result = await _export_full_message(
            service, "msg-4", HEADERS, "raw", user_google_email=USER
        )

    assert "Error" not in result
    assert sd.NO_CREDENTIALS in result


@pytest.mark.asyncio
async def test_full_export_fallback_has_no_note_when_feature_off(monkeypatch):
    import gmail.gmail_tools as gmail_tools

    monkeypatch.delenv("WORKSPACE_MCP_SIGNED_DOWNLOAD_URLS", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_MAX_FILE_BYTES", raising=False)
    monkeypatch.setattr(gmail_tools, "is_stateless_mode", lambda: True)
    service = Mock()
    service.users().messages().get().execute.return_value = {"raw": "SGVsbG8gd29ybGQ="}

    result = await _export_full_message(
        service, "msg-5", HEADERS, "raw", user_google_email=USER
    )

    assert "Hello world" in result
    assert "No signed download URL" not in result


PDF_NAME = "BRN94DDF87494B4_006201.pdf"


def _scanner_mail_service(payload=b"%PDF-1.4 scan", *, pdf_name=PDF_NAME):
    """A scanned PDF behind a signature block: three inline images first, the
    PDF last. The metadata fetch returns attachment IDs Gmail has rotated since
    the listing the caller is holding (``old-*``), so nothing matches by ID."""

    def part(mime, name, aid, size):
        return {
            "mimeType": mime,
            "filename": name,
            "body": {"attachmentId": aid, "size": size},
        }

    service = Mock()
    service.users().messages().attachments().get().execute.return_value = {
        "size": len(payload),
        "data": base64.urlsafe_b64encode(payload).decode(),
    }
    service.users().messages().get().execute.return_value = {
        "payload": {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/related",
                    "parts": [
                        {
                            "mimeType": "multipart/alternative",
                            "parts": [
                                {"mimeType": "text/plain", "body": {"size": 120}},
                                {"mimeType": "text/html", "body": {"size": 2400}},
                            ],
                        },
                        part("image/png", "image001.png", "new-img1", 4210),
                        part("image/png", "image002.png", "new-img2", 3890),
                        part("image/jpeg", "image003.jpg", "new-img3", 9012),
                    ],
                },
                part("application/pdf", pdf_name, "new-pdf", len(payload)),
            ],
        }
    }
    return service


@pytest.mark.asyncio
async def test_signed_url_names_the_attachment_after_gmail_rotated_ids(enabled):
    """Live case: the listing named the PDF, but the link came back as
    'unknown'. With no size cap there is no pre-download metadata pass, and the
    name resolver was handed neither the index nor a size, so a rotated ID left
    it with nothing to match on among the four named parts."""
    with patch.object(
        sd, "offer_url", new_callable=AsyncMock, return_value=sd.Offer(URL, 540)
    ) as offer:
        result = await _unwrap(get_gmail_attachment_content)(
            service=_scanner_mail_service(),
            message_id="msg-1",
            attachment_id="old-pdf",
            user_google_email=USER,
            attachment_index=3,
        )

    assert f"Filename: {PDF_NAME}" in result
    assert "unknown" not in result
    assert offer.call_args.kwargs["filename"] == PDF_NAME
    assert offer.call_args.kwargs["mime_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_signed_url_says_when_gmail_gave_no_name(enabled):
    """A part with no filename is not 'unknown': the link is served as
    'attachment', and the result says so instead of printing a placeholder."""
    service = Mock()
    service.users().messages().get().execute.return_value = {
        "payload": {
            "mimeType": "application/octet-stream",
            "body": {"attachmentId": "new-1", "size": 10},
        }
    }
    with patch.object(
        sd, "offer_url", new_callable=AsyncMock, return_value=sd.Offer(URL, 540)
    ) as offer:
        result = await _unwrap(get_gmail_attachment_content)(
            service=service,
            message_id="msg-1",
            attachment_id="old-1",
            user_google_email=USER,
        )

    assert "unknown" not in result
    assert "Filename: attachment (Gmail gave this part no name" in result
    assert offer.call_args.kwargs["filename"] is None
    # The message's only attachment part is unambiguous even unnamed, so the
    # link names it by its current ID rather than the caller's rotated one.
    assert offer.call_args.kwargs["ref"] == {"mid": "msg-1", "aid": "new-1"}


@pytest.mark.asyncio
async def test_signed_url_mints_against_the_id_the_index_selected(enabled):
    """The link must name the part Gmail has now, not the one the caller asked
    for. The route hands the token's ``aid`` straight to Gmail; minting the
    caller's rotated ID produces a link that resolves a name correctly and then
    502s on fetch, minutes after the tool reported success."""
    with patch.object(
        sd, "offer_url", new_callable=AsyncMock, return_value=sd.Offer(URL, 540)
    ) as offer:
        await _unwrap(get_gmail_attachment_content)(
            service=_scanner_mail_service(),
            message_id="msg-1",
            attachment_id="old-pdf",
            user_google_email=USER,
            attachment_index=3,
        )

    assert offer.call_args.kwargs["ref"] == {"mid": "msg-1", "aid": "new-pdf"}


@pytest.mark.asyncio
async def test_signed_url_mints_against_the_only_attachment_after_rotation(enabled):
    """Same for the only-attachment fallback: one attachment is unambiguous, so
    its current ID is known even with no index and no size."""
    service = Mock()
    service.users().messages().get().execute.return_value = {
        "payload": {
            "parts": [
                {
                    "filename": "report.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "new-only", "size": 4096},
                }
            ]
        }
    }
    with patch.object(
        sd, "offer_url", new_callable=AsyncMock, return_value=sd.Offer(URL, 540)
    ) as offer:
        result = await _unwrap(get_gmail_attachment_content)(
            service=service,
            message_id="msg-1",
            attachment_id="old-only",
            user_google_email=USER,
        )

    assert "Filename: report.pdf" in result
    assert offer.call_args.kwargs["ref"] == {"mid": "msg-1", "aid": "new-only"}


@pytest.mark.asyncio
async def test_signed_url_keeps_the_id_the_size_cap_pass_already_confirmed(
    enabled, monkeypatch
):
    """An ID the pre-download pass resolved is not second-guessed. With the cap
    on, that pass finds the part by ID; it just has no name, so the resolver
    runs for the name alone and may fall back to a same-sized *different* part.
    Taking its ID there would serve the wrong bytes under the wrong name."""
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "10485760")
    service = Mock()
    service.users().messages().get().execute.return_value = {
        "payload": {
            "parts": [
                {
                    "filename": "",
                    "mimeType": "application/octet-stream",
                    "body": {"attachmentId": "asked-for", "size": 4096},
                },
                {
                    "filename": "decoy.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "same-size", "size": 4096},
                },
            ]
        }
    }
    with patch.object(
        sd, "offer_url", new_callable=AsyncMock, return_value=sd.Offer(URL, 540)
    ) as offer:
        await _unwrap(get_gmail_attachment_content)(
            service=service,
            message_id="msg-1",
            attachment_id="asked-for",
            user_google_email=USER,
        )

    assert offer.call_args.kwargs["ref"] == {"mid": "msg-1", "aid": "asked-for"}


def _inline_image_and_pdf_service():
    """An unnamed inline image and one named PDF: the shape that made the old
    only-named-attachment fallback swap one part's bytes for the other's."""
    service = Mock()
    service.users().messages().get().execute.return_value = {
        "payload": {
            "parts": [
                {
                    "filename": "",
                    "mimeType": "image/png",
                    "body": {"attachmentId": "att-NAMELESS", "size": 2048},
                },
                {
                    "filename": "contract.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "att-PDF", "size": 9000},
                },
            ]
        }
    }
    return service


@pytest.mark.asyncio
async def test_a_nameless_part_asked_for_by_id_keeps_its_own_bytes(enabled):
    with patch.object(
        sd, "offer_url", new_callable=AsyncMock, return_value=sd.Offer(URL, 540)
    ) as offer:
        result = await _unwrap(get_gmail_attachment_content)(
            service=_inline_image_and_pdf_service(),
            message_id="msg-1",
            attachment_id="att-NAMELESS",
            user_google_email=USER,
        )
    kwargs = offer.call_args.kwargs
    assert kwargs["ref"] == {"mid": "msg-1", "aid": "att-NAMELESS"}
    assert kwargs["filename"] is None and kwargs["mime_type"] == "image/png"
    assert "contract.pdf" not in result


@pytest.mark.asyncio
async def test_an_unmatched_id_with_several_parts_gets_no_link(enabled, monkeypatch):
    """With the caller's ID gone and nothing safe to select it by, a link would
    have to guess: none is minted, and the normal path answers now instead."""
    monkeypatch.setattr("auth.oauth_config.is_stateless_mode", lambda: True)
    service = _inline_image_and_pdf_service()
    service.users().messages().attachments().get().execute.return_value = {
        "size": 5,
        "data": base64.urlsafe_b64encode(b"bytes").decode(),
    }
    with patch.object(sd, "offer_url", new_callable=AsyncMock) as offer:
        result = await _unwrap(get_gmail_attachment_content)(
            service=service,
            message_id="msg-1",
            attachment_id="att-GONE",
            user_google_email=USER,
        )
    offer.assert_not_called()
    assert "not in the message's current metadata" in result


@pytest.mark.asyncio
async def test_size_cap_nameless_match_keeps_its_type(enabled, monkeypatch):
    """With the cap on, the pre-download pass matched a nameless part by ID. Its
    name stays empty and its type stays its own: no second resolution borrows the
    only named attachment's name, or blanks the type."""
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "10485760")
    service = _inline_image_and_pdf_service()
    with patch.object(
        sd, "offer_url", new_callable=AsyncMock, return_value=sd.Offer(URL, 540)
    ) as offer:
        await _unwrap(get_gmail_attachment_content)(
            service=service,
            message_id="msg-1",
            attachment_id="att-NAMELESS",
            user_google_email=USER,
        )
    kwargs = offer.call_args.kwargs
    assert kwargs["filename"] is None and kwargs["mime_type"] == "image/png"
    assert kwargs["ref"]["aid"] == "att-NAMELESS"
    # One metadata fetch (the cap pass), not a second identical one.
    assert service.users().messages().get.call_count == 2  # Mock() setup call + 1


@pytest.mark.asyncio
async def test_size_cap_pass_pick_is_rechecked_before_minting(enabled, monkeypatch):
    """With the cap on and a rotated ID, upstream's pre-pass still settles on the
    only *named* attachment for its own download. A link needs more certainty:
    with an unnamed part also present it cannot tell which was meant, so none is
    minted and the upstream path answers."""
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "10485760")
    monkeypatch.setattr("auth.oauth_config.is_stateless_mode", lambda: True)
    service = _inline_image_and_pdf_service()
    service.users().messages().attachments().get().execute.return_value = {
        "size": 5,
        "data": base64.urlsafe_b64encode(b"bytes").decode(),
    }
    with patch.object(sd, "offer_url", new_callable=AsyncMock) as offer:
        result = await _unwrap(get_gmail_attachment_content)(
            service=service,
            message_id="msg-1",
            attachment_id="att-ROTATED",
            user_google_email=USER,
        )
    offer.assert_not_called()
    assert "not in the message's current metadata" in result


@pytest.mark.asyncio
async def test_size_cap_still_rejects_an_out_of_range_index(enabled, monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "10485760")
    result = await _unwrap(get_gmail_attachment_content)(
        service=_inline_image_and_pdf_service(),
        message_id="msg-1",
        attachment_id="att-ROTATED",
        user_google_email=USER,
        attachment_index=5,
    )
    assert "Invalid attachment_index 5" in result


def _deep_message_service():
    """A named attachment nested below the metadata mask's depth, ahead of the
    one the caller means in depth-first order."""
    node = {"mimeType": "multipart/mixed", "parts": []}
    deepest = node
    for _ in range(7):
        child = {"mimeType": "multipart/mixed", "parts": []}
        deepest["parts"].append(child)
        deepest = child
    deepest["parts"].append(
        {
            "filename": "deep.pdf",
            "mimeType": "application/pdf",
            "body": {"attachmentId": "deep", "size": 10},
        }
    )
    node["parts"].append(
        {
            "filename": "report.xlsx",
            "mimeType": "application/vnd.ms-excel",
            "body": {"attachmentId": "new-report", "size": 20},
        }
    )

    def masked(tree, depth=0):
        """What the depth-6 fields mask returns: no parts below the limit."""
        copy = {k: v for k, v in tree.items() if k != "parts"}
        if "parts" in tree and depth < 6:
            copy["parts"] = [masked(p, depth + 1) for p in tree["parts"]]
        return copy

    service = Mock()
    service.users().messages().get().execute.return_value = {"payload": masked(node)}
    service.users().messages().attachments().get().execute.return_value = {
        "size": 20,
        "data": base64.urlsafe_b64encode(b"x" * 20).decode(),
    }
    return service


@pytest.mark.asyncio
async def test_an_index_is_not_trusted_when_the_mask_may_hide_parts(
    enabled, monkeypatch
):
    """The listing counted the deep attachment; the masked metadata cannot see
    it, so index 1 would land on a different file. No link is minted."""
    monkeypatch.setattr("gmail.gmail_tools.is_stateless_mode", lambda: True)
    with patch.object(sd, "offer_url", new_callable=AsyncMock) as offer:
        await _unwrap(get_gmail_attachment_content)(
            service=_deep_message_service(),
            message_id="msg-1",
            attachment_id="old-report",
            user_google_email=USER,
            attachment_index=1,
        )
    offer.assert_not_called()


@pytest.mark.asyncio
async def test_stored_copies_keep_upstreams_naming(monkeypatch):
    """Signed links off: the downloaded bytes are named by upstream's rules (the
    only named attachment), unaffected by the stricter link rules."""
    monkeypatch.delenv("WORKSPACE_MCP_SIGNED_DOWNLOAD_URLS", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_MAX_FILE_BYTES", raising=False)
    saved = {}

    class Storage:
        def save_attachment(self, base64_data, filename=None, mime_type=None):
            saved.update(filename=filename, mime_type=mime_type)
            return type("R", (), {"path": "/tmp/x", "file_id": "f1"})()

    monkeypatch.setattr(
        "core.attachment_storage.get_attachment_storage", lambda: Storage()
    )
    monkeypatch.setattr(
        "core.attachment_storage.get_attachment_url", lambda fid: f"/a/{fid}"
    )
    monkeypatch.setattr("core.config.get_transport_mode", lambda: "streamable-http")
    # The tool imports this at call time, so patch it where it is looked up.
    monkeypatch.setattr("auth.oauth_config.is_stateless_mode", lambda: False)
    service = _inline_image_and_pdf_service()
    service.users().messages().attachments().get().execute.return_value = {
        "size": 5,
        "data": base64.urlsafe_b64encode(b"bytes").decode(),
    }
    result = await _unwrap(get_gmail_attachment_content)(
        service=service,
        message_id="msg-1",
        attachment_id="att-ROTATED",
        user_google_email=USER,
    )
    assert saved.get("filename") == "contract.pdf", result


@pytest.mark.asyncio
async def test_a_wrapped_message_at_the_mask_depth_also_blocks_fallbacks():
    """A forwarded message (message/rfc822) nests parts just like multipart."""
    from gmail.gmail_tools import _mask_may_hide_parts

    node = {"mimeType": "multipart/mixed", "parts": []}
    deepest = node
    for _ in range(5):
        child = {"mimeType": "multipart/mixed", "parts": []}
        deepest["parts"].append(child)
        deepest = child
    deepest["parts"].append({"mimeType": "message/rfc822", "filename": ""})
    assert _mask_may_hide_parts(node) is True


@pytest.mark.asyncio
async def test_an_out_of_range_index_is_never_read_as_the_only_attachment(enabled):
    from gmail.gmail_tools import _resolve_attachment

    service = Mock()
    service.users().messages().get().execute.return_value = {
        "payload": {
            "parts": [
                {
                    "filename": "solo.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "new-solo", "size": 9},
                }
            ]
        }
    }
    resolved = await _resolve_attachment(
        service, "msg-1", "old-solo", attachment_index=5
    )
    assert resolved.matched_by is None and resolved.named_count == 1


@pytest.mark.asyncio
async def test_a_stored_copy_is_named_by_its_size_not_a_wrong_index(monkeypatch):
    """The bytes were fetched by the caller's ID; their size names them. An
    index (here 1-based by mistake) does not override that."""
    monkeypatch.delenv("WORKSPACE_MCP_SIGNED_DOWNLOAD_URLS", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_MAX_FILE_BYTES", raising=False)
    monkeypatch.setattr("auth.oauth_config.is_stateless_mode", lambda: False)
    saved = {}

    class Storage:
        def save_attachment(self, base64_data, filename=None, mime_type=None):
            saved.update(filename=filename)
            return type("R", (), {"path": "/tmp/x", "file_id": "f1"})()

    monkeypatch.setattr(
        "core.attachment_storage.get_attachment_storage", lambda: Storage()
    )
    monkeypatch.setattr(
        "core.attachment_storage.get_attachment_url", lambda fid: f"/a/{fid}"
    )
    monkeypatch.setattr("core.config.get_transport_mode", lambda: "streamable-http")
    photo = b"p" * 5000
    service = Mock()
    service.users().messages().get().execute.return_value = {
        "payload": {
            "parts": [
                {
                    "filename": "photo.jpg",
                    "mimeType": "image/jpeg",
                    "body": {"attachmentId": "new-photo", "size": 5000},
                },
                {
                    "filename": "invoice.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "new-invoice", "size": 90000},
                },
            ]
        }
    }
    service.users().messages().attachments().get().execute.return_value = {
        "size": len(photo),
        "data": base64.urlsafe_b64encode(photo).decode(),
    }
    await _unwrap(get_gmail_attachment_content)(
        service=service,
        message_id="msg-1",
        attachment_id="old-photo",
        user_google_email=USER,
        attachment_index=1,
    )
    assert saved["filename"] == "photo.jpg"
