from unittest.mock import Mock

import pytest

from core.server import server
from core.utils import UserInputError
from gslides.slides_tools import (
    _describe_elements,
    _extract_shape_text,
    _iter_text_bearing_elements,
    batch_update_presentation,
)

EXPECTED_SLIDES_BATCH_REQUEST_TYPES = {
    "createSlide",
    "createShape",
    "createTable",
    "insertText",
    "insertTableRows",
    "insertTableColumns",
    "deleteTableRow",
    "deleteTableColumn",
    "replaceAllText",
    "deleteObject",
    "updatePageElementTransform",
    "updateSlidesPosition",
    "deleteText",
    "createImage",
    "createVideo",
    "createSheetsChart",
    "createLine",
    "refreshSheetsChart",
    "updateShapeProperties",
    "updateImageProperties",
    "updateVideoProperties",
    "updatePageProperties",
    "updateTableCellProperties",
    "updateLineProperties",
    "createParagraphBullets",
    "replaceAllShapesWithImage",
    "duplicateObject",
    "updateTextStyle",
    "replaceAllShapesWithSheetsChart",
    "deleteParagraphBullets",
    "updateParagraphStyle",
    "updateTableBorderProperties",
    "updateTableColumnProperties",
    "updateTableRowProperties",
    "mergeTableCells",
    "unmergeTableCells",
    "groupObjects",
    "ungroupObjects",
    "updatePageElementAltText",
    "replaceImage",
    "updateSlideProperties",
    "updatePageElementsZOrder",
    "updateLineCategory",
    "rerouteLine",
}


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
async def test_batch_update_schema_exposes_slides_request_variants():
    tools = await server.list_tools(run_middleware=False)
    tool = next(tool for tool in tools if tool.name == "batch_update_presentation")

    requests_schema = tool.parameters["properties"]["requests"]
    request_variants = requests_schema["items"]["anyOf"]
    request_types = {next(iter(variant["properties"])) for variant in request_variants}

    assert requests_schema["minItems"] == 1
    assert len(request_variants) == 44
    assert request_types == EXPECTED_SLIDES_BATCH_REQUEST_TYPES

    for variant in request_variants:
        assert variant["additionalProperties"] is False
        assert len(variant["properties"]) == 1
        request_type = next(iter(variant["properties"]))
        assert variant["required"] == [request_type]
        request_payload = variant["properties"][request_type]
        assert request_payload["additionalProperties"] is False
        assert request_payload["properties"]

    create_shape = next(
        variant["properties"]["createShape"]
        for variant in request_variants
        if "createShape" in variant["properties"]
    )
    assert create_shape["additionalProperties"] is False
    assert {
        "objectId",
        "shapeType",
        "elementProperties",
    }.issubset(create_shape["properties"])

    create_line = next(
        variant["properties"]["createLine"]
        for variant in request_variants
        if "createLine" in variant["properties"]
    )
    assert {"category", "lineCategory"}.issubset(create_line["properties"])

    replace_shapes_with_image = next(
        variant["properties"]["replaceAllShapesWithImage"]
        for variant in request_variants
        if "replaceAllShapesWithImage" in variant["properties"]
    )
    assert {"imageReplaceMethod", "replaceMethod"}.issubset(
        replace_shapes_with_image["properties"]
    )

    update_text_style = next(
        variant["properties"]["updateTextStyle"]
        for variant in request_variants
        if "updateTextStyle" in variant["properties"]
    )
    assert {
        "backgroundColor",
        "baselineOffset",
        "fontFamily",
        "link",
        "smallCaps",
        "strikethrough",
    }.issubset(update_text_style["properties"]["style"]["properties"])

    update_shape_properties = next(
        variant["properties"]["updateShapeProperties"]
        for variant in request_variants
        if "updateShapeProperties" in variant["properties"]
    )
    solid_fill = update_shape_properties["properties"]["shapeProperties"]["properties"][
        "shapeBackgroundFill"
    ]["anyOf"][0]["properties"]["solidFill"]["anyOf"][0]
    assert "color" in solid_fill["properties"]
    assert "opaqueColor" not in solid_fill["properties"]

    update_video_properties = next(
        variant["properties"]["updateVideoProperties"]
        for variant in request_variants
        if "updateVideoProperties" in variant["properties"]
    )
    video_properties = update_video_properties["properties"]["videoProperties"][
        "properties"
    ]
    assert {"autoPlay", "end", "mute", "outline", "start"} == set(video_properties)


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
            "slides(objectId,slideProperties(notesPage(objectId))),masters(objectId),layouts(objectId),notesMaster(objectId)"
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
async def test_batch_update_strips_schema_nulls_before_google_api_call():
    service, presentations = _build_slides_service(
        batch_update_response={"replies": [{"createSlide": {"objectId": "slide_2"}}]}
    )
    requests = [
        {
            "createSlide": {
                "objectId": "slide_2",
                "insertionIndex": None,
                "slideLayoutReference": None,
            }
        }
    ]

    await _unwrap(batch_update_presentation)(
        service=service,
        user_google_email="user@example.com",
        presentation_id="presentation-1",
        requests=requests,
    )

    call_kwargs = presentations.batchUpdate.call_args.kwargs
    assert call_kwargs["body"] == {
        "requests": [{"createSlide": {"objectId": "slide_2"}}]
    }


