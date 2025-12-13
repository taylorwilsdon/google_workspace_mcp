# auth/google_auth.py

import asyncio
import json
import jwt
import logging
import os

from typing import List, Optional, Tuple, Dict, Any
from urllib.parse import parse_qs, urlparse

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from auth.scopes import SCOPES, get_current_scopes  # noqa
from auth.oauth21_session_store import get_oauth21_session_store
from auth.credential_store import get_credential_store
from auth.oauth_config import get_oauth_config, is_stateless_mode
from core.config import (
    get_transport_mode,
    get_oauth_redirect_uri,
)
from core.context import get_fastmcp_session_id

# Try to import FastMCP dependencies (may not be available in all environments)
try:
    from fastmcp.server.dependencies import get_context as get_fastmcp_context
except ImportError:
    get_fastmcp_context = None

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Constants
def get_default_credentials_dir():
    """Get the default credentials directory path, preferring user-specific locations."""
    # Check for explicit environment variable override
    if os.getenv("GOOGLE_MCP_CREDENTIALS_DIR"):
        return os.getenv("GOOGLE_MCP_CREDENTIALS_DIR")

    # Use user home directory for credentials storage
    home_dir = os.path.expanduser("~")
    if home_dir and home_dir != "~":  # Valid home directory found
        return os.path.join(home_dir, ".google_workspace_mcp", "credentials")

    # Fallback to current working directory if home directory is not accessible
    return os.path.join(os.getcwd(), ".credentials")


DEFAULT_CREDENTIALS_DIR = get_default_credentials_dir()

# Session credentials now handled by OAuth21SessionStore - no local cache needed
# Centralized Client Secrets Path Logic
_client_secrets_env = os.getenv("GOOGLE_CLIENT_SECRET_PATH") or os.getenv(
    "GOOGLE_CLIENT_SECRETS"
)
if _client_secrets_env:
    CONFIG_CLIENT_SECRETS_PATH = _client_secrets_env
else:
    # Assumes this file is in auth/ and client_secret.json is in the root
    CONFIG_CLIENT_SECRETS_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "client_secret.json",
    )

# --- Helper Functions ---


def _find_any_credentials(
    base_dir: str = DEFAULT_CREDENTIALS_DIR,
) -> Optional[Credentials]:
    """
    Find and load any valid credentials from the credentials directory.
    Used in single-user mode to bypass session-to-OAuth mapping.

    Returns:
        First valid Credentials object found, or None if none exist.
    """
    try:
        store = get_credential_store()
        users = store.list_users()
        if not users:
            logger.info(
                "[single-user] No users found with credentials via credential store"
            )
            return None

        # Return credentials for the first user found
        first_user = users[0]
        credentials = store.get_credential(first_user)
        if credentials:
            logger.info(
                f"[single-user] Found credentials for {first_user} via credential store"
            )
            return credentials
        else:
            logger.warning(
                f"[single-user] Could not load credentials for {first_user} via credential store"
            )

    except Exception as e:
        logger.error(
            f"[single-user] Error finding credentials via credential store: {e}"
        )

    logger.info("[single-user] No valid credentials found via credential store")
    return None


def save_credentials_to_session(session_id: str, credentials: Credentials):
    """Saves user credentials using OAuth21SessionStore."""
    # Get user email from credentials if possible
    user_email = None
    if credentials and credentials.id_token:
        try:
            decoded_token = jwt.decode(
                credentials.id_token, options={"verify_signature": False}
            )
            user_email = decoded_token.get("email")
        except Exception as e:
            logger.debug(f"Could not decode id_token to get email: {e}")

    if user_email:
        store = get_oauth21_session_store()
        store.store_session(
            user_email=user_email,
            access_token=credentials.token,
            refresh_token=credentials.refresh_token,
            token_uri=credentials.token_uri,
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
            scopes=credentials.scopes,
            expiry=credentials.expiry,
            mcp_session_id=session_id,
        )
        logger.debug(
            f"Credentials saved to OAuth21SessionStore for session_id: {session_id}, user: {user_email}"
        )
    else:
        logger.warning(
            f"Could not save credentials to session store - no user email found for session: {session_id}"
        )


