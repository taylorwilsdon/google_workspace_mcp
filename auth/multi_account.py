"""
Read across every signed-in Google account in one tool call.

Legacy OAuth 2.0 mode can hold credentials for several accounts at once (one
file per address in the credential store), but each tool call reads one of
them. Someone whose mail and calendars are split across a personal and a work
account then has to call every tool once per address and merge the results by
hand, and a client that forgets one account sees free time that is not free.

Tools that opt in (``require_google_service(..., allow_all_accounts=True)``)
accept ``user_google_email="all"``. The decorator then hands the tool one
:class:`AccountService` per stored account instead of a single service, and the
tool merges what they return. An account whose credentials are missing, expired
or short of the tool's scopes is reported as such, without starting an OAuth
flow for it, so one broken account never hides the others' results.

Every stored account is readable this way, so it is only offered where the
stored accounts all belong to whoever runs the server: over stdio, or over
HTTP when WORKSPACE_MCP_ALLOW_ALL_ACCOUNTS is set. It is never offered under
OAuth 2.1 or a trusted gateway (the account is the verified caller's own),
service-account delegation, or stateless mode (no stored accounts).
"""

import asyncio
import logging
import os
import ssl
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Optional

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from auth.credential_store import get_credential_store
from auth.google_auth import (
    CONFIG_CLIENT_SECRETS_PATH,
    GoogleAuthenticationError,
    build_google_service,
    get_credentials,
)
from auth.oauth_config import (
    get_transport_mode,
    is_oauth21_enabled,
    is_service_account_enabled,
    is_stateless_mode,
    is_trust_gateway_identity,
)
from core.utils import UserInputError

logger = logging.getLogger(__name__)

ALL_ACCOUNTS = "all"

# Failures that belong to one account (or one calendar) and should not hide
# the others. Anything else is a bug and propagates.
ACCOUNT_ERRORS = (
    HttpError,
    RefreshError,
    GoogleAuthenticationError,
    ssl.SSLError,
    OSError,
)


@dataclass
class AccountService:
    """One stored account's service, or why it could not be opened."""

    email: str
    service: Optional[Any] = None
    error: Optional[str] = None


def is_all_accounts(user_google_email: Any) -> bool:
    """True when the caller asked for every signed-in account."""
    return (
        isinstance(user_google_email, str)
        and user_google_email.strip().lower() == ALL_ACCOUNTS
    )


def all_accounts_unavailable_reason() -> Optional[str]:
    """Why ``user_google_email="all"`` cannot be served here, or None if it can."""
    if is_oauth21_enabled() or is_trust_gateway_identity():
        return "the account is the authenticated caller's own"
    if is_service_account_enabled():
        return "service-account mode acts for one delegated user"
    if is_stateless_mode():
        return "stateless mode keeps no stored accounts"
    allowed = os.getenv("WORKSPACE_MCP_ALLOW_ALL_ACCOUNTS", "").strip().lower()
    if get_transport_mode() != "stdio" and allowed not in ("1", "true", "yes"):
        return (
            "over HTTP it needs WORKSPACE_MCP_ALLOW_ALL_ACCOUNTS=true, since it "
            "reads every account stored on the server"
        )
    return None


async def _open_account(
    email: str,
    service_name: str,
    version: str,
    scopes: List[str],
    tool_name: str,
) -> AccountService:
    try:
        credentials = await asyncio.to_thread(
            get_credentials,
            user_google_email=email,
            required_scopes=scopes,
            client_secrets_path=CONFIG_CLIENT_SECRETS_PATH,
            session_id=None,
        )
        if credentials and credentials.valid:
            service = build_google_service(service_name, version, credentials)
            return AccountService(email=email, service=service)
    except ACCOUNT_ERRORS as e:
        logger.warning(f"[{tool_name}] Could not open {email}: {e}")
    return AccountService(
        email=email,
        error=(
            "not authorized for this tool (signed out, expired, or missing "
            f"scopes). Call the tool with user_google_email='{email}' to "
            "re-authorize it."
        ),
    )


async def open_all_account_services(
    service_name: str, version: str, scopes: List[str], tool_name: str
) -> List[AccountService]:
    """Open ``service_name`` for every stored account, in address order."""
    reason = all_accounts_unavailable_reason()
    if reason:
        raise UserInputError(
            f"user_google_email='{ALL_ACCOUNTS}' is not available here: {reason}."
        )
    emails = sorted(get_credential_store().list_users())
    if not emails:
        raise UserInputError(
            "No Google accounts are signed in to this server. Call a tool with "
            "a specific user_google_email to sign one in."
        )
    logger.info(f"[{tool_name}] all accounts -> {service_name}/{version}: {emails}")
    tasks = [
        asyncio.ensure_future(
            _open_account(email, service_name, version, scopes, tool_name)
        )
        for email in emails
    ]
    try:
        await asyncio.wait(tasks)
        return [task.result() for task in tasks]
    except BaseException:
        # Nothing reaches the caller, so close what was already opened.
        for task in tasks:
            task.cancel()
            if task.done() and not task.cancelled() and not task.exception():
                if task.result().service is not None:
                    task.result().service.close()
        raise


async def gather_per_account(
    accounts: List[AccountService],
    fetch: Callable[[AccountService], Awaitable[Any]],
) -> List[Any]:
    """Run ``fetch(account)`` for every usable account concurrently.

    Each account has its own service (and HTTP connection), so accounts run in
    parallel; ``fetch`` must make one account's requests one at a time.

    Returns one entry per account, in order: ``fetch``'s result, or an
    ``AccountService`` carrying the error (the account's own, or the API
    failure ``fetch`` raised for it).
    """

    async def _one(account: AccountService):
        if account.service is None:
            return account
        try:
            return await fetch(account)
        except ACCOUNT_ERRORS as e:
            logger.warning(f"[all accounts] {account.email} failed: {e}")
            return AccountService(email=account.email, error=str(e))

    return list(await asyncio.gather(*(_one(a) for a in accounts)))


def format_account_errors(results: List[Any]) -> List[str]:
    """Output lines naming each account that could not be read."""
    failed = [r for r in results if isinstance(r, AccountService)]
    if not failed:
        return []
    lines = ["", "Accounts not included:"]
    lines.extend(f"- {r.email}: {r.error}" for r in failed)
    return lines
