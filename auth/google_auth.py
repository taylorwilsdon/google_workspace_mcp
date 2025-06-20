# auth/google_auth.py

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import jwt
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow, InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from auth.scopes import OAUTH_STATE_TO_SESSION_ID_MAP, SCOPES

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
DEFAULT_CREDENTIALS_DIR = ".credentials"

# In-memory cache for session credentials, maps session_id to Credentials object
# This is brittle and bad, but our options are limited with Claude in present state.
# This should be more robust in a production system once OAuth2.1 is implemented in client.
_SESSION_CREDENTIALS_CACHE: Dict[str, Credentials] = {}
# Centralized Client Secrets Path Logic
_client_secrets_env = os.getenv("GOOGLE_CLIENT_SECRETS")
if _client_secrets_env:
    CONFIG_CLIENT_SECRETS_PATH = _client_secrets_env
else:
    # Assumes this file is in auth/ and client_secret.json is in the root
    CONFIG_CLIENT_SECRETS_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'client_secret.json'
    )

# --- Helper Functions ---

def _find_any_credentials(base_dir: str = DEFAULT_CREDENTIALS_DIR) -> Optional[Credentials]:
    """
    Find and load any valid credentials from the credentials directory.
    Used in single-user mode to bypass session-to-OAuth mapping.
    Supports both OAuth2 and service account credentials.

    Returns:
        First valid Credentials object found, or None if none exist.
    """
    if not os.path.exists(base_dir):
        logger.info(f"[single-user] Credentials directory not found: {base_dir}")
        return None

    # Scan for any .json credential files
    for filename in os.listdir(base_dir):
        if filename.endswith('.json'):
            filepath = os.path.join(base_dir, filename)
            try:
                # Check if this is a service account file
                if "iam.gserviceaccount.com" in filename:
                    logger.info(f"[single-user] Found service account file: {filepath}")
                    try:
                        credentials = service_account.Credentials.from_service_account_file(filepath,scopes=SCOPES)
                        logger.info(f"[single-user] Successfully loaded service account credentials from {filepath}")

                        # Force refresh to get a token, since by default it's not set and the library considers then credentials to be invalid
                        credentials.refresh(Request())
                        return credentials
                    except Exception as e:
                        logger.warning(f"[single-user] Error loading service account credentials from {filepath}: {e}")
                        continue

                # Handle OAuth2 credentials
                with open(filepath, 'r') as f:
                    creds_data = json.load(f)

                credentials = Credentials(
                    token=creds_data.get('token'),
                    refresh_token=creds_data.get('refresh_token'),
                    token_uri=creds_data.get('token_uri'),
                    client_id=creds_data.get('client_id'),
                    client_secret=creds_data.get('client_secret'),
                    scopes=creds_data.get('scopes')
                )
                logger.info(f"[single-user] Found OAuth2 credentials in {filepath}")
                return credentials
            except (IOError, json.JSONDecodeError, KeyError) as e:
                logger.warning(f"[single-user] Error loading credentials from {filepath}: {e}")
                continue

    logger.info(f"[single-user] No valid credentials found in {base_dir}")
    return None

def _get_user_credential_path(user_google_email: str, base_dir: str = DEFAULT_CREDENTIALS_DIR) -> str:
    """Constructs the path to a user's credential file."""
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)
        logger.info(f"Created credentials directory: {base_dir}")
    return os.path.join(base_dir, f"{user_google_email}.json")

def save_credentials_to_file(user_google_email: str, credentials: Credentials, base_dir: str = DEFAULT_CREDENTIALS_DIR):
    """Saves user credentials to a file."""
    creds_path = _get_user_credential_path(user_google_email, base_dir)
    creds_data = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes,
        'expiry': credentials.expiry.isoformat() if credentials.expiry else None
    }
    try:
        with open(creds_path, 'w') as f:
            json.dump(creds_data, f)
        logger.info(f"Credentials saved for user {user_google_email} to {creds_path}")
    except IOError as e:
        logger.error(f"Error saving credentials for user {user_google_email} to {creds_path}: {e}")
        raise

def save_credentials_to_session(session_id: str, credentials: Credentials):
    """Saves user credentials to the in-memory session cache."""
    _SESSION_CREDENTIALS_CACHE[session_id] = credentials
    logger.debug(f"Credentials saved to session cache for session_id: {session_id}")

