"""Integration test - manage_doc_tab populate_from_markdown against a real Google Doc.

Requires environment:
  - GOOGLE_CLIENT_SECRET_PATH pointing to OAuth credentials JSON
  - USER_GOOGLE_EMAIL set to an authenticated account
  - INTEGRATION_TEST_DOC_ID set to a Google Doc ID the test can mutate

Uses the cached OAuth token at ~/.workspace-mcp/spike_token.json (populated
by scripts/spike/spike_tab_operations.py). If no cached token is present
and the OAuth flow cannot run non-interactively, the test skips.

Run - uv run pytest tests/integration/ -v -m integration
"""

import os
import pathlib
import time

import pytest
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

pytestmark = pytest.mark.integration

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]
TOKEN_CACHE = pathlib.Path.home() / ".workspace-mcp" / "spike_token.json"


def _unwrap(tool):
    """Unwrap the decorated MCP tool function to the underlying implementation.

    Mirrors the convention in tests/gdocs/test_advanced_doc_formatting.py.
    """
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _load_credentials():
    if not TOKEN_CACHE.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_CACHE), SCOPES)
    except Exception:
        return None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_CACHE.write_text(creds.to_json())
            return creds
        except Exception:
            return None
    return None


@pytest.fixture
def docs_service():
    doc_id = os.environ.get("INTEGRATION_TEST_DOC_ID")
    if not doc_id:
        pytest.skip("INTEGRATION_TEST_DOC_ID not set")

    if not os.environ.get("USER_GOOGLE_EMAIL"):
        pytest.skip(
            "USER_GOOGLE_EMAIL not set. Export the Google account email the "
            "MCP tools should act as, then re-run."
        )

    creds = _load_credentials()
    if not creds:
        pytest.skip(
            "No cached OAuth credentials at ~/.workspace-mcp/spike_token.json. "
            "Run scripts/spike/spike_tab_operations.py once to authenticate."
        )

    return build("docs", "v1", credentials=creds), doc_id


def _create_scratch_tab(service, doc_id, title):
    """Create a fresh tab at index 0 and return its tab_id."""
    response = (
        service.documents()
        .batchUpdate(
            documentId=doc_id,
            body={
                "requests": [
                    {
                        "addDocumentTab": {
                            "tabProperties": {
                                "title": title,
                                "index": 0,
                            }
                        }
                    }
                ]
            },
        )
        .execute()
    )
    reply = response["replies"][0]
    return reply["addDocumentTab"]["tabProperties"]["tabId"]


def _delete_tab(service, doc_id, tab_id):
    """Delete a tab via direct Docs API."""
    service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": [{"deleteTab": {"tabId": tab_id}}]},
    ).execute()


def _count_tab_content(service, doc_id, tab_id):
    """Fetch the doc and return (n_structural_elements, char_count) for the tab."""
    doc = (
        service.documents()
        .get(
            documentId=doc_id,
            includeTabsContent=True,
        )
        .execute()
    )
    for t in doc.get("tabs", []):
        if t.get("tabProperties", {}).get("tabId") == tab_id:
            body = t.get("documentTab", {}).get("body", {})
            content = body.get("content", [])
            chars = 0
            for elem in content:
                for run in elem.get("paragraph", {}).get("elements", []):
                    chars += len(run.get("textRun", {}).get("content", ""))
            return len(content), chars
    return 0, 0


SAMPLE_MARKDOWN_FIRST = """# Integration Test - Round 1

This tab was populated by the integration test.

## Details

- Item alpha

- Item beta

- Item gamma
"""

SAMPLE_MARKDOWN_SECOND = """# Integration Test - Round 2

Replacement content. If this renders cleanly, replace_existing works.

**Bold** and *italic* should still format correctly.
"""


@pytest.mark.asyncio
async def test_populate_from_markdown_fills_empty_tab(docs_service):
    """Populating a fresh tab writes all the markdown requests and produces real content."""
    from gdocs.docs_tools import manage_doc_tab

    service, doc_id = docs_service
    tab_id = _create_scratch_tab(service, doc_id, f"Integration {int(time.time())}-A")

    try:
        fn = _unwrap(manage_doc_tab)

        result = await fn(
            service=service,
            user_google_email=os.environ["USER_GOOGLE_EMAIL"],
            document_id=doc_id,
            action="populate_from_markdown",
            tab_id=tab_id,
            markdown_text=SAMPLE_MARKDOWN_FIRST,
            replace_existing=True,
        )

        assert result["success"] is True
        assert result["requests_applied"] >= 5
        assert result["tab_id"] == tab_id

        elements, chars = _count_tab_content(service, doc_id, tab_id)
        assert elements >= 4, f"Expected several structural elements, got {elements}"
        assert chars > 50, f"Expected substantive char count, got {chars}"
    finally:
        _delete_tab(service, doc_id, tab_id)


@pytest.mark.asyncio
async def test_populate_from_markdown_replaces_existing_content(docs_service):
    """Calling with replace_existing=True on a populated tab swaps old for new cleanly."""
    from gdocs.docs_tools import manage_doc_tab

    service, doc_id = docs_service
    tab_id = _create_scratch_tab(service, doc_id, f"Integration {int(time.time())}-B")

    try:
        fn = _unwrap(manage_doc_tab)

        # First pass
        await fn(
            service=service,
            user_google_email=os.environ["USER_GOOGLE_EMAIL"],
            document_id=doc_id,
            action="populate_from_markdown",
            tab_id=tab_id,
            markdown_text=SAMPLE_MARKDOWN_FIRST,
            replace_existing=True,
        )
        _, first_chars = _count_tab_content(service, doc_id, tab_id)

        # Second pass should wipe and re-populate
        result = await fn(
            service=service,
            user_google_email=os.environ["USER_GOOGLE_EMAIL"],
            document_id=doc_id,
            action="populate_from_markdown",
            tab_id=tab_id,
            markdown_text=SAMPLE_MARKDOWN_SECOND,
            replace_existing=True,
        )

        assert result["success"] is True
        _, second_chars = _count_tab_content(service, doc_id, tab_id)
        assert second_chars != first_chars, (
            "Content char count should change after replacement"
        )
    finally:
        _delete_tab(service, doc_id, tab_id)
