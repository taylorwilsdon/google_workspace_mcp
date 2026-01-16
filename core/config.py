"""
Shared configuration for Google Workspace MCP server.
This module holds configuration values that need to be shared across modules
to avoid circular imports.

NOTE: OAuth configuration has been moved to auth.oauth_config for centralization.
This module now imports from there for backward compatibility.
"""

import logging
import os
import threading
from pathlib import Path
from typing import List, Optional

from auth.oauth_config import (
    get_oauth_base_url,
    get_oauth_redirect_uri,
    set_transport_mode,
    get_transport_mode,
    is_oauth21_enabled,
)

logger = logging.getLogger(__name__)

# Server configuration
WORKSPACE_MCP_PORT = int(os.getenv("PORT", os.getenv("WORKSPACE_MCP_PORT", 8000)))
WORKSPACE_MCP_BASE_URI = os.getenv("WORKSPACE_MCP_BASE_URI", "http://localhost")

# Disable USER_GOOGLE_EMAIL in OAuth 2.1 multi-user mode
# This env var is now optional - email can be auto-detected from credentials
USER_GOOGLE_EMAIL = (
    None if is_oauth21_enabled() else os.getenv("USER_GOOGLE_EMAIL", None)
)

# --- Auto-detection of user email from credentials ---
# Thread-safe lazy detection with caching

_detected_email: Optional[str] = None
_email_detection_attempted: bool = False
_email_lock = threading.Lock()


def _get_credentials_dir() -> Path:
    """Get the credentials directory path."""
    if os.getenv("GOOGLE_MCP_CREDENTIALS_DIR"):
        return Path(os.getenv("GOOGLE_MCP_CREDENTIALS_DIR"))

    home_dir = Path.home()
    if home_dir and str(home_dir) != "~":
        return home_dir / ".google_workspace_mcp" / "credentials"

    return Path.cwd() / ".credentials"


def list_authenticated_emails() -> List[str]:
    """
    List all authenticated user emails from credential files.

    This uses fast filename-based detection (parsing filenames like 'user@example.com.json')
    rather than loading and parsing the JSON files, which is more efficient.

    Returns:
        List of email addresses that have stored credentials, sorted alphabetically.
    """
    creds_dir = _get_credentials_dir()

    if not creds_dir.exists():
        logger.debug(f"Credentials directory does not exist: {creds_dir}")
        return []

    emails = []
    try:
        for f in creds_dir.glob("*.json"):
            # Extract email from filename (e.g., 'user@example.com.json' -> 'user@example.com')
            email = f.stem
            # Basic validation - must contain @ to be an email
            if "@" in email and email not in ("service_account", "client_secret"):
                emails.append(email)

        logger.debug(f"Found {len(emails)} authenticated emails in {creds_dir}")
    except OSError as e:
        logger.error(f"Error listing credential files in {creds_dir}: {e}")

    return sorted(emails)


def _detect_single_user_email() -> Optional[str]:
    """
    Internal function to detect user email in single-user mode.

    Detection priority:
    1. USER_GOOGLE_EMAIL environment variable (explicit configuration)
    2. Single credential file in credentials directory
    3. If multiple credentials exist, return None (ambiguous - user must specify)

    Returns:
        Detected email address or None if detection fails or is ambiguous.
    """
    # Priority 1: Explicit environment variable
    env_email = os.getenv("USER_GOOGLE_EMAIL")
    if env_email and "@" in env_email:
        logger.debug(f"Using USER_GOOGLE_EMAIL from environment: {env_email}")
        return env_email

    # Priority 2: Auto-detect from credentials
    emails = list_authenticated_emails()

    if not emails:
        logger.debug("No credential files found for auto-detection")
        return None

    if len(emails) == 1:
        detected = emails[0]
        logger.info(f"Auto-detected user email from credentials: {detected}")
        return detected

    # Multiple credentials - ambiguous, cannot auto-detect
    logger.warning(
        f"Multiple credential files found ({len(emails)}), cannot auto-detect email. "
        f"Available: {', '.join(emails)}. "
        "Please specify user_google_email parameter or set USER_GOOGLE_EMAIL environment variable."
    )
    return None


def get_auto_detected_email() -> Optional[str]:
    """
    Get the auto-detected user email with thread-safe lazy initialization.

    This function caches the result of email detection so subsequent calls
    are fast. The detection is only performed once per process.

    In single-user mode (--single-user flag), this will attempt to auto-detect
    the user's email from stored credentials if USER_GOOGLE_EMAIL is not set.

    Returns:
        The auto-detected email address, or None if:
        - No credentials exist
        - Multiple credentials exist (ambiguous)
        - OAuth 2.1 mode is enabled (multi-user mode)
    """
    global _detected_email, _email_detection_attempted

    # Fast path - already detected
    if _email_detection_attempted:
        return _detected_email

    with _email_lock:
        # Double-check after acquiring lock
        if _email_detection_attempted:
            return _detected_email

        # OAuth 2.1 mode doesn't use auto-detection (multi-user mode)
        if is_oauth21_enabled():
            logger.debug("OAuth 2.1 mode enabled, skipping email auto-detection")
            _email_detection_attempted = True
            return None

        # Perform detection
        _detected_email = _detect_single_user_email()
        _email_detection_attempted = True

        return _detected_email


def clear_auto_detected_email_cache():
    """
    Clear the auto-detected email cache.

    This is useful for testing or when credentials change.
    """
    global _detected_email, _email_detection_attempted
    with _email_lock:
        _detected_email = None
        _email_detection_attempted = False
        logger.debug("Cleared auto-detected email cache")


# Re-export OAuth functions for backward compatibility
__all__ = [
    "WORKSPACE_MCP_PORT",
    "WORKSPACE_MCP_BASE_URI",
    "USER_GOOGLE_EMAIL",
    "get_oauth_base_url",
    "get_oauth_redirect_uri",
    "set_transport_mode",
    "get_transport_mode",
    "list_authenticated_emails",
    "get_auto_detected_email",
    "clear_auto_detected_email_cache",
]
