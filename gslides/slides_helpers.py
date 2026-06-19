"""
Google Slides Helper Functions

Shared utilities for Google Slides operations.
"""

import asyncio
from typing import Any, Dict, List, Set, Tuple

from core.utils import UserInputError

_PRESENTATION_PAGE_ID_FIELDS = (
    "slides(objectId,slideProperties(notesPage(objectId))),"
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


async def _get_presentation_slide_ids(service, presentation_id: str) -> Set[str]:
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
    for slide in result.get("slides", []):
        notes_id = slide.get("slideProperties", {}).get("notesPage", {}).get("objectId")
        if isinstance(notes_id, str):
            page_ids.add(notes_id)
    notes_master = result.get("notesMaster")
    if isinstance(notes_master, dict) and isinstance(notes_master.get("objectId"), str):
        page_ids.add(notes_master["objectId"])
    return page_ids


async def validate_insert_text_targets(
    service, presentation_id: str, requests: List[Dict[str, Any]]
) -> None:
    insert_text_targets = _find_insert_text_targets(requests)
    if not insert_text_targets:
        return

    slide_ids = _find_created_slide_ids(requests)
    slide_ids.update(await _get_presentation_slide_ids(service, presentation_id))

    invalid_targets = [
        (index, object_id)
        for index, object_id in insert_text_targets
        if object_id in slide_ids
    ]
    if not invalid_targets:
        return

    invalid_refs = ", ".join(
        f"requests[{index}].insertText.objectId='{object_id}'"
        for index, object_id in invalid_targets
    )
    raise UserInputError(
        "Invalid Slides batch update request: "
        f"{invalid_refs} targets a slide/page object. The Slides API only allows "
        "insertText on text-capable shapes or table cells. Create a text box or "
        "shape first with createShape, set elementProperties.pageObjectId to the "
        "slide ID, then insertText into the new shape objectId. For existing "
        "content, call get_page and use a Shape or Table element ID, not the "
        "Page ID."
    )
