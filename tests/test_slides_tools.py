import base64
from unittest.mock import Mock

import httpx
import pytest
from fastmcp.tools import ToolResult

from core.utils import UserInputError
from gslides import slides_tools
from gslides.slides_tools import (
    batch_update_presentation,
)


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _build_slides_service(presentation=None, batch_update_response=None):
    service = Mock()
    presentations = service.presentations.return_value
    presentations.get.return_value.execute.return_value = presentation or {
        "slides": [{"objectId": "p"}]
    }
    presentations.batchUpdate.return_value.execute.return_value = (
        batch_update_response or {"replies": []}
    )
    return service, presentations


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requests", "expected_message"),
    [
        ([], "requests must contain at least one request object"),
        ([{}], "requests[0] is empty"),
        ([{"unknownRequest": {}}], "unsupported request type 'unknownRequest'"),
        (
            [{"createSlide": {}, "insertText": {}}],
            "requests[0] contains multiple fields (createSlide, insertText)",
        ),
        ([{"createSlide": None}], "requests[0].createSlide must be an object"),
    ],
)
async def test_batch_update_rejects_invalid_request_objects(requests, expected_message):
    service, presentations = _build_slides_service()

    with pytest.raises(UserInputError) as exc_info:
        await _unwrap(batch_update_presentation)(
            service=service,
            user_google_email="user@example.com",
            presentation_id="presentation-1",
            requests=requests,
        )

    message = str(exc_info.value)
    assert expected_message in message
    assert "exactly one Slides request type" in message
    assert "createSlide" in message
    presentations.get.assert_not_called()
    presentations.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_batch_update_rejects_insert_text_targeting_slide_id():
    service, presentations = _build_slides_service()

    with pytest.raises(UserInputError) as exc_info:
        await _unwrap(batch_update_presentation)(
            service=service,
            user_google_email="user@example.com",
            presentation_id="presentation-1",
            requests=[
                {
                    "insertText": {
                        "objectId": "p",
                        "insertionIndex": 0,
                        "text": "Title",
                    }
                }
            ],
        )

    assert "requests[0].insertText.objectId='p'" in str(exc_info.value)
    assert "createShape" in str(exc_info.value)
    presentations.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_batch_update_rejects_insert_text_targeting_other_page_ids():
    service, presentations = _build_slides_service(
        presentation={
            "slides": [
                {
                    "objectId": "slide_1",
                    "slideProperties": {"notesPage": {"objectId": "notes_1"}},
                }
            ],
            "masters": [{"objectId": "master_1"}],
            "layouts": [{"objectId": "layout_1"}],
            "notesMaster": {"objectId": "notes_master_1"},
        }
    )

    with pytest.raises(UserInputError) as exc_info:
        await _unwrap(batch_update_presentation)(
            service=service,
            user_google_email="user@example.com",
            presentation_id="presentation-1",
            requests=[
                {
                    "insertText": {
                        "objectId": "master_1",
                        "insertionIndex": 0,
                        "text": "Title",
                    }
                },
                {
                    "insertText": {
                        "objectId": "layout_1",
                        "insertionIndex": 0,
                        "text": "Title",
                    }
                },
                {
                    "insertText": {
                        "objectId": "notes_master_1",
                        "insertionIndex": 0,
                        "text": "Title",
                    }
                },
                {
                    "insertText": {
                        "objectId": "notes_1",
                        "insertionIndex": 0,
                        "text": "Title",
                    }
                },
            ],
        )

    message = str(exc_info.value)
    assert "requests[0].insertText.objectId='master_1'" in message
    assert "requests[1].insertText.objectId='layout_1'" in message
    assert "requests[2].insertText.objectId='notes_master_1'" in message
    assert "requests[3].insertText.objectId='notes_1'" in message
    presentations.get.assert_called_once_with(
        presentationId="presentation-1",
        fields=(
            "slides(objectId,slideProperties(notesPage(objectId,notesProperties(speakerNotesObjectId)))),masters(objectId),layouts(objectId),notesMaster(objectId)"
        ),
    )
    presentations.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_batch_update_allows_insert_text_targeting_created_shape():
    service, presentations = _build_slides_service(
        batch_update_response={
            "replies": [
                {},
                {"createShape": {"objectId": "title_box"}},
                {},
            ]
        }
    )
    requests = [
        {"createSlide": {"objectId": "slide_2"}},
        {
            "createShape": {
                "objectId": "title_box",
                "shapeType": "TEXT_BOX",
                "elementProperties": {"pageObjectId": "slide_2"},
            }
        },
        {
            "insertText": {
                "objectId": "title_box",
                "insertionIndex": 0,
                "text": "Title",
            }
        },
    ]

    result = await _unwrap(batch_update_presentation)(
        service=service,
        user_google_email="user@example.com",
        presentation_id="presentation-1",
        requests=requests,
    )

    call_kwargs = presentations.batchUpdate.call_args.kwargs
    assert call_kwargs["body"] == {"requests": requests}
    assert "Batch Update Completed" in result
    assert "Created shape with ID title_box" in result


