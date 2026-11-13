"""
Google Slides MCP Tools

This module provides MCP tools for interacting with Google Slides API.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx
from fastmcp.tools import ToolResult
from mcp.types import ImageContent, TextContent, ToolAnnotations

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import (
    StringList,
    handle_http_errors,
    image_content_from_bytes,
    sniff_inline_image_mime_type,
)
from core.comments import create_comment_tools
from gslides.slides_helpers import (
    read_presentation,
    validate_batch_update_requests,
    validate_insert_text_targets,
    validate_page_image_size,
)

logger = logging.getLogger(__name__)


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
        f"[create_presentation] Invoked. Email: '{user_google_email}', title_len={len(title)}"
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
    service,
    user_google_email: str,
    presentation_id: str,
    action: str = "deck",
    page_id: Optional[str] = None,
    targets: Optional[StringList] = None,
    raw: bool = False,
    offset: int = 0,
    after: Optional[str] = None,
) -> str:
    """
    Read a deck: action "deck" (default) lists its slides and layouts, "page" gives one slide's element IDs and all its text (start here for content), "markup" gives one slide's elements as compact JSON with geometry and styles, "layouts" lists every layout.

    Actions:
        - "deck": Title, URL, revision, and per slide its ID, title, layout,
          element counts and notes length; then the deck's layouts with the
          placeholders each provides. Lists 100 slides per call; a longer deck
          ends with the offset to call again with. Uses offset.
        - "layouts": Every layout, grouped by master, with its placeholders
          and the slides using it. Only needed when "deck" says there are
          more layouts than it lists. Uses after.
        - "page": Every element on one slide with its ID, in z-order, groups
          nested, with all text, table cells, image sources and speaker notes.
          No geometry or styling. Requires page_id.
        - "markup": One slide, or chosen elements, as JSON: boxes in points
          ({"x","y","w","h"}), text as paragraphs and runs, styles in a shared
          "styles" table referenced by "s", only locally set properties.
          A shape with no "shape" key is a TEXT_BOX. Requires page_id; uses
          targets and raw.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation.
        action (str): "deck" (default): slides and layouts. "page": one
            slide's element IDs and text. "markup": one slide's elements as
            JSON with geometry and styles. "layouts": every layout.
        page_id (Optional[str]): Slide (or any page) object ID, from "deck".
            Required for "page" and "markup".
        targets (Optional[List[str]]): For "markup": element IDs to return
            instead of the whole slide, plus "notes" for the speaker notes.
            A target inside a group comes back with page coordinates and its
            "group" ID; a group comes back with its children.
        raw (bool): For "markup": return the Slides API Page resource itself
            (EMU, transforms, text indices), limited to targets when given,
            for writing raw batch_update_presentation requests. Defaults to
            False.
        offset (int): For "deck": index of the first slide to list, from the
            note at the end of a previous call. Defaults to 0.
        after (Optional[str]): For "layouts": a slide ID, or "start". Lists
            only the layouts of the master in use at that position, which are
            the only ones a new slide inserted there can use.

    Returns:
        str: The deck or layout digest, the page digest, or the markup JSON.
    """
    logger.info(
        f"[get_presentation] Invoked. Email: '{user_google_email}', ID: '{presentation_id}', Action: '{action}'"
    )
    return await read_presentation(
        service,
        presentation_id,
        action,
        page_id=page_id,
        targets=targets,
        raw=raw,
        offset=offset,
        after=after,
    )


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
        existing text, call get_presentation with action="page" and use a
        Shape or Table element ID.

        To write speaker (presenter) notes, call get_presentation with
        action="page" to get the slide's speaker notes shape ID, then target
        that ID: deleteText with textRange {"type": "ALL"} to clear
        the existing notes (omit this when they are already empty, which errors),
        followed by insertText at insertionIndex 0. The notes page ID is not a
        valid target.

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
            elif "replaceAllText" in reply:
                count = reply["replaceAllText"].get("occurrencesChanged", 0)
                confirmation_message += (
                    f"\n  Request {i}: Replaced {count} occurrence(s)"
                )
            else:
                confirmation_message += f"\n  Request {i}: Operation completed"

    logger.info(f"Batch update completed successfully for {user_google_email}")
    return confirmation_message


async def _fetch_page_image(url: str) -> Optional[ImageContent]:
    """Download a rendered page image, or return None if it cannot be retrieved."""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        # The URL grants the requester's access to anyone holding it, so it
        # stays out of the log.
        logger.warning(
            f"[render_page_to_image] Image fetch failed: {type(exc).__name__}"
        )
        return None
    content_type = response.headers.get("content-type", "").lower()
    mime_type = sniff_inline_image_mime_type(response.content)
    if not content_type.startswith("image/") or mime_type is None:
        logger.warning("[render_page_to_image] Image fetch returned a non-image")
        return None
    return image_content_from_bytes(response.content, mime_type)


@server.tool(
    title="Render Page to Image",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("render_page_to_image", is_read_only=True, service_type="slides")
@require_google_service("slides", "slides_read")
async def render_page_to_image(
    service,
    user_google_email: str,
    presentation_id: str,
    page_object_id: str,
    size: str = "MEDIUM",
) -> str:
    """
    Render a slide (or any page, including notes pages) to an image and return it as image content, for looking at a slide's layout and visuals.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation.
        page_object_id (str): The object ID of the page/slide to render.
        size (str): Image width. One of "SMALL" (200px), "MEDIUM" (800px),
            "LARGE" (1600px) or "WIDTH2000_PX" (2000px). Defaults to "MEDIUM".
            Start at MEDIUM: it reads titles and body text. Use LARGE for small
            text such as tables, chart labels and footnotes. WIDTH2000_PX
            rarely reads more than LARGE. SMALL shows layout only; its text is
            unreadable. Token cost and the resolution the model sees depend on
            the model: Claude models with high-resolution vision read MEDIUM at
            ~460 image tokens, LARGE at ~1,850 and WIDTH2000_PX at ~2,900, while
            other models may downscale LARGE and WIDTH2000_PX and count tokens
            differently.

    Returns:
        A short text block (presentation ID, page ID, size and a temporary
        image URL) followed by the rendered PNG as image content. If the image
        cannot be fetched, only the text block is returned, with a note saying
        so.
    """
    requested_width = validate_page_image_size(size)
    logger.info(
        f"[render_page_to_image] Invoked. Email: '{user_google_email}', Presentation: '{presentation_id}', Page: '{page_object_id}', Size: '{size}'"
    )

    result = await asyncio.to_thread(
        service.presentations()
        .pages()
        .getThumbnail(
            presentationId=presentation_id,
            pageObjectId=page_object_id,
            thumbnailProperties_thumbnailSize=size,
            thumbnailProperties_mimeType="PNG",
        )
        .execute
    )

    image_url = result.get("contentUrl", "")
    width = result.get("width", requested_width)
    dimensions = (
        f"{width}x{result['height']}px" if "height" in result else f"{width}px wide"
    )

    text = f"""Rendered page:
- Presentation ID: {presentation_id}
- Page ID: {page_object_id}
- Size: {size} ({dimensions})
- Image URL: {image_url or "(none returned)"}
  (expires after about 30 minutes and is tagged with the requesting account;
  anyone holding it can open the image while it lasts)"""

    image = await _fetch_page_image(image_url) if image_url else None
    if image is None:
        text += "\n\nImage unavailable: the rendered page could not be fetched."
        if image_url:
            text += " Open the image URL above instead, before it expires."
    content: List[TextContent | ImageContent] = [TextContent(type="text", text=text)]
    if image is not None:
        content.append(image)
    logger.info(f"Page rendered for {user_google_email}")
    # structured_content mirrors the text to satisfy the `str` output schema.
    return ToolResult(content=content, structured_content={"result": text})


# Create comment management tools for slides
_comment_tools = create_comment_tools("presentation", "presentation_id")
list_presentation_comments = _comment_tools["list_comments"]
manage_presentation_comment = _comment_tools["manage_comment"]

# Aliases for backwards compatibility and intuitive naming
list_slide_comments = list_presentation_comments
manage_slide_comment = manage_presentation_comment
