"""
Google Slides Helper Functions

Shared utilities for Google Slides operations.
"""

import asyncio
from typing import Any, Dict, List, Set, Tuple

from core.utils import UserInputError

_PRESENTATION_PAGE_ID_FIELDS = (
    "slides(objectId,slideProperties(notesPage(objectId,"
    "notesProperties(speakerNotesObjectId)))),"
    "masters(objectId),layouts(objectId),notesMaster(objectId)"
)

_SLIDES_BATCH_REQUEST_TYPES = frozenset(
    {
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
)

_SLIDES_BATCH_REQUEST_EXAMPLES = (
    "createSlide",
    "createShape",
    "insertText",
    "updateTextStyle",
    "createImage",
    "deleteObject",
)


def _slides_batch_request_guidance() -> str:
    examples = ", ".join(_SLIDES_BATCH_REQUEST_EXAMPLES)
    return f"exactly one Slides request type such as {examples}"


def validate_batch_update_requests(requests: List[Dict[str, Any]]) -> None:
    guidance = _slides_batch_request_guidance()
    if not requests:
        raise UserInputError(
            "Invalid Slides batch update request: requests must contain at least "
            f"one request object with {guidance}."
        )

    for index, request in enumerate(requests):
        if not isinstance(request, dict):
            raise UserInputError(
                "Invalid Slides batch update request: "
                f"requests[{index}] must be an object containing {guidance}."
            )

        request_types = list(request)
        if len(request_types) != 1:
            if not request_types:
                problem = "is empty"
            else:
                problem = f"contains multiple fields ({', '.join(request_types)})"
            raise UserInputError(
                "Invalid Slides batch update request: "
                f"requests[{index}] {problem}; it must contain {guidance}."
            )

        request_type = request_types[0]
        if request_type not in _SLIDES_BATCH_REQUEST_TYPES:
            raise UserInputError(
                "Invalid Slides batch update request: "
                f"requests[{index}] has unsupported request type '{request_type}'. "
                f"It must contain {guidance}."
            )

        if not isinstance(request[request_type], dict):
            raise UserInputError(
                "Invalid Slides batch update request: "
                f"requests[{index}].{request_type} must be an object for {guidance}."
            )


def _get_request_payload(request: Dict[str, Any], request_type: str) -> Dict[str, Any]:
    payload = request.get(request_type)
    return payload if isinstance(payload, dict) else {}


def _find_insert_text_targets(
    requests: List[Dict[str, Any]],
) -> List[Tuple[int, str]]:
    targets = []
    for index, request in enumerate(requests):
        if not isinstance(request, dict):
            continue
        object_id = _get_request_payload(request, "insertText").get("objectId")
        if isinstance(object_id, str) and object_id:
            targets.append((index, object_id))
    return targets


def _find_created_slide_ids(requests: List[Dict[str, Any]]) -> Set[str]:
    slide_ids = set()
    for request in requests:
        if not isinstance(request, dict):
            continue
        object_id = _get_request_payload(request, "createSlide").get("objectId")
        if isinstance(object_id, str) and object_id:
            slide_ids.add(object_id)
    return slide_ids


async def _get_presentation_page_ids(
    service, presentation_id: str
) -> Tuple[Set[str], Dict[str, str]]:
    """Return every page object ID plus a notes page ID -> speaker notes shape ID map.

    The speaker notes shape is the only writable part of a notes page, so the map
    lets an insertText aimed at a notes page be redirected to the shape that can
    actually hold the text. See https://developers.google.com/slides/api/guides/notes
    """
    result = await asyncio.to_thread(
        service.presentations()
        .get(
            presentationId=presentation_id,
            fields=_PRESENTATION_PAGE_ID_FIELDS,
        )
        .execute
    )
    page_ids = {
        page["objectId"]
        for page_type in ("slides", "masters", "layouts")
        for page in result.get(page_type, [])
        if isinstance(page.get("objectId"), str)
    }
    speaker_notes_shapes: Dict[str, str] = {}
    for slide in result.get("slides", []):
        notes_page = slide.get("slideProperties", {}).get("notesPage", {})
        notes_id = notes_page.get("objectId")
        if not isinstance(notes_id, str):
            continue
        page_ids.add(notes_id)
        shape_id = notes_page.get("notesProperties", {}).get("speakerNotesObjectId")
        if isinstance(shape_id, str) and shape_id:
            speaker_notes_shapes[notes_id] = shape_id
    notes_master = result.get("notesMaster")
    if isinstance(notes_master, dict) and isinstance(notes_master.get("objectId"), str):
        page_ids.add(notes_master["objectId"])
    return page_ids, speaker_notes_shapes


async def validate_insert_text_targets(
    service, presentation_id: str, requests: List[Dict[str, Any]]
) -> None:
    insert_text_targets = _find_insert_text_targets(requests)
    if not insert_text_targets:
        return

    page_ids = _find_created_slide_ids(requests)
    presentation_page_ids, speaker_notes_shapes = await _get_presentation_page_ids(
        service, presentation_id
    )
    page_ids.update(presentation_page_ids)

    invalid_targets = [
        (index, object_id)
        for index, object_id in insert_text_targets
        if object_id in page_ids
    ]
    if not invalid_targets:
        return

    problems = []
    has_non_notes_target = False
    for index, object_id in invalid_targets:
        ref = f"requests[{index}].insertText.objectId='{object_id}'"
        shape_id = speaker_notes_shapes.get(object_id)
        if shape_id:
            problems.append(
                f"{ref} targets a notes page. Speaker notes text lives in the notes "
                f"shape on that page, so use objectId '{shape_id}' instead."
            )
        else:
            has_non_notes_target = True
            problems.append(f"{ref} targets a slide/page object.")

    message = "Invalid Slides batch update request: " + " ".join(problems)
    if has_non_notes_target:
        message += (
            " The Slides API only allows insertText on text-capable shapes or table "
            "cells. Create a text box or shape first with createShape, set "
            "elementProperties.pageObjectId to the slide ID, then insertText into "
            "the new shape objectId. For existing content, call get_page and use a "
            "Shape or Table element ID, not the Page ID."
        )
    raise UserInputError(message)