def load_credentials_from_file(user_google_email: str, base_dir: str = DEFAULT_CREDENTIALS_DIR) -> Optional[Credentials]:
    """Loads user credentials from a file."""
    creds_path = _get_user_credential_path(user_google_email, base_dir)
    if not os.path.exists(creds_path):
        logger.info(f"No credentials file found for user {user_google_email} at {creds_path}")
        return None

    try:
        with open(creds_path, 'r') as f:
            creds_data = json.load(f)

        # Parse expiry if present
        expiry = None
        if creds_data.get('expiry'):
            try:
                expiry = datetime.fromisoformat(creds_data['expiry'])
            except (ValueError, TypeError) as e:
                logger.warning(f"Could not parse expiry time for {user_google_email}: {e}")

        credentials = Credentials(
            token=creds_data.get('token'),
            refresh_token=creds_data.get('refresh_token'),
            token_uri=creds_data.get('token_uri'),
            client_id=creds_data.get('client_id'),
            client_secret=creds_data.get('client_secret'),
            scopes=creds_data.get('scopes'),
            expiry=expiry
        )
        logger.debug(f"Credentials loaded for user {user_google_email} from {creds_path}")
        return credentials
    except (IOError, json.JSONDecodeError, KeyError) as e:
        logger.error(f"Error loading or parsing credentials for user {user_google_email} from {creds_path}: {e}")
        return None

def load_credentials_from_session(session_id: str) -> Optional[Credentials]:
    """Loads user credentials from the in-memory session cache."""
    credentials = _SESSION_CREDENTIALS_CACHE.get(session_id)
    if credentials:
        logger.debug(f"Credentials loaded from session cache for session_id: {session_id}")
    else:
        logger.debug(f"No credentials found in session cache for session_id: {session_id}")
    return credentials

def load_client_secrets(client_secrets_path: str) -> Dict[str, Any]:
    """Loads the client secrets file."""
    try:
        with open(client_secrets_path, 'r') as f:
            client_config = json.load(f)
            # The file usually contains a top-level key like "web" or "installed"
            if "web" in client_config:
                return client_config["web"]
            elif "installed" in client_config:
                 return client_config["installed"]
            else:
                 logger.error(f"Client secrets file {client_secrets_path} has unexpected format.")
                 raise ValueError("Invalid client secrets file format")
    except (IOError, json.JSONDecodeError) as e:
        logger.error(f"Error loading client secrets file {client_secrets_path}: {e}")
        raise

# --- Core OAuth Logic ---