@pytest.mark.asyncio
async def test_batch_update_prunes_nested_empty_schema_objects():
    service, presentations = _build_slides_service()
    requests = [
        {
            "updateTextStyle": {
                "objectId": "title_box",
                "textRange": {
                    "type": "ALL",
                    "startIndex": None,
                    "endIndex": None,
                },
                "style": {
                    "backgroundColor": {"opaqueColor": None},
                    "baselineOffset": None,
                    "bold": True,
                    "fontFamily": None,
                    "fontSize": None,
                    "foregroundColor": {"opaqueColor": None},
                    "italic": None,
                    "link": {
                        "url": None,
                        "relativeLink": None,
                        "pageObjectId": None,
                        "slideIndex": None,
                    },
                    "smallCaps": None,
                    "strikethrough": None,
                    "underline": None,
                    "weightedFontFamily": {
                        "fontFamily": None,
                        "weight": None,
                    },
                },
                "fields": "bold",
                "cellLocation": None,
            }
        }
    ]

    await _unwrap(batch_update_presentation)(
        service=service,
        user_google_email="user@example.com",
        presentation_id="presentation-1",
        requests=requests,
    )

    call_kwargs = presentations.batchUpdate.call_args.kwargs
    assert call_kwargs["body"] == {
        "requests": [
            {
                "updateTextStyle": {
                    "objectId": "title_box",
                    "textRange": {"type": "ALL"},
                    "style": {"bold": True},
                    "fields": "bold",
                }
            }
        ]
    }


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


# --- Tests for text-extraction helpers used by get_page / get_presentation ---


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


def test_extract_shape_text_joins_runs_in_start_index_order():
    shape = {
        "text": {
            "textElements": [
                {"startIndex": 7, "textRun": {"content": "world!"}},
                {"startIndex": 0, "textRun": {"content": "Hello, "}},
            ]
        }
    }
    assert _extract_shape_text(shape) == "Hello, world!"


def test_extract_shape_text_handles_missing_or_empty_inputs():
    assert _extract_shape_text(None) == ""
    assert _extract_shape_text({}) == ""
    assert _extract_shape_text({"shapeType": "RECTANGLE"}) == ""
    assert _extract_shape_text({"text": {"textElements": []}}) == ""


def test_extract_shape_text_skips_textelements_without_textrun():
    shape = {
        "text": {
            "textElements": [
                {"startIndex": 0, "paragraphMarker": {}},
                {"startIndex": 0, "textRun": {"content": "actual text"}},
            ]
        }
    }
    assert _extract_shape_text(shape) == "actual text"


def test_iter_text_bearing_elements_recurses_into_groups():
    elements = [
        _text_shape("top-level"),
        {
            "elementGroup": {
                "children": [
                    _text_shape("nested"),
                    {
                        "elementGroup": {
                            "children": [_text_shape("deep")],
                        }
                    },
                ]
            }
        },
    ]
    assert list(_iter_text_bearing_elements(elements)) == [
        "top-level",
        "nested",
        "deep",
    ]


def test_iter_text_bearing_elements_skips_empty_shapes_and_non_shape_types():
    elements = [
        {"shape": {"shapeType": "RECTANGLE"}},
        {"table": {"rows": 2, "columns": 2}},
        {"line": {"lineType": "STRAIGHT"}},
        {"image": {}},
        _text_shape("only-text"),
    ]
    assert list(_iter_text_bearing_elements(elements)) == ["only-text"]


