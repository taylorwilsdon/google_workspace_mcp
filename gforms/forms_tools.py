"""
Google Forms MCP Tools

This module provides MCP tools for interacting with Google Forms API.
"""

import logging
import asyncio
import json
from typing import List, Optional, Dict, Any


from mcp.types import ToolAnnotations

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors

logger = logging.getLogger(__name__)


def _extract_option_values(options: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract valid option objects from Forms choice option objects.

    Returns the full option dicts (preserving fields like ``isOther``,
    ``image``, ``goToAction``, and ``goToSectionId``) while filtering
    out entries that lack a truthy ``value``.
    """
    return [option for option in options if option.get("value")]


def _get_question_type(question: Dict[str, Any]) -> str:
    """Infer a stable question/item type label from a Forms question payload."""
    choice_question = question.get("choiceQuestion")
    if choice_question:
        return choice_question.get("type", "CHOICE")

    text_question = question.get("textQuestion")
    if text_question:
        return "PARAGRAPH" if text_question.get("paragraph") else "TEXT"

    if "rowQuestion" in question:
        return "GRID_ROW"
    if "scaleQuestion" in question:
        return "SCALE"
    if "dateQuestion" in question:
        return "DATE"
    if "timeQuestion" in question:
        return "TIME"
    if "fileUploadQuestion" in question:
        return "FILE_UPLOAD"
    if "ratingQuestion" in question:
        return "RATING"

    return "QUESTION"


def _serialize_form_item(item: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Serialize a Forms item with the key metadata agents need for edits."""
    serialized_item: Dict[str, Any] = {
        "index": index,
        "itemId": item.get("itemId"),
        "title": item.get("title", f"Question {index}"),
    }

    if item.get("description"):
        serialized_item["description"] = item["description"]

    if "questionItem" in item:
        question = item.get("questionItem", {}).get("question", {})
        serialized_item["type"] = _get_question_type(question)
        serialized_item["required"] = question.get("required", False)

        question_id = question.get("questionId")
        if question_id:
            serialized_item["questionId"] = question_id

        choice_question = question.get("choiceQuestion")
        if choice_question:
            serialized_item["options"] = _extract_option_values(
                choice_question.get("options", [])
            )

        return serialized_item

    if "questionGroupItem" in item:
        question_group = item.get("questionGroupItem", {})
        columns = _extract_option_values(
            question_group.get("grid", {}).get("columns", {}).get("options", [])
        )

        rows = []
        for question in question_group.get("questions", []):
            row: Dict[str, Any] = {
                "title": question.get("rowQuestion", {}).get("title", "")
            }
            row_question_id = question.get("questionId")
            if row_question_id:
                row["questionId"] = row_question_id
            row["required"] = question.get("required", False)
            rows.append(row)

        serialized_item["type"] = "GRID"
        serialized_item["grid"] = {"rows": rows, "columns": columns}
        return serialized_item

    if "pageBreakItem" in item:
        serialized_item["type"] = "PAGE_BREAK"
    elif "textItem" in item:
        serialized_item["type"] = "TEXT_ITEM"
    elif "imageItem" in item:
        serialized_item["type"] = "IMAGE"
    elif "videoItem" in item:
        serialized_item["type"] = "VIDEO"
    else:
        serialized_item["type"] = "UNKNOWN"

    return serialized_item


@server.tool(
    title="Create Form",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("create_form", service_type="forms")
@require_google_service("forms", "forms")
async def create_form(
    service,
    user_google_email: str,
    title: str,
    description: Optional[str] = None,
    document_title: Optional[str] = None,
) -> str:
    """
    Create a new form using the title given in the provided form message in the request.

    Args:
        user_google_email (str): The user's Google email address. Required.
        title (str): The title of the form.
        description (Optional[str]): The description of the form.
        document_title (Optional[str]): The document title (shown in browser tab).

    Returns:
        str: Confirmation message with form ID and edit URL.
    """
    logger.info(f"[create_form] Invoked. Email: '{user_google_email}', Title: {title}")

    form_body: Dict[str, Any] = {"info": {"title": title}}

    if description:
        form_body["info"]["description"] = description

    if document_title:
        form_body["info"]["document_title"] = document_title

    created_form = await asyncio.to_thread(
        service.forms().create(body=form_body).execute
    )

    form_id = created_form.get("formId")
    edit_url = f"https://docs.google.com/forms/d/{form_id}/edit"
    responder_url = created_form.get(
        "responderUri", f"https://docs.google.com/forms/d/{form_id}/viewform"
    )

    confirmation_message = f"Successfully created form '{created_form.get('info', {}).get('title', title)}' for {user_google_email}. Form ID: {form_id}. Edit URL: {edit_url}. Responder URL: {responder_url}"
    logger.info(f"Form created successfully for {user_google_email}. ID: {form_id}")
    return confirmation_message


@server.tool(
    title="Get Form",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("get_form", is_read_only=True, service_type="forms")
@require_google_service("forms", "forms")
async def get_form(service, user_google_email: str, form_id: str) -> str:
    """
    Get a form.

    Args:
        user_google_email (str): The user's Google email address. Required.
        form_id (str): The ID of the form to retrieve.

    Returns:
        str: Form details including title, description, questions, and URLs.
    """
    logger.info(f"[get_form] Invoked. Email: '{user_google_email}', Form ID: {form_id}")

    form = await asyncio.to_thread(service.forms().get(formId=form_id).execute)

    form_info = form.get("info", {})
    title = form_info.get("title", "No Title")
    description = form_info.get("description", "No Description")
    document_title = form_info.get("documentTitle", title)

    edit_url = f"https://docs.google.com/forms/d/{form_id}/edit"
    responder_url = form.get(
        "responderUri", f"https://docs.google.com/forms/d/{form_id}/viewform"
    )

    items = form.get("items", [])
    serialized_items = [
        _serialize_form_item(item, i) for i, item in enumerate(items, 1)
    ]

    items_summary = []
    for serialized_item in serialized_items:
        item_index = serialized_item["index"]
        item_title = serialized_item.get("title", f"Item {item_index}")
        item_type = serialized_item.get("type", "UNKNOWN")
        required_text = " (Required)" if serialized_item.get("required") else ""
        items_summary.append(
            f"  {item_index}. {item_title} [{item_type}]{required_text}"
        )

    items_summary_text = (
        "\n".join(items_summary) if items_summary else "  No items found"
    )
    items_text = json.dumps(serialized_items, indent=2) if serialized_items else "[]"

    result = f"""Form Details for {user_google_email}:
- Title: "{title}"
- Description: "{description}"
- Document Title: "{document_title}"
- Form ID: {form_id}
- Edit URL: {edit_url}
- Responder URL: {responder_url}
- Items ({len(items)} total):
{items_summary_text}
- Items (structured):
{items_text}"""

    logger.info(f"Successfully retrieved form for {user_google_email}. ID: {form_id}")
    return result


@server.tool(
    title="Set Publish Settings",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("set_publish_settings", service_type="forms")
@require_google_service("forms", "forms")
async def set_publish_settings(
    service,
    user_google_email: str,
    form_id: str,
    publish_as_template: bool = False,
    require_authentication: bool = False,
) -> str:
    """
    Updates the publish settings of a form.

    Args:
        user_google_email (str): The user's Google email address. Required.
        form_id (str): The ID of the form to update publish settings for.
        publish_as_template (bool): Whether to publish as a template. Defaults to False.
        require_authentication (bool): Whether to require authentication to view/submit. Defaults to False.

    Returns:
        str: Confirmation message of the successful publish settings update.
    """
    logger.info(
        f"[set_publish_settings] Invoked. Email: '{user_google_email}', Form ID: {form_id}"
    )

    settings_body = {
        "publishAsTemplate": publish_as_template,
        "requireAuthentication": require_authentication,
    }

    await asyncio.to_thread(
        service.forms().setPublishSettings(formId=form_id, body=settings_body).execute
    )

    confirmation_message = f"Successfully updated publish settings for form {form_id} for {user_google_email}. Publish as template: {publish_as_template}, Require authentication: {require_authentication}"
    logger.info(
        f"Publish settings updated successfully for {user_google_email}. Form ID: {form_id}"
    )
    return confirmation_message


@server.tool(
    title="Get Form Response",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("get_form_response", is_read_only=True, service_type="forms")
@require_google_service("forms", "forms")
async def get_form_response(
    service, user_google_email: str, form_id: str, response_id: str
) -> str:
    """
    Get one response from the form.

    Args:
        user_google_email (str): The user's Google email address. Required.
        form_id (str): The ID of the form.
        response_id (str): The ID of the response to retrieve.

    Returns:
        str: Response details including answers and metadata.
    """
    logger.info(
        f"[get_form_response] Invoked. Email: '{user_google_email}', Form ID: {form_id}, Response ID: {response_id}"
    )

    response = await asyncio.to_thread(
        service.forms().responses().get(formId=form_id, responseId=response_id).execute
    )

    response_id = response.get("responseId", "Unknown")
    create_time = response.get("createTime", "Unknown")
    last_submitted_time = response.get("lastSubmittedTime", "Unknown")

    answers = response.get("answers", {})
    answer_details = []
    for question_id, answer_data in answers.items():
        question_response = answer_data.get("textAnswers", {}).get("answers", [])
        if question_response:
            answer_text = ", ".join([ans.get("value", "") for ans in question_response])
            answer_details.append(f"  Question ID {question_id}: {answer_text}")
        else:
            answer_details.append(f"  Question ID {question_id}: No answer provided")

    answers_text = "\n".join(answer_details) if answer_details else "  No answers found"

    result = f"""Form Response Details for {user_google_email}:
- Form ID: {form_id}
- Response ID: {response_id}
- Created: {create_time}
- Last Submitted: {last_submitted_time}
- Answers:
{answers_text}"""

    logger.info(
        f"Successfully retrieved response for {user_google_email}. Response ID: {response_id}"
    )
    return result


@server.tool(
    title="List Form Responses",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("list_form_responses", is_read_only=True, service_type="forms")
@require_google_service("forms", "forms")
async def list_form_responses(
    service,
    user_google_email: str,
    form_id: str,
    page_size: int = 10,
    page_token: Optional[str] = None,
) -> str:
    """
    List a form's responses.

    Args:
        user_google_email (str): The user's Google email address. Required.
        form_id (str): The ID of the form.
        page_size (int): Maximum number of responses to return. Defaults to 10.
        page_token (Optional[str]): Token for retrieving next page of results.

    Returns:
        str: List of responses with basic details and pagination info.
    """
    logger.info(
        f"[list_form_responses] Invoked. Email: '{user_google_email}', Form ID: {form_id}"
    )

    params = {"formId": form_id, "pageSize": page_size}
    if page_token:
        params["pageToken"] = page_token

    responses_result = await asyncio.to_thread(
        service.forms().responses().list(**params).execute
    )

    responses = responses_result.get("responses", [])
    next_page_token = responses_result.get("nextPageToken")

    if not responses:
        return f"No responses found for form {form_id} for {user_google_email}."

    response_details = []
    for i, response in enumerate(responses, 1):
        response_id = response.get("responseId", "Unknown")
        create_time = response.get("createTime", "Unknown")
        last_submitted_time = response.get("lastSubmittedTime", "Unknown")

        answers_count = len(response.get("answers", {}))
        response_details.append(
            f"  {i}. Response ID: {response_id} | Created: {create_time} | Last Submitted: {last_submitted_time} | Answers: {answers_count}"
        )

    pagination_info = (
        f"\nNext page token: {next_page_token}"
        if next_page_token
        else "\nNo more pages."
    )

    result = f"""Form Responses for {user_google_email}:
- Form ID: {form_id}
- Total responses returned: {len(responses)}
- Responses:
{chr(10).join(response_details)}{pagination_info}"""

    logger.info(
        f"Successfully retrieved {len(responses)} responses for {user_google_email}. Form ID: {form_id}"
    )
    return result


# Internal implementation function for testing
async def _batch_update_form_impl(
    service: Any,
    form_id: str,
    requests: List[Dict[str, Any]],
) -> str:
    """Internal implementation for batch_update_form.

    Applies batch updates to a Google Form using the Forms API batchUpdate method.

    Args:
        service: Google Forms API service client.
        form_id: The ID of the form to update.
        requests: List of update request dictionaries.

    Returns:
        Formatted string with batch update results.
    """
    body = {"requests": requests}

    result = await asyncio.to_thread(
        service.forms().batchUpdate(formId=form_id, body=body).execute
    )

    replies = result.get("replies", [])

    confirmation_message = f"""Batch Update Completed:
- Form ID: {form_id}
- URL: https://docs.google.com/forms/d/{form_id}/edit
- Requests Applied: {len(requests)}
- Replies Received: {len(replies)}"""

    if replies:
        confirmation_message += "\n\nUpdate Results:"
        for i, reply in enumerate(replies, 1):
            if "createItem" in reply:
                item_id = reply["createItem"].get("itemId", "Unknown")
                question_ids = reply["createItem"].get("questionId", [])
                question_info = (
                    f" (Question IDs: {', '.join(question_ids)})"
                    if question_ids
                    else ""
                )
                confirmation_message += (
                    f"\n  Request {i}: Created item {item_id}{question_info}"
                )
            else:
                confirmation_message += f"\n  Request {i}: Operation completed"

    return confirmation_message


@server.tool(
    title="Batch Update Form",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("batch_update_form", service_type="forms")
@require_google_service("forms", "forms")
async def batch_update_form(
    service,
    user_google_email: str,
    form_id: str,
    requests: List[Dict[str, Any]],
) -> str:
    """
    Apply batch updates to a Google Form.

    Supports adding, updating, and deleting form items, as well as updating
    form metadata and settings. This is the primary method for modifying form
    content after creation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        form_id (str): The ID of the form to update.
        requests (List[Dict[str, Any]]): List of update requests to apply.
            Supported request types:
            - createItem: Add a new question or content item
            - updateItem: Modify an existing item
            - deleteItem: Remove an item
            - moveItem: Reorder an item
            - updateFormInfo: Update form title/description
            - updateSettings: Modify form settings (e.g., quiz mode)

    Returns:
        str: Details about the batch update operation results.
    """
    logger.info(
        f"[batch_update_form] Invoked. Email: '{user_google_email}', "
        f"Form ID: '{form_id}', Requests: {len(requests)}"
    )

    result = await _batch_update_form_impl(service, form_id, requests)

    logger.info(f"Batch update completed successfully for {user_google_email}")
    return result