async def start_auth_flow(
    mcp_session_id: Optional[str],
    user_google_email: Optional[str],
    service_name: str, # e.g., "Google Calendar", "Gmail" for user messages
    redirect_uri: str, # Added redirect_uri as a required parameter
) -> str:
    """
    Initiates the Google OAuth flow and returns an actionable message for the user.

    Args:
        mcp_session_id: The active MCP session ID.
        user_google_email: The user's specified Google email, if provided.
        service_name: The name of the Google service requiring auth (for user messages).
        redirect_uri: The URI Google will redirect to after authorization.

    Returns:
        A formatted string containing guidance for the LLM/user.

    Raises:
        Exception: If the OAuth flow cannot be initiated.
    """
    initial_email_provided = bool(user_google_email and user_google_email.strip() and user_google_email.lower() != 'default')
    user_display_name = f"{service_name} for '{user_google_email}'" if initial_email_provided else service_name

    logger.info(f"[start_auth_flow] Initiating auth for {user_display_name} (session: {mcp_session_id}) with global SCOPES.")

    try:
        if 'OAUTHLIB_INSECURE_TRANSPORT' not in os.environ and ("localhost" in redirect_uri or "127.0.0.1" in redirect_uri): # Use passed redirect_uri
            logger.warning("OAUTHLIB_INSECURE_TRANSPORT not set. Setting it for localhost/local development.")
            os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

        oauth_state = os.urandom(16).hex()
        if mcp_session_id:
            OAUTH_STATE_TO_SESSION_ID_MAP[oauth_state] = mcp_session_id
            logger.info(f"[start_auth_flow] Stored mcp_session_id '{mcp_session_id}' for oauth_state '{oauth_state}'.")

        flow = Flow.from_client_secrets_file(
            CONFIG_CLIENT_SECRETS_PATH, # Use module constant
            scopes=SCOPES, # Use global SCOPES
            redirect_uri=redirect_uri, # Use passed redirect_uri
            state=oauth_state
        )

        auth_url, _ = flow.authorization_url(access_type='offline', prompt='consent')
        logger.info(f"Auth flow started for {user_display_name}. State: {oauth_state}. Advise user to visit: {auth_url}")

        message_lines = [
            f"**ACTION REQUIRED: Google Authentication Needed for {user_display_name}**\n",
            f"To proceed, the user must authorize this application for {service_name} access using all required permissions.",
            "**LLM, please present this exact authorization URL to the user as a clickable hyperlink:**",
            f"Authorization URL: {auth_url}",
            f"Markdown for hyperlink: [Click here to authorize {service_name} access]({auth_url})\n",
            "**LLM, after presenting the link, instruct the user as follows:**",
            "1. Click the link and complete the authorization in their browser.",
        ]
        session_info_for_llm = f" (this will link to your current session {mcp_session_id})" if mcp_session_id else ""

        if not initial_email_provided:
            message_lines.extend([
                f"2. After successful authorization{session_info_for_llm}, the browser page will display the authenticated email address.",
                "   **LLM: Instruct the user to provide you with this email address.**",
                "3. Once you have the email, **retry their original command, ensuring you include this `user_google_email`.**"
            ])
        else:
            message_lines.append(f"2. After successful authorization{session_info_for_llm}, **retry their original command**.")

        message_lines.append(f"\nThe application will use the new credentials. If '{user_google_email}' was provided, it must match the authenticated account.")
        return "\n".join(message_lines)

    except FileNotFoundError as e:
        error_text = f"OAuth client secrets file not found: {e}. Please ensure '{CONFIG_CLIENT_SECRETS_PATH}' is correctly configured."
        logger.error(error_text, exc_info=True)
        raise Exception(error_text)
    except Exception as e:
        error_text = f"Could not initiate authentication for {user_display_name} due to an unexpected error: {str(e)}"
        logger.error(f"Failed to start the OAuth flow for {user_display_name}: {e}", exc_info=True)
        raise Exception(error_text)

def handle_auth_callback(
    client_secrets_path: str,
    scopes: List[str],
    authorization_response: str,
    redirect_uri: str, # Made redirect_uri a required parameter
    credentials_base_dir: str = DEFAULT_CREDENTIALS_DIR,
    session_id: Optional[str] = None
) -> Tuple[str, Credentials]:
    """
    Handles the callback from Google, exchanges the code for credentials,
    fetches user info, determines user_google_email, saves credentials (file & session),
    and returns them.

    Args:
        client_secrets_path: Path to the Google client secrets JSON file.
        scopes: List of OAuth scopes requested.
        authorization_response: The full callback URL from Google.
        redirect_uri: The redirect URI.
        credentials_base_dir: Base directory for credential files.
        session_id: Optional MCP session ID to associate with the credentials.

    Returns:
        A tuple containing the user_google_email and the obtained Credentials object.

    Raises:
        ValueError: If the state is missing or doesn't match.
        FlowExchangeError: If the code exchange fails.
        HttpError: If fetching user info fails.
    """
    try:
        # Allow HTTP for localhost in development
        if 'OAUTHLIB_INSECURE_TRANSPORT' not in os.environ:
            logger.warning("OAUTHLIB_INSECURE_TRANSPORT not set. Setting it for localhost development.")
            os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

        flow = Flow.from_client_secrets_file(
            client_secrets_path,
            scopes=scopes,
            redirect_uri=redirect_uri
        )

        # Exchange the authorization code for credentials
        # Note: fetch_token will use the redirect_uri configured in the flow
        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials
        logger.info("Successfully exchanged authorization code for tokens.")

        # Get user info to determine user_id (using email here)
        user_info = get_user_info(credentials)
        if not user_info or 'email' not in user_info:
             logger.error("Could not retrieve user email from Google.")
             raise ValueError("Failed to get user email for identification.")

        user_google_email = user_info['email']
        logger.info(f"Identified user_google_email: {user_google_email}")

        # Save the credentials to file
        save_credentials_to_file(user_google_email, credentials, credentials_base_dir)

        # If session_id is provided, also save to session cache
        if session_id:
            save_credentials_to_session(session_id, credentials)

        return user_google_email, credentials

    except Exception as e: # Catch specific exceptions like FlowExchangeError if needed
        logger.error(f"Error handling auth callback: {e}")
        raise # Re-raise for the caller

