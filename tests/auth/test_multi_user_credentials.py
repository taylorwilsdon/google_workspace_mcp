"""Tests for multi-user credential resolution in get_credentials().

When multiple Google accounts are configured, get_credentials() must return
credentials for the requested user_google_email, not whichever account
happened to authenticate first in the current session.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

USER_A = "alice@example.com"
USER_B = "bob@example.com"

SESSION_ID = "test-session-123"


def _make_credentials(email: str, valid: bool = True) -> MagicMock:
    """Create a mock Credentials object for the given email."""
    creds = MagicMock(spec=Credentials)
    creds.valid = valid
    creds.expired = not valid
    creds.refresh_token = "refresh-token"
    creds.token = f"token-for-{email}"
    creds.scopes = set(SCOPES)
    creds.expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    return creds


class TestPhase1OAuthSessionStoreEmailValidation:
    """Phase 1: OAuth 2.1 session store should skip credentials bound to a different user."""

    @patch("auth.google_auth.get_oauth21_session_store")
    def test_skips_session_credentials_when_email_does_not_match(self, mock_get_store):
        """When session is bound to user A but credentials are requested for user B,
        Phase 1 should skip and fall through."""
        from auth.google_auth import get_credentials

        store = MagicMock()
        mock_get_store.return_value = store

        creds_a = _make_credentials(USER_A)
        store.get_credentials_by_mcp_session.return_value = creds_a
        store.get_user_by_mcp_session.return_value = USER_A

        # Patch the file-based credential store to return user B's credentials
        with patch("auth.google_auth.get_credential_store") as mock_file_store, \
             patch("auth.google_auth.is_stateless_mode", return_value=False):
            file_store = MagicMock()
            mock_file_store.return_value = file_store
            creds_b = _make_credentials(USER_B)
            file_store.get_credential.return_value = creds_b

            result = get_credentials(
                user_google_email=USER_B,
                required_scopes=SCOPES,
                session_id=SESSION_ID,
            )

        # Should get user B's credentials from file store, not user A's from session
        assert result is not None
        assert result.token == f"token-for-{USER_B}"
        file_store.get_credential.assert_called_once_with(USER_B)

    @patch("auth.google_auth.get_oauth21_session_store")
    def test_uses_session_credentials_when_email_matches(self, mock_get_store):
        """When session is bound to user A and credentials are requested for user A,
        Phase 1 should return session credentials."""
        from auth.google_auth import get_credentials

        store = MagicMock()
        mock_get_store.return_value = store

        creds_a = _make_credentials(USER_A)
        store.get_credentials_by_mcp_session.return_value = creds_a
        store.get_user_by_mcp_session.return_value = USER_A

        result = get_credentials(
            user_google_email=USER_A,
            required_scopes=SCOPES,
            session_id=SESSION_ID,
        )

        # Should return user A's session credentials directly
        assert result is not None
        assert result.token == f"token-for-{USER_A}"

    @patch("auth.google_auth.get_oauth21_session_store")
    def test_uses_session_credentials_when_no_email_specified(self, mock_get_store):
        """When no user_google_email is specified, session credentials should be used
        regardless of which user they belong to."""
        from auth.google_auth import get_credentials

        store = MagicMock()
        mock_get_store.return_value = store

        creds_a = _make_credentials(USER_A)
        store.get_credentials_by_mcp_session.return_value = creds_a
        store.get_user_by_mcp_session.return_value = USER_A

        result = get_credentials(
            user_google_email=None,
            required_scopes=SCOPES,
            session_id=SESSION_ID,
        )

        assert result is not None
        assert result.token == f"token-for-{USER_A}"


class TestPhase3aSessionFallbackEmailValidation:
    """Phase 3a: load_credentials_from_session should skip credentials bound to a different user."""

    @patch("auth.google_auth.get_oauth21_session_store")
    @patch("auth.google_auth.load_credentials_from_session")
    def test_skips_session_credentials_when_email_does_not_match(
        self, mock_load_session, mock_get_store
    ):
        """When Phase 1 doesn't apply (e.g. no OAuth 2.1 store) but session has user A's
        credentials, requesting user B should fall through to file store."""
        from auth.google_auth import get_credentials

        # Phase 1: OAuth 2.1 store returns nothing
        store = MagicMock()
        mock_get_store.return_value = store
        store.get_credentials_by_mcp_session.return_value = None
        store.get_user_by_mcp_session.return_value = USER_A

        # Phase 3a: session has user A's credentials
        creds_a = _make_credentials(USER_A)
        mock_load_session.return_value = creds_a

        with patch("auth.google_auth.get_credential_store") as mock_file_store, \
             patch("auth.google_auth.is_stateless_mode", return_value=False), \
             patch.dict("os.environ", {"MCP_SINGLE_USER_MODE": "0"}, clear=False):
            file_store = MagicMock()
            mock_file_store.return_value = file_store
            creds_b = _make_credentials(USER_B)
            file_store.get_credential.return_value = creds_b

            result = get_credentials(
                user_google_email=USER_B,
                required_scopes=SCOPES,
                session_id=SESSION_ID,
            )

        assert result is not None
        assert result.token == f"token-for-{USER_B}"
        file_store.get_credential.assert_called_once_with(USER_B)

    @patch("auth.google_auth.get_oauth21_session_store")
    @patch("auth.google_auth.load_credentials_from_session")
    def test_uses_session_credentials_when_email_matches(
        self, mock_load_session, mock_get_store
    ):
        """When Phase 1 doesn't find anything but Phase 3a session matches the requested user."""
        from auth.google_auth import get_credentials

        # Phase 1: OAuth 2.1 store returns nothing
        store = MagicMock()
        mock_get_store.return_value = store
        store.get_credentials_by_mcp_session.return_value = None
        store.get_user_by_mcp_session.return_value = USER_A

        # Phase 3a: session has user A's credentials
        creds_a = _make_credentials(USER_A)
        mock_load_session.return_value = creds_a

        with patch.dict("os.environ", {"MCP_SINGLE_USER_MODE": "0"}, clear=False):
            result = get_credentials(
                user_google_email=USER_A,
                required_scopes=SCOPES,
                session_id=SESSION_ID,
            )

        assert result is not None
        assert result.token == f"token-for-{USER_A}"