@pytest.mark.asyncio
async def test_batch_update_reports_replace_all_text_counts() -> None:
    """replaceAllText replies report how many occurrences changed, including none."""
    service, _ = _build_slides_service(
        batch_update_response={
            "replies": [
                {"replaceAllText": {"occurrencesChanged": 3}},
                {"replaceAllText": {}},
            ]
        }
    )
    requests = [
        {
            "replaceAllText": {
                "containsText": {"text": "{{name}}"},
                "replaceText": "Ada",
            }
        },
        {
            "replaceAllText": {
                "containsText": {"text": "{{missing}}"},
                "replaceText": "x",
            }
        },
    ]

    result = await _unwrap(batch_update_presentation)(
        service=service,
        user_google_email="user@example.com",
        presentation_id="presentation-1",
        requests=requests,
    )

    assert "Request 1: Replaced 3 occurrence(s)" in result
    assert "Request 2: Replaced 0 occurrence(s)" in result
    assert "Operation completed" not in result


@pytest.mark.asyncio
async def test_batch_update_rejects_insert_text_targeting_new_slide_id():
    service, presentations = _build_slides_service(presentation={"slides": []})

    with pytest.raises(UserInputError) as exc_info:
        await _unwrap(batch_update_presentation)(
            service=service,
            user_google_email="user@example.com",
            presentation_id="presentation-1",
            requests=[
                {"createSlide": {"objectId": "slide_2"}},
                {
                    "insertText": {
                        "objectId": "slide_2",
                        "insertionIndex": 0,
                        "text": "Title",
                    }
                },
            ],
        )

    assert "requests[1].insertText.objectId='slide_2'" in str(exc_info.value)
    presentations.batchUpdate.assert_not_called()


# --- Tests for speaker notes reporting and insertText redirection ---


def _text_shape(content):
    """Build a minimal shape dict whose text is a single run with `content`."""
    return {
        "shape": {
            "shapeType": "TEXT_BOX",
            "text": {
                "textElements": [{"startIndex": 0, "textRun": {"content": content}}]
            },
        }
    }


def _slide_with_notes(slide_id="slide_1", notes_object_id="notes_shape_1", text=None):
    """Build a slide resource carrying a notes page, as presentations.get returns."""
    notes_page = {
        "objectId": f"{slide_id}_notes",
        "notesProperties": {"speakerNotesObjectId": notes_object_id}
        if notes_object_id
        else {},
        "pageElements": [],
    }
    if text is not None:
        element = _text_shape(text)
        element["objectId"] = notes_object_id
        notes_page["pageElements"].append(element)
    return {
        "objectId": slide_id,
        "slideProperties": {"notesPage": notes_page},
    }