def get_credentials(
    user_google_email: Optional[str], # Can be None if relying on session_id
    required_scopes: List[str],
    client_secrets_path: Optional[str] = None,
    credentials_base_dir: str = DEFAULT_CREDENTIALS_DIR,
    session_id: Optional[str] = None
) -> Optional[Credentials]:
    """
    Retrieves stored credentials, prioritizing session, then file. Refreshes if necessary.
    If credentials are loaded from file and a session_id is present, they are cached in the session.
    In single-user mode, bypasses session mapping and uses any available credentials.

    Args:
        user_google_email: Optional user's Google email.
        required_scopes: List of scopes the credentials must have.
        client_secrets_path: Path to client secrets, required for refresh if not in creds.
        credentials_base_dir: Base directory for credential files.
        session_id: Optional MCP session ID.

    Returns:
        Valid Credentials object or None.
    """
    # Check for single-user mode
    if os.getenv('MCP_SINGLE_USER_MODE') == '1':
        logger.info(f"[get_credentials] Single-user mode: bypassing session mapping, finding any credentials")
        credentials = _find_any_credentials(credentials_base_dir)
        if not credentials:
            logger.info(f"[get_credentials] Single-user mode: No credentials found in {credentials_base_dir}")
            return None

        # In single-user mode, if user_google_email wasn't provided, try to get it from user info
        # This is needed for proper credential saving after refresh
        if not user_google_email and credentials.valid:
            try:
                user_info = get_user_info(credentials)
                if user_info and 'email' in user_info:
                    user_google_email = user_info['email']
                    logger.debug(f"[get_credentials] Single-user mode: extracted user email {user_google_email} from credentials")
            except Exception as e:
                logger.debug(f"[get_credentials] Single-user mode: could not extract user email: {e}")
    else:
        credentials: Optional[Credentials] = None

        # Session ID should be provided by the caller
        if not session_id:
            logger.debug("[get_credentials] No session_id provided")

        logger.debug(f"[get_credentials] Called for user_google_email: '{user_google_email}', session_id: '{session_id}', required_scopes: {required_scopes}")

        if session_id:
            credentials = load_credentials_from_session(session_id)
            if credentials:
                logger.debug(f"[get_credentials] Loaded credentials from session for session_id '{session_id}'.")

        if not credentials and user_google_email:
            logger.debug(f"[get_credentials] No session credentials, trying file for user_google_email '{user_google_email}'.")
            credentials = load_credentials_from_file(user_google_email, credentials_base_dir)
            if credentials and session_id:
                logger.debug(f"[get_credentials] Loaded from file for user '{user_google_email}', caching to session '{session_id}'.")
                save_credentials_to_session(session_id, credentials) # Cache for current session

        if not credentials:
            logger.info(f"[get_credentials] No credentials found for user '{user_google_email}' or session '{session_id}'.")
            return None

    logger.debug(f"[get_credentials] Credentials found. Scopes: {credentials.scopes}, Valid: {credentials.valid}, Expired: {credentials.expired}")

    if not all(scope in credentials.scopes for scope in required_scopes):
        logger.warning(f"[get_credentials] Credentials lack required scopes. Need: {required_scopes}, Have: {credentials.scopes}. User: '{user_google_email}', Session: '{session_id}'")
        return None # Re-authentication needed for scopes

    logger.debug(f"[get_credentials] Credentials have sufficient scopes. User: '{user_google_email}', Session: '{session_id}'")

    if credentials.valid:
        logger.debug(f"[get_credentials] Credentials are valid. User: '{user_google_email}', Session: '{session_id}'")
        return credentials
    elif credentials.expired and credentials.refresh_token:
        logger.info(f"[get_credentials] Credentials expired. Attempting refresh. User: '{user_google_email}', Session: '{session_id}'")
        if not client_secrets_path:
             logger.error("[get_credentials] Client secrets path required for refresh but not provided.")
             return None
        try:
            logger.debug(f"[get_credentials] Refreshing token using client_secrets_path: {client_secrets_path}")
            # client_config = load_client_secrets(client_secrets_path) # Not strictly needed if creds have client_id/secret
            credentials.refresh(Request())
            logger.info(f"[get_credentials] Credentials refreshed successfully. User: '{user_google_email}', Session: '{session_id}'")

            # Save refreshed credentials
            if user_google_email: # Always save to file if email is known
                save_credentials_to_file(user_google_email, credentials, credentials_base_dir)
            if session_id: # Update session cache if it was the source or is active
                save_credentials_to_session(session_id, credentials)
            return credentials
        except RefreshError as e:
            logger.warning(f"[get_credentials] RefreshError - token expired/revoked: {e}. User: '{user_google_email}', Session: '{session_id}'")
            # For RefreshError, we should return None to trigger reauthentication
            return None
        except Exception as e:
            logger.error(f"[get_credentials] Error refreshing credentials: {e}. User: '{user_google_email}', Session: '{session_id}'", exc_info=True)
            return None # Failed to refresh
    else:
        logger.warning(f"[get_credentials] Credentials invalid/cannot refresh. Valid: {credentials.valid}, Refresh Token: {credentials.refresh_token is not None}. User: '{user_google_email}', Session: '{session_id}'")
        return None


