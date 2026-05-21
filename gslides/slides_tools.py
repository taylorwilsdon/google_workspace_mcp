"""
Google Slides MCP Tools

This module provides MCP tools for interacting with Google Slides API.
"""

import logging
import asyncio
from typing import Any, Dict, Iterator, List, Optional

from mcp.types import ToolAnnotations

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors
from core.comments import create_comment_tools
from gslides.slides_helpers import (
    validate_batch_update_requests,
    validate_insert_text_targets,
)

logger = logging.getLogger(__name__)


def _extract_shape_text(shape: Optional[Dict[str, Any]]) -> str:
    """Extract the full text content from a Slides shape, sorted by text-run start index.

    Returns an empty string if the shape has no text. The Slides API stores text
    as a tree of textElements containing textRuns; this walks that tree, sorts
    runs by startIndex, and joins their content. See:
    https://googleapis.github.io/google-api-python-client/docs/dyn/slides_v1.presentations.html#get
    """
    if not shape:
        return ""
    text = shape.get("text")
    if not text:
        return ""
    runs = []
    for text_element in text.get("textElements", []):
        text_run = text_element.get("textRun")
        if text_run and text_run.get("content"):
            runs.append((text_element.get("startIndex", 0), text_run["content"]))
    if not runs:
        return ""
    runs.sort(key=lambda r: r[0])
    return "".join(r[1] for r in runs)


def _iter_text_bearing_elements(
    elements: Optional[List[Dict[str, Any]]],
) -> Iterator[str]:
    """Yield full text strings from any shape with non-empty text, descending
    recursively into elementGroup.children so grouped shapes are not skipped.
    """
    for element in elements or []:
        if "shape" in element:
            full_text = _extract_shape_text(element["shape"])
            if full_text:
                yield full_text
        elif "elementGroup" in element:
            children = element["elementGroup"].get("children", [])
            yield from _iter_text_bearing_elements(children)


def _describe_elements(
    elements: Optional[List[Dict[str, Any]]], indent: str = "  "
) -> List[str]:
    """Build descriptive lines for page elements, including text content for shapes.

    Recurses into elementGroup.children with deeper indentation so grouped shapes
    and their text are visible. Multi-line shape text is rendered as indented
    blockquote-style lines preserving paragraph structure.
    """
    info: List[str] = []
    for element in elements or []:
        element_id = element.get("objectId", "Unknown")
        if "shape" in element:
            shape_type = element["shape"].get("shapeType", "Unknown")
            full_text = _extract_shape_text(element["shape"])
            if full_text:
                lines = [
                    line.rstrip() for line in full_text.split("\n") if line.strip()
                ]
                if len(lines) == 1:
                    info.append(
                        f'{indent}Shape: ID {element_id}, Type: {shape_type}, Text: "{lines[0]}"'
                    )
                else:
                    info.append(
                        f"{indent}Shape: ID {element_id}, Type: {shape_type}, Text:"
                    )
                    info.extend(f"{indent}  > {line}" for line in lines)
            else:
                info.append(f"{indent}Shape: ID {element_id}, Type: {shape_type}")
        elif "table" in element:
            table = element["table"]
            rows = table.get("rows", 0)
            cols = table.get("columns", 0)
            info.append(f"{indent}Table: ID {element_id}, Size: {rows}x{cols}")
        elif "line" in element:
            line_type = element["line"].get("lineType", "Unknown")
            info.append(f"{indent}Line: ID {element_id}, Type: {line_type}")
        elif "elementGroup" in element:
            children = element["elementGroup"].get("children", [])
            info.append(f"{indent}Group: ID {element_id}, Children: {len(children)}")
            info.extend(_describe_elements(children, indent + "  "))
        else:
            info.append(f"{indent}Element: ID {element_id}, Type: Unknown")
    return info