@pytest.mark.asyncio
async def test_batch_update_redirects_insert_text_from_notes_page_to_notes_shape():
    service, presentations = _build_slides_service(
        presentation={
            "slides": [_slide_with_notes("slide_1", "notes_shape_1")],
        }
    )

    with pytest.raises(UserInputError) as exc_info:
        await _unwrap(batch_update_presentation)(
            service=service,
            user_google_email="user@example.com",
            presentation_id="presentation-1",
            requests=[
                {
                    "insertText": {
                        "objectId": "slide_1_notes",
                        "insertionIndex": 0,
                        "text": "Talk track",
                    }
                }
            ],
        )

    message = str(exc_info.value)
    assert "requests[0].insertText.objectId='slide_1_notes'" in message
    assert "targets a notes page" in message
    assert "use objectId 'notes_shape_1' instead" in message
    # The createShape guidance is wrong for notes and must not be appended.
    assert "createShape" not in message
    presentations.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_batch_update_keeps_shape_guidance_when_a_slide_id_is_also_targeted():
    service, _ = _build_slides_service(
        presentation={"slides": [_slide_with_notes("slide_1", "notes_shape_1")]}
    )

    with pytest.raises(UserInputError) as exc_info:
        await _unwrap(batch_update_presentation)(
            service=service,
            user_google_email="user@example.com",
            presentation_id="presentation-1",
            requests=[
                {
                    "insertText": {
                        "objectId": "slide_1_notes",
                        "insertionIndex": 0,
                        "text": "Notes",
                    }
                },
                {
                    "insertText": {
                        "objectId": "slide_1",
                        "insertionIndex": 0,
                        "text": "Title",
                    }
                },
            ],
        )

    message = str(exc_info.value)
    assert "use objectId 'notes_shape_1' instead" in message
    assert "requests[1].insertText.objectId='slide_1' targets a slide/page object" in (
        message
    )
    assert "createShape" in message


@pytest.mark.asyncio
async def test_batch_update_allows_insert_text_into_the_speaker_notes_shape():
    service, presentations = _build_slides_service(
        presentation={
            "slides": [_slide_with_notes("slide_1", "notes_shape_1", "Old\n")]
        }
    )
    requests = [
        {"deleteText": {"objectId": "notes_shape_1", "textRange": {"type": "ALL"}}},
        {
            "insertText": {
                "objectId": "notes_shape_1",
                "insertionIndex": 0,
                "text": "New talk track",
            }
        },
    ]

    result = await _unwrap(batch_update_presentation)(
        service=service,
        user_google_email="user@example.com",
        presentation_id="presentation-1",
        requests=requests,
    )

    assert presentations.batchUpdate.call_args.kwargs["body"] == {"requests": requests}
    assert "Batch Update Completed" in result


