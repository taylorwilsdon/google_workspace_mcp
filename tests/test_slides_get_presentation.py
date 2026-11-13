"""get_presentation actions: dispatch, required parameters, and what each fetches."""

import json
from unittest.mock import Mock

import pytest

from core.utils import UserInputError
from gslides.slides_tools import get_presentation

EMU = 12700


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _text_box(oid, text, size=None):
    style = {"fontSize": {"magnitude": size, "unit": "PT"}} if size else {}
    return {
        "objectId": oid,
        "size": {
            "width": {"magnitude": 100 * EMU, "unit": "EMU"},
            "height": {"magnitude": 40 * EMU, "unit": "EMU"},
        },
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 10 * EMU, "unit": "EMU"},
        "shape": {
            "shapeType": "TEXT_BOX",
            "text": {
                "textElements": [
                    {"paragraphMarker": {}},
                    {"textRun": {"content": text + "\n", "style": style}},
                ]
            },
        },
    }


PAGE = {
    "objectId": "slide_1",
    "pageType": "SLIDE",
    "revisionId": "rev1",
    "pageElements": [
        _text_box("label", "ANNOUNCEMENT", 8),
        {
            "objectId": "grp",
            "elementGroup": {
                "children": [_text_box("headline", "Quarterly review", 20)]
            },
        },
    ],
    "slideProperties": {
        "layoutObjectId": "L1",
        "masterObjectId": "M1",
        "notesPage": {
            "notesProperties": {"speakerNotesObjectId": "notes_1"},
            "pageElements": [
                {"objectId": "notes_1", "shape": {"shapeType": "TEXT_BOX"}}
            ],
        },
    },
}

DECK = {
    "presentationId": "pres",
    "title": "Q4",
    "revisionId": "rev1",
    "pageSize": {
        "width": {"magnitude": 720 * EMU, "unit": "EMU"},
        "height": {"magnitude": 405 * EMU, "unit": "EMU"},
    },
    "slides": [PAGE],
    "layouts": [
        {
            "objectId": "L1",
            "layoutProperties": {"displayName": "Blank", "masterObjectId": "M1"},
            "pageElements": [],
        }
    ],
    "masters": [{"objectId": "M1"}],
}


def _service(deck=DECK, page=PAGE):
    service = Mock()
    presentations = service.presentations.return_value
    presentations.get.return_value.execute.return_value = deck
    presentations.pages.return_value.get.return_value.execute.return_value = page
    return service, presentations


async def _call(service, **kwargs):
    return await _unwrap(get_presentation)(
        service=service,
        user_google_email="u@example.com",
        presentation_id="pres",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_unknown_action_names_the_valid_ones():
    service, _ = _service()
    with pytest.raises(UserInputError, match='"deck", "layouts", "page", "markup"'):
        await _call(service, action="slides")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["page", "markup"])
async def test_page_actions_require_page_id(action):
    service, _ = _service()
    with pytest.raises(UserInputError, match="page_id is required"):
        await _call(service, action=action)


@pytest.mark.asyncio
async def test_deck_lists_slides_titles_and_layouts():
    service, _ = _service()
    out = await _call(service, action=" Deck ")
    assert "Slides: 1   Page size: 720 x 405 pt   Revision: rev1" in out
    # No title placeholder: the largest type wins over the label above it.
    assert "1. slide_1  Quarterly review" in out
    assert "L1  Blank  -  slide 1" in out


@pytest.mark.asyncio
async def test_action_defaults_to_deck():
    service, _ = _service()
    assert (await _call(service)).startswith("Presentation: Q4")


@pytest.mark.asyncio
async def test_deck_rejects_negative_offset():
    service, _ = _service()
    with pytest.raises(UserInputError, match="offset must be >= 0"):
        await _call(service, action="deck", offset=-1)


@pytest.mark.asyncio
async def test_layouts_fetches_only_layout_fields_and_checks_after():
    service, presentations = _service()
    out = await _call(service, action="layouts")
    assert "L1  Blank" in out
    assert "layouts(" in presentations.get.call_args.kwargs["fields"]
    with pytest.raises(UserInputError, match="not a slide"):
        await _call(service, action="layouts", after="nope")


@pytest.mark.asyncio
async def test_page_lists_elements_groups_and_empty_notes_shape():
    service, _ = _service()
    out = await _call(service, action="page", page_id="slide_1")
    assert out.startswith("page slide_1  SLIDE  2 elements  revision rev1")
    assert (
        "- group grp  1 element\n  - shape headline  TEXT_BOX\n    Quarterly review"
        in out
    )
    assert "notes shape notes_1: (empty)" in out


@pytest.mark.asyncio
async def test_markup_is_compact_json_in_points():
    service, _ = _service()
    out = await _call(service, action="markup", page_id="slide_1")
    assert ", " not in out and ": " not in out.replace("ANNOUNCEMENT", "")
    body = json.loads(out)
    assert body["page_size"] == {"w": 720, "h": 405}
    label = body["elements"][0]
    assert label["id"] == "label" and label["box"] == {
        "x": 10,
        "y": 0,
        "w": 100,
        "h": 40,
    }


@pytest.mark.asyncio
async def test_markup_targets_scope_and_refuse_unknown_ids():
    service, _ = _service()
    body = json.loads(
        await _call(service, action="markup", page_id="slide_1", targets=["headline"])
    )
    assert [e["id"] for e in body["elements"]] == ["headline"]
    assert body["elements"][0]["group"] == "grp"
    with pytest.raises(UserInputError, match="did you mean headline"):
        await _call(service, action="markup", page_id="slide_1", targets=["headlne"])


@pytest.mark.asyncio
async def test_markup_raw_prunes_the_page_resource():
    service, _ = _service()
    body = json.loads(
        await _call(
            service, action="markup", page_id="slide_1", targets=["label"], raw=True
        )
    )
    assert [e["objectId"] for e in body["pageElements"]] == ["label"]
    assert "notesPage" not in body["slideProperties"]
    full = json.loads(
        await _call(service, action="markup", page_id="slide_1", raw=True)
    )
    assert full == PAGE