@server.tool(
    title="Create Presentation",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("create_presentation", service_type="slides")
@require_google_service("slides", "slides")
async def create_presentation(
    service, user_google_email: str, title: str = "Untitled Presentation"
) -> str:
    """
    Create a new Google Slides presentation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        title (str): The title for the new presentation. Defaults to "Untitled Presentation".

    Returns:
        str: Details about the created presentation including ID and URL.
    """
    logger.info(
        f"[create_presentation] Invoked. Email: '{user_google_email}', Title: '{title}'"
    )

    body = {"title": title}

    result = await asyncio.to_thread(service.presentations().create(body=body).execute)

    presentation_id = result.get("presentationId")
    presentation_url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"

    confirmation_message = f"""Presentation Created Successfully for {user_google_email}:
- Title: {title}
- Presentation ID: {presentation_id}
- URL: {presentation_url}
- Slides: {len(result.get("slides", []))} slide(s) created"""

    logger.info(f"Presentation created successfully for {user_google_email}")
    return confirmation_message


@server.tool(
    title="Get Presentation",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("get_presentation", is_read_only=True, service_type="slides")
@require_google_service("slides", "slides_read")
async def get_presentation(
    service, user_google_email: str, presentation_id: str
) -> str:
    """
    Get details about a Google Slides presentation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation to retrieve.

    Returns:
        str: Details about the presentation including title, slides count, and metadata.
    """
    logger.info(
        f"[get_presentation] Invoked. Email: '{user_google_email}', ID: '{presentation_id}'"
    )

    result = await asyncio.to_thread(
        service.presentations().get(presentationId=presentation_id).execute
    )

    title = result.get("title", "Untitled")
    slides = result.get("slides", [])
    page_size = result.get("pageSize", {})

    slides_info = []
    for i, slide in enumerate(slides, 1):
        slide_id = slide.get("objectId", "Unknown")
        page_elements = slide.get("pageElements", [])

        # Collect text from the slide, recursing into elementGroup.children so
        # grouped shapes (common for layout templates) are not skipped. The
        # Slides API JSON structure is documented at:
        # https://googleapis.github.io/google-api-python-client/docs/dyn/slides_v1.presentations.html#get
        slide_text = ""
        try:
            texts_from_elements = list(_iter_text_bearing_elements(page_elements))

            # cleanup text we collected
            slide_text = "\n".join(texts_from_elements)
            slide_text_rows = slide_text.split("\n")
            slide_text_rows = [row for row in slide_text_rows if len(row.strip()) > 0]
            if slide_text_rows:
                slide_text_rows = ["    > " + row for row in slide_text_rows]
                slide_text = "\n" + "\n".join(slide_text_rows)
            else:
                slide_text = ""
        except Exception as e:
            logger.warning(f"Failed to extract text from the slide {slide_id}: {e}")
            slide_text = f"<failed to extract text: {type(e)}, {e}>"

        slides_info.append(
            f"  Slide {i}: ID {slide_id}, {len(page_elements)} element(s), text: {slide_text if slide_text else 'empty'}"
        )

    confirmation_message = f"""Presentation Details for {user_google_email}:
- Title: {title}
- Presentation ID: {presentation_id}
- URL: https://docs.google.com/presentation/d/{presentation_id}/edit
- Total Slides: {len(slides)}
- Page Size: {page_size.get("width", {}).get("magnitude", "Unknown")} x {page_size.get("height", {}).get("magnitude", "Unknown")} {page_size.get("width", {}).get("unit", "")}

Slides Breakdown:
{chr(10).join(slides_info) if slides_info else "  No slides found"}"""

    logger.info(f"Presentation retrieved successfully for {user_google_email}")
    return confirmation_message


@server.tool(
    title="Batch Update Presentation",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("batch_update_presentation", service_type="slides")
@require_google_service("slides", "slides")
async def batch_update_presentation(
    service,
    user_google_email: str,
    presentation_id: str,
    requests: List[Dict[str, Any]],
) -> str:
    """
    Apply batch updates to a Google Slides presentation.

    Important:
        Each request object must contain exactly one supported Slides request
        type, such as createSlide, createShape, insertText, updateTextStyle,
        createImage, or deleteObject.

        insertText.objectId must be a text-capable shape or table object ID,
        not a slide/page object ID. To add text to a slide, create a text box
        or shape first with createShape, set elementProperties.pageObjectId to
        the slide ID, and then insertText into that shape objectId. To edit
        existing text, call get_page and use a Shape or Table element ID.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation to update.
        requests (List[Dict[str, Any]]): List of update requests to apply.

    Returns:
        str: Details about the batch update operation results.
    """
    logger.info(
        f"[batch_update_presentation] Invoked. Email: '{user_google_email}', ID: '{presentation_id}', Requests: {len(requests)}"
    )

    validate_batch_update_requests(requests)
    await validate_insert_text_targets(service, presentation_id, requests)

    body = {"requests": requests}

    result = await asyncio.to_thread(
        service.presentations()
        .batchUpdate(presentationId=presentation_id, body=body)
        .execute
    )

    replies = result.get("replies", [])

    confirmation_message = f"""Batch Update Completed for {user_google_email}:
- Presentation ID: {presentation_id}
- URL: https://docs.google.com/presentation/d/{presentation_id}/edit
- Requests Applied: {len(requests)}
- Replies Received: {len(replies)}"""

    if replies:
        confirmation_message += "\n\nUpdate Results:"
        for i, reply in enumerate(replies, 1):
            if "createSlide" in reply:
                slide_id = reply["createSlide"].get("objectId", "Unknown")
                confirmation_message += (
                    f"\n  Request {i}: Created slide with ID {slide_id}"
                )
            elif "createShape" in reply:
                shape_id = reply["createShape"].get("objectId", "Unknown")
                confirmation_message += (
                    f"\n  Request {i}: Created shape with ID {shape_id}"
                )
            else:
                confirmation_message += f"\n  Request {i}: Operation completed"

    logger.info(f"Batch update completed successfully for {user_google_email}")
    return confirmation_message


@server.tool(
    title="Get Page",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("get_page", is_read_only=True, service_type="slides")
@require_google_service("slides", "slides_read")
async def get_page(
    service, user_google_email: str, presentation_id: str, page_object_id: str
) -> str:
    """
    Get details about a specific page (slide) in a presentation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation.
        page_object_id (str): The object ID of the page/slide to retrieve.

    Returns:
        str: Details about the specific page including elements and layout.
    """
    logger.info(
        f"[get_page] Invoked. Email: '{user_google_email}', Presentation: '{presentation_id}', Page: '{page_object_id}'"
    )

    result = await asyncio.to_thread(
        service.presentations()
        .pages()
        .get(presentationId=presentation_id, pageObjectId=page_object_id)
        .execute
    )

    page_type = result.get("pageType", "Unknown")
    page_elements = result.get("pageElements", [])

    # Walk pageElements recursively, surfacing text content from shapes and
    # descending into elementGroup.children so grouped shapes are not hidden.
    # This is what makes the documented "call get_page and use a Shape or Table
    # element ID" workflow in batch_update_presentation actually viable for
    # text that lives inside a Group.
    elements_info = _describe_elements(page_elements)

    confirmation_message = f"""Page Details for {user_google_email}:
- Presentation ID: {presentation_id}
- Page ID: {page_object_id}
- Page Type: {page_type}
- Total Elements: {len(page_elements)}

Page Elements:
{chr(10).join(elements_info) if elements_info else "  No elements found"}"""

    logger.info(f"Page retrieved successfully for {user_google_email}")
    return confirmation_message


@server.tool(
    title="Get Page Thumbnail",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("get_page_thumbnail", is_read_only=True, service_type="slides")
@require_google_service("slides", "slides_read")
async def get_page_thumbnail(
    service,
    user_google_email: str,
    presentation_id: str,
    page_object_id: str,
    thumbnail_size: str = "MEDIUM",
) -> str:
    """
    Generate a thumbnail URL for a specific page (slide) in a presentation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation.
        page_object_id (str): The object ID of the page/slide.
        thumbnail_size (str): Size of thumbnail ("LARGE", "MEDIUM", "SMALL"). Defaults to "MEDIUM".

    Returns:
        str: URL to the generated thumbnail image.
    """
    logger.info(
        f"[get_page_thumbnail] Invoked. Email: '{user_google_email}', Presentation: '{presentation_id}', Page: '{page_object_id}', Size: '{thumbnail_size}'"
    )

    result = await asyncio.to_thread(
        service.presentations()
        .pages()
        .getThumbnail(
            presentationId=presentation_id,
            pageObjectId=page_object_id,
            thumbnailProperties_thumbnailSize=thumbnail_size,
            thumbnailProperties_mimeType="PNG",
        )
        .execute
    )

    thumbnail_url = result.get("contentUrl", "")

    confirmation_message = f"""Thumbnail Generated for {user_google_email}:
- Presentation ID: {presentation_id}
- Page ID: {page_object_id}
- Thumbnail Size: {thumbnail_size}
- Thumbnail URL: {thumbnail_url}

You can view or download the thumbnail using the provided URL."""

    logger.info(f"Thumbnail generated successfully for {user_google_email}")
    return confirmation_message


# Create comment management tools for slides
_comment_tools = create_comment_tools("presentation", "presentation_id")
list_presentation_comments = _comment_tools["list_comments"]
manage_presentation_comment = _comment_tools["manage_comment"]

# Aliases for backwards compatibility and intuitive naming
list_slide_comments = list_presentation_comments
manage_slide_comment = manage_presentation_comment