def load_credentials_from_session(session_id: str) -> Optional[Credentials]:
    """Loads user credentials from OAuth21SessionStore."""
    store = get_oauth21_session_store()
    credentials = store.get_credentials_by_mcp_session(session_id)
    if credentials:
        logger.debug(
            f"Credentials loaded from OAuth21SessionStore for session_id: {session_id}"
        )
    else:
        logger.debug(
            f"No credentials found in OAuth21SessionStore for session_id: {session_id}"
        )
    return credentials


def load_client_secrets_from_env() -> Optional[Dict[str, Any]]:
    """
    Loads the client secrets from environment variables.

    Environment variables used:
        - GOOGLE_OAUTH_CLIENT_ID: OAuth 2.0 client ID
        - GOOGLE_OAUTH_CLIENT_SECRET: OAuth 2.0 client secret
        - GOOGLE_OAUTH_REDIRECT_URI: (optional) OAuth redirect URI

    Returns:
        Client secrets configuration dict compatible with Google OAuth library,
        or None if required environment variables are not set.
    """
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")

    if client_id and client_secret:
        # Create config structure that matches Google client secrets format
        web_config = {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        }

        # Add redirect_uri if provided via environment variable
        if redirect_uri:
            web_config["redirect_uris"] = [redirect_uri]

        # Return the full config structure expected by Google OAuth library
        config = {"web": web_config}

        logger.info("Loaded OAuth client credentials from environment variables")
        return config

    logger.debug("OAuth client credentials not found in environment variables")
    return None


def load_client_secrets(client_secrets_path: str) -> Dict[str, Any]:
    """
    Loads the client secrets from environment variables (preferred) or from the client secrets file.

    Priority order:
    1. Environment variables (GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET)
    2. File-based credentials at the specified path

    Args:
        client_secrets_path: Path to the client secrets JSON file (used as fallback)

    Returns:
        Client secrets configuration dict

    Raises:
        ValueError: If client secrets file has invalid format
        IOError: If file cannot be read and no environment variables are set
    """
    # First, try to load from environment variables
    env_config = load_client_secrets_from_env()
    if env_config:
        # Extract the "web" config from the environment structure
        return env_config["web"]

    # Fall back to loading from file
    try:
        with open(client_secrets_path, "r") as f:
            client_config = json.load(f)
            # The file usually contains a top-level key like "web" or "installed"
            if "web" in client_config:
                logger.info(
                    f"Loaded OAuth client credentials from file: {client_secrets_path}"
                )
                return client_config["web"]
            elif "installed" in client_config:
                logger.info(
                    f"Loaded OAuth client credentials from file: {client_secrets_path}"
                )
                return client_config["installed"]
            else:
                logger.error(
                    f"Client secrets file {client_secrets_path} has unexpected format."
                )
                raise ValueError("Invalid client secrets file format")
    except (IOError, json.JSONDecodeError) as e:
        logger.error(f"Error loading client secrets file {client_secrets_path}: {e}")
        raise


def check_client_secrets() -> Optional[str]:
    """
    Checks for the presence of OAuth client secrets, either as environment
    variables or as a file.

    Returns:
        An error message string if secrets are not found, otherwise None.
    """
    env_config = load_client_secrets_from_env()
    if not env_config and not os.path.exists(CONFIG_CLIENT_SECRETS_PATH):
        logger.error(
            f"OAuth client credentials not found. No environment variables set and no file at {CONFIG_CLIENT_SECRETS_PATH}"
        )
        return f"OAuth client credentials not found. Please set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET environment variables or provide a client secrets file at {CONFIG_CLIENT_SECRETS_PATH}."
    return None


def create_oauth_flow(
    scopes: List[str], redirect_uri: str, state: Optional[str] = None
) -> Flow:
    """Creates an OAuth flow using environment variables or client secrets file."""
    # Try environment variables first
    env_config = load_client_secrets_from_env()
    if env_config:
        # Use client config directly
        flow = Flow.from_client_config(
            env_config, scopes=scopes, redirect_uri=redirect_uri, state=state
        )
        logger.debug("Created OAuth flow from environment variables")
        return flow

    # Fall back to file-based config
    if not os.path.exists(CONFIG_CLIENT_SECRETS_PATH):
        raise FileNotFoundError(
            f"OAuth client secrets file not found at {CONFIG_CLIENT_SECRETS_PATH} and no environment variables set"
        )

    flow = Flow.from_client_secrets_file(
        CONFIG_CLIENT_SECRETS_PATH,
        scopes=scopes,
        redirect_uri=redirect_uri,
        state=state,
    )
    logger.debug(
        f"Created OAuth flow from client secrets file: {CONFIG_CLIENT_SECRETS_PATH}"
    )
    return flow