def get_user_info(credentials: Credentials) -> Optional[Dict[str, Any]]:
    """Fetches basic user profile information (requires userinfo.email scope)."""
    if not credentials or not credentials.valid:
        logger.error("Cannot get user info: Invalid or missing credentials.")
        return None
    try:
        # Using googleapiclient discovery to get user info
        # Requires 'google-api-python-client' library
        service = build('oauth2', 'v2', credentials=credentials)
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
    service_name: str,      # "gmail", "calendar", "drive", "docs"
    version: str,           # "v1", "v3"
    tool_name: str,         # For logging/debugging
    user_google_email: str, # Required - no more Optional
    required_scopes: List[str],
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
    logger.info(
        f"[{tool_name}] Attempting to get authenticated {service_name} service. Email: '{user_google_email}'"
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
        session_id=None,  # Session ID not available in service layer
    )

    if not credentials or not credentials.valid:
        logger.warning(
            f"[{tool_name}] No valid credentials. Email: '{user_google_email}'."
        )
        logger.info(
            f"[{tool_name}] Valid email '{user_google_email}' provided, initiating auth flow."
        )

        # Import here to avoid circular import
        from core.server import get_oauth_redirect_uri_for_current_mode

        # Ensure OAuth callback is available
        redirect_uri = get_oauth_redirect_uri_for_current_mode()
        # Note: We don't know the transport mode here, but the server should have set it

        # Generate auth URL and raise exception with it
        auth_response = await start_auth_flow(
            mcp_session_id=None,  # Session ID not available in service layer
            user_google_email=user_google_email,
            service_name=f"Google {service_name.title()}",
            redirect_uri=redirect_uri,
        )

        # Extract the auth URL from the response and raise with it
        raise GoogleAuthenticationError(auth_response)

    try:
        service = build(service_name, version, credentials=credentials)
        log_user_email = user_google_email

        # For service accounts, use the service account email
        if hasattr(credentials, 'service_account_email'):
            log_user_email = credentials.service_account_email
            logger.info(f"[{tool_name}] Using service account: {log_user_email}")
        # For OAuth2 credentials, try to get email from id_token
        elif credentials and hasattr(credentials, 'id_token') and credentials.id_token:
            try:
                # Decode without verification (just to get email for logging)
                decoded_token = jwt.decode(credentials.id_token, options={"verify_signature": False})
                token_email = decoded_token.get("email")
                if token_email:
                    log_user_email = token_email
                    logger.info(f"[{tool_name}] Token email: {token_email}")
            except Exception as e:
                logger.debug(f"[{tool_name}] Could not decode id_token: {e}")

        logger.info(f"[{tool_name}] Successfully authenticated {service_name} service for user: {log_user_email}")
        return service, log_user_email

    except Exception as e:
        error_msg = f"[{tool_name}] Failed to build {service_name} service: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise GoogleAuthenticationError(error_msg)