class TestRenderPageToImage:
    URL = "https://lh7-us.googleusercontent.com/thumb.png"

    def _service(self):
        service = Mock()
        service.presentations.return_value.pages.return_value.getThumbnail.return_value.execute.return_value = {
            "contentUrl": self.URL
        }
        return service

    @staticmethod
    def _mock_fetch(monkeypatch, handler):
        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            slides_tools.httpx,
            "AsyncClient",
            lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
        )

    @staticmethod
    def _png_handler(png=b"\x89PNG\r\n\x1a\nfake"):
        def handler(request):
            assert request.url.host == "lh7-us.googleusercontent.com"
            return httpx.Response(
                200, content=png, headers={"content-type": "image/png"}
            )

        return handler

    @pytest.mark.asyncio
    async def test_returns_png_image_content_by_default(self, monkeypatch):
        png = b"\x89PNG\r\n\x1a\nfake"
        self._mock_fetch(monkeypatch, self._png_handler(png))
        service = self._service()
        out = await _unwrap(slides_tools.render_page_to_image)(
            service, "u@example.com", "pres", "p1"
        )
        assert isinstance(out, ToolResult)
        text, image = out.content
        assert "pres" in text.text and "p1" in text.text
        assert "MEDIUM" in text.text
        assert self.URL in text.text
        assert "30 minutes" in text.text
        assert image.type == "image" and image.mime_type == "image/png"
        assert base64.b64decode(image.data) == png
        assert out.structured_content == {"result": text.text}
        get_thumbnail = (
            service.presentations.return_value.pages.return_value.getThumbnail
        )
        assert (
            get_thumbnail.call_args.kwargs["thumbnailProperties_thumbnailSize"]
            == "MEDIUM"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "size, width",
        [("SMALL", 200), ("MEDIUM", 800), ("LARGE", 1600), ("WIDTH2000_PX", 2000)],
    )
    async def test_each_valid_size_is_passed_to_the_api(self, monkeypatch, size, width):
        self._mock_fetch(monkeypatch, self._png_handler())
        service = self._service()
        out = await _unwrap(slides_tools.render_page_to_image)(
            service, "u@example.com", "pres", "p1", size=size
        )
        get_thumbnail = (
            service.presentations.return_value.pages.return_value.getThumbnail
        )
        get_thumbnail.assert_called_once_with(
            presentationId="pres",
            pageObjectId="p1",
            thumbnailProperties_thumbnailSize=size,
            thumbnailProperties_mimeType="PNG",
        )
        assert f"{size} ({width}px wide)" in out.content[0].text

    @pytest.mark.asyncio
    async def test_reports_dimensions_returned_by_the_api(self, monkeypatch):
        self._mock_fetch(monkeypatch, self._png_handler())
        service = self._service()
        service.presentations.return_value.pages.return_value.getThumbnail.return_value.execute.return_value = {
            "contentUrl": self.URL,
            "width": 450,
            "height": 800,
        }
        out = await _unwrap(slides_tools.render_page_to_image)(
            service, "u@example.com", "pres", "p1"
        )
        assert "MEDIUM (450x800px)" in out.content[0].text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("size", ["HUGE", "medium", ""])
    async def test_invalid_size_is_rejected_before_calling_the_api(self, size):
        service = self._service()
        with pytest.raises(UserInputError) as exc_info:
            await _unwrap(slides_tools.render_page_to_image)(
                service, "u@example.com", "pres", "p1", size=size
            )
        message = str(exc_info.value)
        for valid in ("SMALL", "MEDIUM", "LARGE", "WIDTH2000_PX"):
            assert valid in message
        service.presentations.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(403),
            httpx.Response(
                200, content=b"<html>", headers={"content-type": "text/html"}
            ),
            httpx.Response(
                200, content=b"<svg/>", headers={"content-type": "image/svg+xml"}
            ),
            httpx.Response(
                200,
                content=b"\x89PNG\r\n\x1a\nfake",
                headers={"content-type": "text/html"},
            ),
            httpx.Response(200, content=b"\x89PNG\r\n\x1a\nfake"),
            httpx.ConnectError("unreachable"),
        ],
    )
    async def test_fetch_failure_falls_back_to_text_with_url(
        self, monkeypatch, response
    ):
        def handler(request):
            if isinstance(response, Exception):
                raise response
            return response

        self._mock_fetch(monkeypatch, handler)
        out = await _unwrap(slides_tools.render_page_to_image)(
            self._service(), "u@example.com", "pres", "p1"
        )
        assert isinstance(out, ToolResult)
        assert len(out.content) == 1
        text = out.content[0].text
        assert self.URL in text
        assert "Image unavailable" in text
        assert out.structured_content == {"result": text}

    @pytest.mark.asyncio
    async def test_fetch_failure_keeps_url_out_of_logs(self, monkeypatch, caplog):
        self._mock_fetch(monkeypatch, lambda request: httpx.Response(403))
        with caplog.at_level("DEBUG", logger=slides_tools.logger.name):
            await _unwrap(slides_tools.render_page_to_image)(
                self._service(), "u@example.com", "pres", "p1"
            )
        # httpx's own request log is held at WARNING by main.py, so only the
        # tool's records are checked here.
        tool_logs = " ".join(
            r.getMessage() for r in caplog.records if r.name == slides_tools.logger.name
        )
        assert "Image fetch failed" in tool_logs
        assert "googleusercontent" not in tool_logs