# --- Core OAuth Logic ---


async def start_auth_flow(
    user_google_email: Optional[str],
    service_name: str,  # e.g., "Google Calendar", "Gmail" for user messages
    redirect_uri: str,  # Added redirect_uri as a required parameter
) -> str:
    """
    Initiates the Google OAuth flow and returns an actionable message for the user.

    Args:
        user_google_email: The user's specified Google email, if provided.
        service_name: The name of the Google service requiring auth (for user messages).
        redirect_uri: The URI Google will redirect to after authorization.

    Returns:
        A formatted string containing guidance for the LLM/user.

    Raises:
        Exception: If the OAuth flow cannot be initiated.
    """
    initial_email_provided = bool(
        user_google_email
        and user_google_email.strip()
        and user_google_email.lower() != "default"
    )
    user_display_name = (
        f"{service_name} for '{user_google_email}'"
        if initial_email_provided
        else service_name
    )

    logger.info(
        f"[start_auth_flow] Initiating auth for {user_display_name} with scopes for enabled tools."
    )

    # Note: Caller should ensure OAuth callback is available before calling this function

    try:
        if "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ and (
            "localhost" in redirect_uri or "127.0.0.1" in redirect_uri
        ):  # Use passed redirect_uri
            logger.warning(
                "OAUTHLIB_INSECURE_TRANSPORT not set. Setting it for localhost/local development."
            )
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

        oauth_state = os.urandom(16).hex()

        flow = create_oauth_flow(
            scopes=get_current_scopes(),  # Use scopes for enabled tools only
            redirect_uri=redirect_uri,  # Use passed redirect_uri
            state=oauth_state,
        )

        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")

        session_id = None
        try:
            session_id = get_fastmcp_session_id()
        except Exception as e:
            logger.debug(
                f"Could not retrieve FastMCP session ID for state binding: {e}"
            )

        store = get_oauth21_session_store()
        store.store_oauth_state(oauth_state, session_id=session_id)

        logger.info(
            f"Auth flow started for {user_display_name}. State: {oauth_state[:8]}... Advise user to visit: {auth_url}"
        )

        message_lines = [
            f"**ACTION REQUIRED: Google Authentication Needed for {user_display_name}**\n",
            f"To proceed, the user must authorize this application for {service_name} access using all required permissions.",
            "**LLM, please present this exact authorization URL to the user as a clickable hyperlink:**",
            f"Authorization URL: {auth_url}",
            f"Markdown for hyperlink: [Click here to authorize {service_name} access]({auth_url})\n",
            "**LLM, after presenting the link, instruct the user as follows:**",
            "1. Click the link and complete the authorization in their browser.",
        ]
        session_info_for_llm = ""

        if not initial_email_provided:
            message_lines.extend(
                [
                    f"2. After successful authorization{session_info_for_llm}, the browser page will display the authenticated email address.",
                    "   **LLM: Instruct the user to provide you with this email address.**",
                    "3. Once you have the email, **retry their original command, ensuring you include this `user_google_email`.**",
                ]
            )
        else:
            message_lines.append(
                f"2. After successful authorization{session_info_for_llm}, **retry their original command**."
            )

        message_lines.append(
            f"\nThe application will use the new credentials. If '{user_google_email}' was provided, it must match the authenticated account."
        )
        return "\n".join(message_lines)

    except FileNotFoundError as e:
        error_text = f"OAuth client credentials not found: {e}. Please either:\n1. Set environment variables: GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET\n2. Ensure '{CONFIG_CLIENT_SECRETS_PATH}' file exists"
        logger.error(error_text, exc_info=True)
        raise Exception(error_text)
    except Exception as e:
        error_text = f"Could not initiate authentication for {user_display_name} due to an unexpected error: {str(e)}"
        logger.error(
            f"Failed to start the OAuth flow for {user_display_name}: {e}",
            exc_info=True,
        )
        raise Exception(error_text)


