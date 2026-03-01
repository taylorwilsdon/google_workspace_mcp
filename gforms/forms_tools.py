"""
Google Forms MCP Tools

This module provides MCP tools for interacting with Google Forms API.
"""

import logging
import asyncio
from typing import Literal, Optional, Dict, Any


from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors

logger = logging.getLogger(__name__)


@server.tool()
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
        form_body["info"]["documentTitle"] = document_title

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


@server.tool()
@handle_http_errors("manage_form", service_type="forms")
@require_google_service("forms", "forms")
async def manage_form(
    service,
    user_google_email: str,
    operation: Literal["get", "update_settings"],
    form_id: str,
    publish_as_template: bool = False,
    require_authentication: bool = False,
) -> str:
    """
    Manage form metadata or publish settings.

    operation: Literal["get", "update_settings"]
    - get: Retrieve form details (title, description, questions, URLs).
    - update_settings: Update publish/template and authentication settings.

    Examples:
    - manage_form(..., operation="get", form_id="abc123")
    - manage_form(..., operation="update_settings", form_id="abc123", publish_as_template=True)
    """
    logger.info(
        f"[manage_form] Operation={operation}, Email='{user_google_email}', Form ID: {form_id}"
    )

    if operation == "get":
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
        questions_summary = []
        for i, item in enumerate(items, 1):
            item_title = item.get("title", f"Question {i}")
            item_type = (
                item.get("questionItem", {}).get("question", {}).get("required", False)
            )
            required_text = " (Required)" if item_type else ""
            questions_summary.append(f"  {i}. {item_title}{required_text}")

        questions_text = (
            "\n".join(questions_summary)
            if questions_summary
            else "  No questions found"
        )

        logger.info(
            f"Successfully retrieved form for {user_google_email}. ID: {form_id}"
        )
        return f"""Form Details for {user_google_email}:
- Title: "{title}"
- Description: "{description}"
- Document Title: "{document_title}"
- Form ID: {form_id}
- Edit URL: {edit_url}
- Responder URL: {responder_url}
- Questions ({len(items)} total):
{questions_text}"""

    if operation == "update_settings":
        settings_body = {
            "publishAsTemplate": publish_as_template,
            "requireAuthentication": require_authentication,
        }

        await asyncio.to_thread(
            service.forms()
            .setPublishSettings(formId=form_id, body=settings_body)
            .execute
        )

        logger.info(
            f"Publish settings updated successfully for {user_google_email}. Form ID: {form_id}"
        )
        return (
            f"Successfully updated publish settings for form {form_id} for {user_google_email}. "
            f"Publish as template: {publish_as_template}, Require authentication: {require_authentication}"
        )

    raise Exception("Unsupported operation. Use 'get' or 'update_settings'.")


@server.tool()
@handle_http_errors("get_form_responses", is_read_only=True, service_type="forms")
@require_google_service("forms", "forms")
async def get_form_responses(
    service,
    user_google_email: str,
    operation: Literal["list", "get"],
    form_id: str,
    response_id: Optional[str] = None,
    page_size: int = 10,
    page_token: Optional[str] = None,
) -> str:
    """
    Retrieve responses for a form.

    operation: Literal["list", "get"]
    - list: List responses with basic details (supports pagination).
    - get: Fetch a single response by response_id.

    Examples:
    - get_form_responses(..., operation="list", form_id="abc123", page_size=5)
    - get_form_responses(..., operation="get", form_id="abc123", response_id="resp-1")
    """
    logger.info(
        f"[get_form_responses] Operation={operation}, Form ID: {form_id}, Response ID: {response_id}"
    )

    if operation == "get":
        if not response_id:
            raise Exception("Operation 'get' requires 'response_id'.")

        response = await asyncio.to_thread(
            service.forms()
            .responses()
            .get(formId=form_id, responseId=response_id)
            .execute
        )

        resp_id = response.get("responseId", "Unknown")
        create_time = response.get("createTime", "Unknown")
        last_submitted_time = response.get("lastSubmittedTime", "Unknown")

        answers = response.get("answers", {})
        answer_details = []
        for question_id, answer_data in answers.items():
            question_response = answer_data.get("textAnswers", {}).get("answers", [])
            if question_response:
                answer_text = ", ".join(
                    [ans.get("value", "") for ans in question_response]
                )
                answer_details.append(f"  Question ID {question_id}: {answer_text}")
            else:
                answer_details.append(
                    f"  Question ID {question_id}: No answer provided"
                )

        answers_text = (
            "\n".join(answer_details) if answer_details else "  No answers found"
        )

        logger.info(
            f"Successfully retrieved response for {user_google_email}. Response ID: {resp_id}"
        )
        return f"""Form Response Details for {user_google_email}:
- Form ID: {form_id}
- Response ID: {resp_id}
- Created: {create_time}
- Last Submitted: {last_submitted_time}
- Answers:
{answers_text}"""

    if operation == "list":
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
            resp_id = response.get("responseId", "Unknown")
            create_time = response.get("createTime", "Unknown")
            last_submitted_time = response.get("lastSubmittedTime", "Unknown")

            answers_count = len(response.get("answers", {}))
            response_details.append(
                f"  {i}. Response ID: {resp_id} | Created: {create_time} | Last Submitted: {last_submitted_time} | Answers: {answers_count}"
            )

        pagination_info = (
            f"\nNext page token: {next_page_token}"
            if next_page_token
            else "\nNo more pages."
        )

        logger.info(
            f"Successfully retrieved {len(responses)} responses for {user_google_email}. Form ID: {form_id}"
        )
        return f"""Form Responses for {user_google_email}:
- Form ID: {form_id}
- Total responses returned: {len(responses)}
- Responses:
{chr(10).join(response_details)}{pagination_info}"""

    raise Exception("Unsupported operation. Use 'list' or 'get'.")
