"""Read-only Directory API calls for the opt-in admin service."""

import asyncio
import json

from core.utils import UserInputError


USER_FIELDS = "id,primaryEmail,name,aliases,nonEditableAliases,orgUnitPath,suspended"


async def get_users(service, user_key, query, customer, max_results, page_token):
    """Read one user or one page without fetching sensitive extended user fields."""
    if not 1 <= max_results <= 500:
        raise UserInputError("max_results must be between 1 and 500.")
    if user_key is not None:
        if not user_key.strip():
            raise UserInputError("user_key must not be empty.")
        if query or page_token or customer != "my_customer" or max_results != 100:
            raise UserInputError(
                "user_key cannot be combined with list/search parameters."
            )
        request = service.users().get(
            userKey=user_key, projection="basic", fields=USER_FIELDS
        )
    else:
        if not customer.strip():
            raise UserInputError("customer must not be empty.")
        request = service.users().list(
            customer=customer,
            query=query,
            maxResults=max_results,
            pageToken=page_token,
            projection="basic",
            fields=f"users({USER_FIELDS}),nextPageToken",
        )
    result = await asyncio.to_thread(request.execute)
    if user_key is None:
        result.setdefault("users", [])
    return json.dumps(result, indent=2)