def handle_auth_callback(
    scopes: List[str],
    authorization_response: str,
    redirect_uri: str,
    credentials_base_dir: str = DEFAULT_CREDENTIALS_DIR,
    session_id: Optional[str] = None,
    client_secrets_path: Optional[
        str
    ] = None,  # Deprecated: kept for backward compatibility
) -> Tuple[str, Credentials]:
    """
    Handles the callback from Google, exchanges the code for credentials,
    fetches user info, determines user_google_email, saves credentials (file & session),
    and returns them.

    Args:
        scopes: List of OAuth scopes requested.
        authorization_response: The full callback URL from Google.
        redirect_uri: The redirect URI.
        credentials_base_dir: Base directory for credential files.
        session_id: Optional MCP session ID to associate with the credentials.
        client_secrets_path: (Deprecated) Path to client secrets file. Ignored if environment variables are set.

    Returns:
        A tuple containing the user_google_email and the obtained Credentials object.

    Raises:
        ValueError: If the state is missing or doesn't match.
        FlowExchangeError: If the code exchange fails.
        HttpError: If fetching user info fails.
    """
    try:
        # Log deprecation warning if old parameter is used
        if client_secrets_path:
            logger.warning(
                "The 'client_secrets_path' parameter is deprecated. Use GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET environment variables instead."
            )

        # Allow HTTP for localhost in development
        if "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ:
            logger.warning(
                "OAUTHLIB_INSECURE_TRANSPORT not set. Setting it for localhost development."
            )
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

        store = get_oauth21_session_store()
        parsed_response = urlparse(authorization_response)
        state_values = parse_qs(parsed_response.query).get("state")
        state = state_values[0] if state_values else None

        state_info = store.validate_and_consume_oauth_state(
            state, session_id=session_id
        )
        logger.debug(
            "Validated OAuth callback state %s for session %s",
            (state[:8] if state else "<missing>"),
            state_info.get("session_id") or "<unknown>",
        )

        flow = create_oauth_flow(scopes=scopes, redirect_uri=redirect_uri, state=state)

        # Exchange the authorization code for credentials
        # Note: fetch_token will use the redirect_uri configured in the flow
        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials
        logger.info("Successfully exchanged authorization code for tokens.")

        # Get user info to determine user_id (using email here)
        user_info = get_user_info(credentials)
        if not user_info or "email" not in user_info:
            logger.error("Could not retrieve user email from Google.")
            raise ValueError("Failed to get user email for identification.")

        user_google_email = user_info["email"]
        logger.info(f"Identified user_google_email: {user_google_email}")

        # Save the credentials
        credential_store = get_credential_store()
        credential_store.store_credential(user_google_email, credentials)

        # Always save to OAuth21SessionStore for centralized management
        store = get_oauth21_session_store()
        store.store_session(
            user_email=user_google_email,
            access_token=credentials.token,
            refresh_token=credentials.refresh_token,
            token_uri=credentials.token_uri,
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
            scopes=credentials.scopes,
            expiry=credentials.expiry,
            mcp_session_id=session_id,
            issuer="https://accounts.google.com",  # Add issuer for Google tokens
        )

        # If session_id is provided, also save to session cache for compatibility
        if session_id:
            save_credentials_to_session(session_id, credentials)

        return user_google_email, credentials

    except Exception as e:  # Catch specific exceptions like FlowExchangeError if needed
        logger.error(f"Error handling auth callback: {e}")
        raise  # Re-raise for the caller