class TestFileBasedFallbackPerUser:
    """The file-based credential store must be used for the correct email."""

    @patch("auth.google_auth.get_oauth21_session_store")
    @patch("auth.google_auth.load_credentials_from_session")
    def test_file_store_loads_credentials_for_requested_email(
        self, mock_load_session, mock_get_store
    ):
        """When session has no credentials, the file store should be queried
        with the exact user_google_email."""
        from auth.google_auth import get_credentials

        # Phase 1: no OAuth 2.1 credentials
        store = MagicMock()
        mock_get_store.return_value = store
        store.get_credentials_by_mcp_session.return_value = None

        # Phase 3a: no session credentials
        mock_load_session.return_value = None

        with patch("auth.google_auth.get_credential_store") as mock_file_store, \
             patch("auth.google_auth.is_stateless_mode", return_value=False), \
             patch.dict("os.environ", {"MCP_SINGLE_USER_MODE": "0"}, clear=False):
            file_store = MagicMock()
            mock_file_store.return_value = file_store
            creds_b = _make_credentials(USER_B)
            file_store.get_credential.return_value = creds_b

            result = get_credentials(
                user_google_email=USER_B,
                required_scopes=SCOPES,
                session_id=SESSION_ID,
            )

        assert result is not None
        assert result.token == f"token-for-{USER_B}"
        file_store.get_credential.assert_called_once_with(USER_B)

    @patch("auth.google_auth.get_oauth21_session_store")
    @patch("auth.google_auth.load_credentials_from_session")
    def test_each_email_gets_its_own_credentials_from_file(
        self, mock_load_session, mock_get_store
    ):
        """Two sequential calls for different emails should each get their own credentials."""
        from auth.google_auth import get_credentials

        # Phase 1: no OAuth 2.1 credentials for both calls
        store = MagicMock()
        mock_get_store.return_value = store
        store.get_credentials_by_mcp_session.return_value = None

        # Phase 3a: no session credentials
        mock_load_session.return_value = None

        creds_a = _make_credentials(USER_A)
        creds_b = _make_credentials(USER_B)

        with patch("auth.google_auth.get_credential_store") as mock_file_store, \
             patch("auth.google_auth.is_stateless_mode", return_value=False), \
             patch.dict("os.environ", {"MCP_SINGLE_USER_MODE": "0"}, clear=False):
            file_store = MagicMock()
            mock_file_store.return_value = file_store
            file_store.get_credential.side_effect = lambda email: (
                creds_a if email == USER_A else creds_b
            )

            result_a = get_credentials(
                user_google_email=USER_A,
                required_scopes=SCOPES,
                session_id=SESSION_ID,
            )
            result_b = get_credentials(
                user_google_email=USER_B,
                required_scopes=SCOPES,
                session_id=SESSION_ID,
            )

        assert result_a.token == f"token-for-{USER_A}"
        assert result_b.token == f"token-for-{USER_B}"
