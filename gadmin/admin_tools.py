"""Opt-in read-only Google Admin SDK Directory tools."""

from mcp.types import ToolAnnotations

from auth.scopes import ADMIN_DIRECTORY_USER_READONLY_SCOPE
from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors
from gadmin.admin_helpers import get_users


@server.tool(
    title="Get Admin Users",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("get_admin_users", is_read_only=True, service_type="admin")
@require_google_service("admin", ADMIN_DIRECTORY_USER_READONLY_SCOPE)
async def get_admin_users(
    service,
    user_google_email: str,
    user_key: str | None = None,
    query: str | None = None,
    customer: str = "my_customer",
    max_results: int = 100,
    page_token: str | None = None,
) -> str:
    """Read domain users, aliases, suspension status and organizational-unit paths.

    Requires explicit admin service selection and Google-authorized domain-admin
    access. Supply user_key for one user, or list/search parameters for one page.
    Pass the returned nextPageToken as page_token to continue the same search.

    Args:
        user_google_email: Authenticated domain admin's Google email address.
        user_key: User primary email, alias or ID. Cannot combine with list parameters.
        query: Directory users search expression, for example isSuspended=true.
        customer: Customer ID, or my_customer for the authenticated admin's organization.
        max_results: Page size from 1 to 500; defaults to 100. List/search only.
        page_token: Token from the previous list/search result. Keep other parameters unchanged.

    Returns:
        JSON user, or users array and optional nextPageToken. No data is changed.
    """
    return await get_users(service, user_key, query, customer, max_results, page_token)