def get_credentials(
    user_google_email: Optional[str],  # Can be None if relying on session_id
    required_scopes: List[str],
    client_secrets_path: Optional[str] = None,
    credentials_base_dir: str = DEFAULT_CREDENTIALS_DIR,
    session_id: Optional[str] = None,
) -> Optional[Credentials]:
    """
    Retrieves stored credentials, prioritizing OAuth 2.1 store, then session, then file. Refreshes if necessary.
    If credentials are loaded from file and a session_id is present, they are cached in the session.
    In single-user mode, bypasses session mapping and uses any available credentials.

    Args:
        user_google_email: Optional user's Google email.
        required_scopes: List of scopes the credentials must have.
        client_secrets_path: Optional path to client secrets (legacy; refresh uses embedded client info).
        credentials_base_dir: Base directory for credential files.
        session_id: Optional MCP session ID.

    Returns:
        Valid Credentials object or None.
    """
    # First, try OAuth 2.1 session store if we have a session_id (FastMCP session)
    if session_id:
        try:
            store = get_oauth21_session_store()

            # Try to get credentials by MCP session
            credentials = store.get_credentials_by_mcp_session(session_id)
            if credentials:
                logger.info(
                    f"[get_credentials] Found OAuth 2.1 credentials for MCP session {session_id}"
                )

                # Check scopes
                if not all(scope in credentials.scopes for scope in required_scopes):
                    logger.warning(
                        f"[get_credentials] OAuth 2.1 credentials lack required scopes. Need: {required_scopes}, Have: {credentials.scopes}"
                    )
                    return None

                # Return if valid
                if credentials.valid:
                    return credentials
                elif credentials.expired and credentials.refresh_token:
                    # Try to refresh
                    try:
                        credentials.refresh(Request())
                        logger.info(
                            f"[get_credentials] Refreshed OAuth 2.1 credentials for session {session_id}"
                        )
                        # Update stored credentials
                        user_email = store.get_user_by_mcp_session(session_id)
                        if user_email:
                            store.store_session(
                                user_email=user_email,
                                access_token=credentials.token,
                                refresh_token=credentials.refresh_token,
                                scopes=credentials.scopes,
                                expiry=credentials.expiry,
                                mcp_session_id=session_id,
                            )
                        return credentials
                    except Exception as e:
                        logger.error(
                            f"[get_credentials] Failed to refresh OAuth 2.1 credentials: {e}"
                        )
                        return None
        except ImportError:
            pass  # OAuth 2.1 store not available
        except Exception as e:
            logger.debug(f"[get_credentials] Error checking OAuth 2.1 store: {e}")

    # Check for single-user mode
    if os.getenv("MCP_SINGLE_USER_MODE") == "1":
        logger.info(
            "[get_credentials] Single-user mode: bypassing session mapping, finding any credentials"
        )
        credentials = _find_any_credentials(credentials_base_dir)
        if not credentials:
            logger.info(
                f"[get_credentials] Single-user mode: No credentials found in {credentials_base_dir}"
            )
            return None

        # In single-user mode, if user_google_email wasn't provided, try to get it from user info
        # This is needed for proper credential saving after refresh
        if not user_google_email and credentials.valid:
            try:
                user_info = get_user_info(credentials)
                if user_info and "email" in user_info:
                    user_google_email = user_info["email"]
                    logger.debug(
                        f"[get_credentials] Single-user mode: extracted user email {user_google_email} from credentials"
                    )
            except Exception as e:
                logger.debug(
                    f"[get_credentials] Single-user mode: could not extract user email: {e}"
                )
    else:
        credentials: Optional[Credentials] = None

        # Session ID should be provided by the caller
        if not session_id:
            logger.debug("[get_credentials] No session_id provided")

        logger.debug(
            f"[get_credentials] Called for user_google_email: '{user_google_email}', session_id: '{session_id}', required_scopes: {required_scopes}"
        )

        if session_id:
            credentials = load_credentials_from_session(session_id)
            if credentials:
                logger.debug(
                    f"[get_credentials] Loaded credentials from session for session_id '{session_id}'."
                )

        if not credentials and user_google_email:
            if not is_stateless_mode():
                logger.debug(
                    f"[get_credentials] No session credentials, trying credential store for user_google_email '{user_google_email}'."
                )
                store = get_credential_store()
                credentials = store.get_credential(user_google_email)
            else:
                logger.debug(
                    f"[get_credentials] No session credentials, skipping file store in stateless mode for user_google_email '{user_google_email}'."
                )

            if credentials and session_id:
                logger.debug(
                    f"[get_credentials] Loaded from file for user '{user_google_email}', caching to session '{session_id}'."
                )
                save_credentials_to_session(
                    session_id, credentials
                )  # Cache for current session

        if not credentials:
            logger.info(
                f"[get_credentials] No credentials found for user '{user_google_email}' or session '{session_id}'."
            )
            return None

    logger.debug(
        f"[get_credentials] Credentials found. Scopes: {credentials.scopes}, Valid: {credentials.valid}, Expired: {credentials.expired}"
    )

    if not all(scope in credentials.scopes for scope in required_scopes):
        logger.warning(
            f"[get_credentials] Credentials lack required scopes. Need: {required_scopes}, Have: {credentials.scopes}. User: '{user_google_email}', Session: '{session_id}'"
        )
        return None  # Re-authentication needed for scopes

    logger.debug(
        f"[get_credentials] Credentials have sufficient scopes. User: '{user_google_email}', Session: '{session_id}'"
    )

    if credentials.valid:
        logger.debug(
            f"[get_credentials] Credentials are valid. User: '{user_google_email}', Session: '{session_id}'"
        )
        return credentials
    elif credentials.expired and credentials.refresh_token:
        logger.info(
            f"[get_credentials] Credentials expired. Attempting refresh. User: '{user_google_email}', Session: '{session_id}'"
        )
        try:
            logger.debug(
                "[get_credentials] Refreshing token using embedded client credentials"
            )
            # client_config = load_client_secrets(client_secrets_path) # Not strictly needed if creds have client_id/secret
            credentials.refresh(Request())
            logger.info(
                f"[get_credentials] Credentials refreshed successfully. User: '{user_google_email}', Session: '{session_id}'"
            )

            # Save refreshed credentials (skip file save in stateless mode)
            if user_google_email:  # Always save to credential store if email is known
                if not is_stateless_mode():
                    credential_store = get_credential_store()
                    credential_store.store_credential(user_google_email, credentials)
                else:
                    logger.info(
                        f"Skipping credential file save in stateless mode for {user_google_email}"
                    )

                # Also update OAuth21SessionStore
                store = get_oauth21_session_store()
                store.store_session(
                    user_email=user_google_email,
                    access_token=credentials.token,
                    refresh_token=credentials.refresh_token,
                    token_uri=credentials.token_uri,
                    client_id=credentials.client_id,
                    client_secret=credentials.client_secret,
                    scopes=credentials.scopes,
                    expiry=credentials.expiry,
                    mcp_session_id=session_id,
                    issuer="https://accounts.google.com",  # Add issuer for Google tokens
                )

            if session_id:  # Update session cache if it was the source or is active
                save_credentials_to_session(session_id, credentials)
            return credentials
        except RefreshError as e:
            logger.warning(
                f"[get_credentials] RefreshError - token expired/revoked: {e}. User: '{user_google_email}', Session: '{session_id}'"
            )
            # For RefreshError, we should return None to trigger reauthentication
            return None
        except Exception as e:
            logger.error(
                f"[get_credentials] Error refreshing credentials: {e}. User: '{user_google_email}', Session: '{session_id}'",
                exc_info=True,
            )
            return None  # Failed to refresh
    else:
        logger.warning(
            f"[get_credentials] Credentials invalid/cannot refresh. Valid: {credentials.valid}, Refresh Token: {credentials.refresh_token is not None}. User: '{user_google_email}', Session: '{session_id}'"
        )
        return None