def test_iter_text_bearing_elements_handles_empty_and_none_input():
    assert list(_iter_text_bearing_elements([])) == []
    assert list(_iter_text_bearing_elements(None)) == []


def test_describe_elements_renders_single_line_text_inline():
    elements = [{"objectId": "s1", **_text_shape("Hello")}]
    assert _describe_elements(elements) == [
        '  Shape: ID s1, Type: TEXT_BOX, Text: "Hello"'
    ]


def test_describe_elements_renders_multiline_text_as_blockquote():
    elements = [
        {
            "objectId": "s1",
            "shape": {
                "shapeType": "TEXT_BOX",
                "text": {
                    "textElements": [
                        {
                            "startIndex": 0,
                            "textRun": {"content": "line one\nline two\nline three"},
                        }
                    ]
                },
            },
        }
    ]
    assert _describe_elements(elements) == [
        "  Shape: ID s1, Type: TEXT_BOX, Text:",
        "    > line one",
        "    > line two",
        "    > line three",
    ]


def test_describe_elements_recurses_into_groups_with_deeper_indent():
    elements = [
        {
            "objectId": "g1",
            "elementGroup": {
                "children": [
                    {"objectId": "child1", **_text_shape("inside")},
                    {
                        "objectId": "g2",
                        "elementGroup": {
                            "children": [
                                {"objectId": "grandchild", **_text_shape("deeper")}
                            ]
                        },
                    },
                ]
            },
        }
    ]
    assert _describe_elements(elements) == [
        "  Group: ID g1, Children: 2",
        '    Shape: ID child1, Type: TEXT_BOX, Text: "inside"',
        "    Group: ID g2, Children: 1",
        '      Shape: ID grandchild, Type: TEXT_BOX, Text: "deeper"',
    ]


def test_describe_elements_labels_non_text_element_types():
    elements = [
        {"objectId": "t1", "table": {"rows": 3, "columns": 2}},
        {"objectId": "l1", "line": {"lineType": "STRAIGHT"}},
        {"objectId": "x1", "speakerSpotlight": {}},
    ]
    assert _describe_elements(elements) == [
        "  Table: ID t1, Size: 3x2",
        "  Line: ID l1, Type: STRAIGHT",
        "  Element: ID x1, Type: Unknown",
    ]


def test_describe_elements_surfaces_sheets_chart_source():
    """A linked Sheets chart must expose its source spreadsheetId/chartId so a
    caller can edit the source data and refresh the chart with refreshSheetsChart.
    """
    elements = [
        {
            "objectId": "c1",
            "sheetsChart": {
                "spreadsheetId": "sheet-123",
                "chartId": 456,
                "contentUrl": "https://example.com/chart.png",
            },
        }
    ]
    assert _describe_elements(elements) == [
        "  SheetsChart: ID c1, SpreadsheetID sheet-123, ChartID 456"
    ]


def test_describe_elements_surfaces_sheets_chart_with_missing_fields():
    elements = [{"objectId": "c1", "sheetsChart": {}}]
    assert _describe_elements(elements) == [
        "  SheetsChart: ID c1, SpreadsheetID Unknown, ChartID Unknown"
    ]


def test_describe_elements_labels_image_video_and_wordart():
    elements = [
        {"objectId": "i1", "image": {"sourceUrl": "https://example.com/img.png"}},
        {"objectId": "i2", "image": {"contentUrl": "https://example.com/rendered.png"}},
        {"objectId": "i3", "image": {}},
        {"objectId": "v1", "video": {"source": "YOUTUBE", "id": "abc123"}},
        {"objectId": "w1", "wordArt": {"renderedText": "Hello"}},
        {"objectId": "w2", "wordArt": {}},
    ]
    assert _describe_elements(elements) == [
        "  Image: ID i1, Source: https://example.com/img.png",
        "  Image: ID i2, ContentURL: https://example.com/rendered.png",
        "  Image: ID i3, Source: Unknown",
        "  Video: ID v1, Source: YOUTUBE, VideoID: abc123",
        '  WordArt: ID w1, Text: "Hello"',
        "  WordArt: ID w2",
    ]


def test_describe_elements_keeps_shape_without_text_simple():
    elements = [
        {"objectId": "s1", "shape": {"shapeType": "RECTANGLE"}},
    ]
    assert _describe_elements(elements) == [
        "  Shape: ID s1, Type: RECTANGLE",
    ]


def test_describe_elements_handles_empty_and_none_input():
    assert _describe_elements([]) == []
    assert _describe_elements(None) == []