def get_user_info(credentials: Credentials) -> Optional[Dict[str, Any]]:
    """Fetches basic user profile information (requires userinfo.email scope)."""
    if not credentials or not credentials.valid:
        logger.error("Cannot get user info: Invalid or missing credentials.")
        return None
    try:
        # Using googleapiclient discovery to get user info
        # Requires 'google-api-python-client' library
        service = build("oauth2", "v2", credentials=credentials)
        user_info = service.userinfo().get().execute()
        logger.info(f"Successfully fetched user info: {user_info.get('email')}")
        return user_info
    except HttpError as e:
        logger.error(f"HttpError fetching user info: {e.status_code} {e.reason}")
        # Handle specific errors, e.g., 401 Unauthorized might mean token issue
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching user info: {e}")
        return None


# --- Centralized Google Service Authentication ---


class GoogleAuthenticationError(Exception):
    """Exception raised when Google authentication is required or fails."""

    def __init__(self, message: str, auth_url: Optional[str] = None):
        super().__init__(message)
        self.auth_url = auth_url


async def get_authenticated_google_service(
    service_name: str,  # "gmail", "calendar", "drive", "docs"
    version: str,  # "v1", "v3"
    tool_name: str,  # For logging/debugging
    user_google_email: str,  # Required - no more Optional
    required_scopes: List[str],
    session_id: Optional[str] = None,  # Session context for logging
) -> tuple[Any, str]:
    """
    Centralized Google service authentication for all MCP tools.
    Returns (service, user_email) on success or raises GoogleAuthenticationError.

    Args:
        service_name: The Google service name ("gmail", "calendar", "drive", "docs")
        version: The API version ("v1", "v3", etc.)
        tool_name: The name of the calling tool (for logging/debugging)
        user_google_email: The user's Google email address (required)
        required_scopes: List of required OAuth scopes

    Returns:
        tuple[service, user_email] on success

    Raises:
        GoogleAuthenticationError: When authentication is required or fails
    """

    # Try to get FastMCP session ID if not provided
    if not session_id:
        try:
            # First try context variable (works in async context)
            session_id = get_fastmcp_session_id()
            if session_id:
                logger.debug(
                    f"[{tool_name}] Got FastMCP session ID from context: {session_id}"
                )
            else:
                logger.debug(
                    f"[{tool_name}] Context variable returned None/empty session ID"
                )
        except Exception as e:
            logger.debug(
                f"[{tool_name}] Could not get FastMCP session from context: {e}"
            )

        # Fallback to direct FastMCP context if context variable not set
        if not session_id and get_fastmcp_context:
            try:
                fastmcp_ctx = get_fastmcp_context()
                if fastmcp_ctx and hasattr(fastmcp_ctx, "session_id"):
                    session_id = fastmcp_ctx.session_id
                    logger.debug(
                        f"[{tool_name}] Got FastMCP session ID directly: {session_id}"
                    )
                else:
                    logger.debug(
                        f"[{tool_name}] FastMCP context exists but no session_id attribute"
                    )
            except Exception as e:
                logger.debug(
                    f"[{tool_name}] Could not get FastMCP context directly: {e}"
                )

        # Final fallback: log if we still don't have session_id
        if not session_id:
            logger.warning(
                f"[{tool_name}] Unable to obtain FastMCP session ID from any source"
            )

    logger.info(
        f"[{tool_name}] Attempting to get authenticated {service_name} service. Email: '{user_google_email}', Session: '{session_id}'"
    )

    # Validate email format
    if not user_google_email or "@" not in user_google_email:
        error_msg = f"Authentication required for {tool_name}. No valid 'user_google_email' provided. Please provide a valid Google email address."
        logger.info(f"[{tool_name}] {error_msg}")
        raise GoogleAuthenticationError(error_msg)

    credentials = await asyncio.to_thread(
        get_credentials,
        user_google_email=user_google_email,
        required_scopes=required_scopes,
        client_secrets_path=CONFIG_CLIENT_SECRETS_PATH,
        session_id=session_id,  # Pass through session context
    )

    if not credentials or not credentials.valid:
        logger.warning(
            f"[{tool_name}] No valid credentials. Email: '{user_google_email}'."
        )
        logger.info(
            f"[{tool_name}] Valid email '{user_google_email}' provided, initiating auth flow."
        )

        # Ensure OAuth callback is available
        from auth.oauth_callback_server import ensure_oauth_callback_available

        redirect_uri = get_oauth_redirect_uri()
        config = get_oauth_config()
        success, error_msg = ensure_oauth_callback_available(
            get_transport_mode(), config.port, config.base_uri
        )
        if not success:
            error_detail = f" ({error_msg})" if error_msg else ""
            raise GoogleAuthenticationError(
                f"Cannot initiate OAuth flow - callback server unavailable{error_detail}"
            )

        # Generate auth URL and raise exception with it
        auth_response = await start_auth_flow(
            user_google_email=user_google_email,
            service_name=f"Google {service_name.title()}",
            redirect_uri=redirect_uri,
        )

        # Extract the auth URL from the response and raise with it
        raise GoogleAuthenticationError(auth_response)

    try:
        service = build(service_name, version, credentials=credentials)
        log_user_email = user_google_email

        # Try to get email from credentials if needed for validation
        if credentials and credentials.id_token:
            try:
                # Decode without verification (just to get email for logging)
                decoded_token = jwt.decode(
                    credentials.id_token, options={"verify_signature": False}
                )
                token_email = decoded_token.get("email")
                if token_email:
                    log_user_email = token_email
                    logger.info(f"[{tool_name}] Token email: {token_email}")
            except Exception as e:
                logger.debug(f"[{tool_name}] Could not decode id_token: {e}")

        logger.info(
            f"[{tool_name}] Successfully authenticated {service_name} service for user: {log_user_email}"
        )
        return service, log_user_email

    except Exception as e:
        error_msg = f"[{tool_name}] Failed to build {service_name} service: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise GoogleAuthenticationError(error_msg)
