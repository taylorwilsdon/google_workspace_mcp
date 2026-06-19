"""
Credential Store API for Google Workspace MCP

This module provides a standardized interface for credential storage and retrieval,
supporting multiple backends configurable via environment variables.
"""

import json
import logging
import os
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional
from urllib.parse import quote, unquote

from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)


class CredentialStore(ABC):
    """Abstract base class for credential storage."""

    FILE_EXTENSION = ".json"

    @abstractmethod
    def get_credential(self, user_email: str) -> Optional[Credentials]:
        """
        Get credentials for a user by email.

        Args:
            user_email: User's email address

        Returns:
            Google Credentials object or None if not found
        """
        pass

    @abstractmethod
    def store_credential(self, user_email: str, credentials: Credentials) -> bool:
        """
        Store credentials for a user.

        Args:
            user_email: User's email address
            credentials: Google Credentials object to store

        Returns:
            True if successfully stored, False otherwise
        """
        pass

    @abstractmethod
    def delete_credential(self, user_email: str) -> bool:
        """
        Delete credentials for a user.

        Args:
            user_email: User's email address

        Returns:
            True if successfully deleted, False otherwise
        """
        pass

    @abstractmethod
    def list_users(self) -> List[str]:
        """
        List all users with stored credentials.

        Returns:
            List of user email addresses
        """
        pass


class LocalDirectoryCredentialStore(CredentialStore):
    """Credential store that uses local JSON files for storage."""

    def __init__(self, base_dir: Optional[str] = None):
        """
        Initialize the local JSON credential store.

        Args:
            base_dir: Base directory for credential files. If None, uses the directory
                     configured by environment variables in this order:
                     1. WORKSPACE_MCP_CREDENTIALS_DIR (preferred)
                     2. GOOGLE_MCP_CREDENTIALS_DIR (backward compatibility)
                     3. ~/.google_workspace_mcp/credentials (default)
        """
        if base_dir is None:
            # Check WORKSPACE_MCP_CREDENTIALS_DIR first (preferred)
            workspace_creds_dir = os.getenv("WORKSPACE_MCP_CREDENTIALS_DIR")
            google_creds_dir = os.getenv("GOOGLE_MCP_CREDENTIALS_DIR")

            if workspace_creds_dir:
                base_dir = os.path.expanduser(workspace_creds_dir)
                logger.info(
                    f"Using credentials directory from WORKSPACE_MCP_CREDENTIALS_DIR: {base_dir}"
                )
            # Fall back to GOOGLE_MCP_CREDENTIALS_DIR for backward compatibility
            elif google_creds_dir:
                base_dir = os.path.expanduser(google_creds_dir)
                logger.info(
                    f"Using credentials directory from GOOGLE_MCP_CREDENTIALS_DIR: {base_dir}"
                )
            else:
                home_dir = os.path.expanduser("~")
                if home_dir and home_dir != "~":
                    base_dir = os.path.join(
                        home_dir, ".google_workspace_mcp", "credentials"
                    )
                else:
                    base_dir = os.path.join(os.getcwd(), ".credentials")
                logger.info(f"Using default credentials directory: {base_dir}")

        self.base_dir = base_dir
        logger.info(
            f"LocalDirectoryCredentialStore initialized with base_dir: {base_dir}"
        )

    @staticmethod
    def _legacy_safe_email(user_email: str) -> str:
        """Return the pre-URL-encoding filename form for backward compatibility."""
        return re.sub(r"[^a-zA-Z0-9@._-]", "_", user_email)

    def _resolve_credential_path(self, filename: str) -> str:
        """Resolve a credential filename under base_dir and enforce containment."""
        creds_path = os.path.join(self.base_dir, filename)

        # Verify resolved path is still under base_dir
        base_resolved = os.path.realpath(str(self.base_dir))
        resolved = os.path.realpath(creds_path)
        if not resolved.startswith(base_resolved + os.sep):
            raise ValueError(f"Invalid credential path: {creds_path}")

        return creds_path

    def _get_credential_path(self, user_email: str) -> str:
        """Get the file path for a user's credentials.

        URL-encodes user_email to prevent path traversal while preserving a
        collision-free mapping from email address to filename. For backward
        compatibility, pre-existing legacy filenames from the older regex-based
        sanitization scheme are still discovered if the URL-encoded file does
        not exist yet. The resolved path is validated to remain under base_dir.
        """
        if not user_email or not user_email.strip():
            raise ValueError("user_email must be a non-empty string")

        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir, mode=0o700, exist_ok=True)
            logger.info(f"Created credentials directory: {self.base_dir}")

        safe_email = quote(user_email, safe="@._-")
        creds_path = self._resolve_credential_path(f"{safe_email}{self.FILE_EXTENSION}")

        if os.path.exists(creds_path):
            return creds_path

        legacy_safe_email = self._legacy_safe_email(user_email)
        if legacy_safe_email != safe_email:
            legacy_path = self._resolve_credential_path(
                f"{legacy_safe_email}{self.FILE_EXTENSION}"
            )
            if os.path.exists(legacy_path):
                logger.info(
                    "Using legacy credential filename for %s at %s",
                    user_email,
                    legacy_path,
                )
                return legacy_path

        return creds_path

    def get_credential(self, user_email: str) -> Optional[Credentials]:
        """Get credentials from local JSON file."""
        creds_path = self._get_credential_path(user_email)

        if not os.path.exists(creds_path):
            logger.debug(f"No credential file found for {user_email} at {creds_path}")
            return None

        try:
            with open(creds_path, "r") as f:
                creds_data = json.load(f)

            # Parse expiry if present
            expiry = None
            if creds_data.get("expiry"):
                try:
                    expiry = datetime.fromisoformat(creds_data["expiry"])
                    # Ensure timezone-naive datetime for Google auth library compatibility
                    if expiry.tzinfo is not None:
                        expiry = expiry.replace(tzinfo=None)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Could not parse expiry time for {user_email}: {e}")

            credentials = Credentials(
                token=creds_data.get("token"),
                refresh_token=creds_data.get("refresh_token"),
                token_uri=creds_data.get("token_uri"),
                client_id=creds_data.get("client_id"),
                client_secret=creds_data.get("client_secret"),
                scopes=creds_data.get("scopes"),
                expiry=expiry,
            )

            logger.debug(f"Loaded credentials for {user_email} from {creds_path}")
            return credentials

        except (IOError, json.JSONDecodeError, KeyError) as e:
            logger.error(
                f"Error loading credentials for {user_email} from {creds_path}: {e}"
            )
            return None

    def store_credential(self, user_email: str, credentials: Credentials) -> bool:
        """Store credentials to local JSON file."""
        creds_path = self._get_credential_path(user_email)

        creds_data = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
            "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
        }

        try:
            fd = os.open(str(creds_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(creds_data, f, indent=2)
            logger.info(f"Stored credentials for {user_email} to {creds_path}")
            return True
        except IOError as e:
            logger.error(
                f"Error storing credentials for {user_email} to {creds_path}: {e}"
            )
            return False

    def delete_credential(self, user_email: str) -> bool:
        """Delete credential file for a user."""
        creds_path = self._get_credential_path(user_email)

        try:
            if os.path.exists(creds_path):
                os.remove(creds_path)
                logger.info(f"Deleted credentials for {user_email} from {creds_path}")
                return True
            else:
                logger.debug(
                    f"No credential file to delete for {user_email} at {creds_path}"
                )
                return True  # Consider it a success if file doesn't exist
        except IOError as e:
            logger.error(
                f"Error deleting credentials for {user_email} from {creds_path}: {e}"
            )
            return False

    def list_users(self) -> List[str]:
        """List all users with credential files."""
        if not os.path.exists(self.base_dir):
            return []

        users = set()
        non_credential_files = {"oauth_states"}
        try:
            for filename in os.listdir(self.base_dir):
                if filename.endswith(self.FILE_EXTENSION):
                    stored_name = filename[: -len(self.FILE_EXTENSION)]
                    user_email = (
                        unquote(stored_name) if "%" in stored_name else stored_name
                    )
                    if (
                        stored_name in non_credential_files
                        or user_email in non_credential_files
                        or "@" not in user_email
                    ):
                        continue
                    users.add(user_email)
            logger.debug(
                f"Found {len(users)} users with credentials in {self.base_dir}"
            )
        except OSError as e:
            logger.error(f"Error listing credential files in {self.base_dir}: {e}")

        return sorted(users)


class GCSCredentialStore(CredentialStore):
    """Credential store backed directly by a Google Cloud Storage bucket.

    Uses the GCS JSON API (not a gcsfuse mount) which provides:
    - Atomic read-modify-write via generation preconditions (prevents lost
      updates on concurrent token rotation).
    - First-class integration with Cloud IAM, Cloud Audit Logs, and VPC-SC.
    - Transparent bucket-level CMEK encryption at rest (no app-level key).

    Confidentiality at rest is delegated to the bucket's encryption
    configuration:
    - Default: Google-managed encryption (always on, zero configuration).
    - CMEK: Set the bucket's default_kms_key_name (e.g. via Terraform).
      Every object inherits the key; no per-request configuration needed.

    To guard against accidentally deploying against a bucket without CMEK,
    set ``WORKSPACE_MCP_GCS_REQUIRE_CMEK=true`` and call ``verify_cmek()``
    during synchronous startup. Constructor initialization deliberately avoids
    fetching bucket metadata so async callers do not block the event loop.

    This backend does not support ``list_users()`` — it is designed for
    multi-user OAuth 2.1 mode where users are looked up by email.

    Configuration (env vars):
        WORKSPACE_MCP_GCS_BUCKET         — bucket name (required)
        WORKSPACE_MCP_GCS_PREFIX         — object prefix, default empty
        WORKSPACE_MCP_GCS_REQUIRE_CMEK   — "true"/"1" to enforce CMEK
    """

    def __init__(
        self,
        bucket_name: Optional[str] = None,
        prefix: Optional[str] = None,
        require_cmek: Optional[bool] = None,
    ):
        from google.cloud import storage
        from google.cloud.exceptions import NotFound, PreconditionFailed

        bucket_name = bucket_name or os.getenv("WORKSPACE_MCP_GCS_BUCKET")
        if not bucket_name:
            raise ValueError(
                "GCSCredentialStore requires a bucket name; "
                "pass bucket_name or set WORKSPACE_MCP_GCS_BUCKET"
            )
        self.bucket_name = bucket_name

        prefix = (
            prefix if prefix is not None else os.getenv("WORKSPACE_MCP_GCS_PREFIX", "")
        )
        self.prefix = prefix.strip("/")
        if self.prefix:
            self.prefix += "/"

        if require_cmek is None:
            require_cmek = _parse_bool_env(
                os.getenv("WORKSPACE_MCP_GCS_REQUIRE_CMEK", "")
            )
        self.require_cmek = require_cmek

        self._NotFound = NotFound
        self._PreconditionFailed = PreconditionFailed
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket_name)

        logger.info(
            f"GCSCredentialStore initialized with bucket={bucket_name}, "
            f"prefix={self.prefix!r}, require_cmek={require_cmek}"
        )

    def _verify_cmek(self) -> None:
        """Fetch bucket metadata and assert a default KMS key is configured.

        Raises ValueError if the bucket exists but has no default_kms_key_name.
        Raises the underlying google exception if the bucket does not exist.
        """
        self._bucket.reload()
        if not self._bucket.default_kms_key_name:
            raise ValueError(
                f"GCSCredentialStore: bucket {self.bucket_name!r} has no "
                f"default KMS key configured, but "
                f"WORKSPACE_MCP_GCS_REQUIRE_CMEK is set. Either configure "
                f"bucket.default_kms_key_name (e.g. via Terraform) or unset "
                f"the requirement flag."
            )
        logger.info(
            f"GCSCredentialStore: verified CMEK on bucket "
            f"{self.bucket_name!r} (key={self._bucket.default_kms_key_name})"
        )

    def verify_cmek(self) -> None:
        """Synchronously verify bucket CMEK when the backend is configured to require it."""
        if not self.require_cmek:
            return
        self._verify_cmek()

    def _blob_name(self, user_email: str) -> str:
        """Construct the object key for a user, URL-encoding to prevent collisions."""
        if not user_email or not user_email.strip():
            raise ValueError("user_email must be a non-empty string")
        safe_email = quote(user_email, safe="@._-")
        return f"{self.prefix}{safe_email}{self.FILE_EXTENSION}"

    def get_credential(self, user_email: str) -> Optional[Credentials]:
        """Download and deserialize credentials for a user."""
        blob = self._bucket.blob(self._blob_name(user_email))
        try:
            raw = blob.download_as_bytes()
        except self._NotFound:
            logger.debug(f"No credentials object for {user_email}")
            return None
        except Exception as e:
            logger.error(f"Error downloading credentials for {user_email}: {e}")
            raise

        try:
            creds_data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing credentials for {user_email}: {e}")
            return None

        expiry = None
        if creds_data.get("expiry"):
            try:
                expiry = datetime.fromisoformat(creds_data["expiry"])
                if expiry.tzinfo is not None:
                    expiry = expiry.replace(tzinfo=None)
            except (ValueError, TypeError) as e:
                logger.warning(f"Could not parse expiry for {user_email}: {e}")

        return Credentials(
            token=creds_data.get("token"),
            refresh_token=creds_data.get("refresh_token"),
            token_uri=creds_data.get("token_uri"),
            client_id=creds_data.get("client_id"),
            client_secret=creds_data.get("client_secret"),
            scopes=creds_data.get("scopes"),
            expiry=expiry,
        )

    def store_credential(self, user_email: str, credentials: Credentials) -> bool:
        """Serialize and upload credentials using a generation precondition.

        Fails fast on concurrent writes (HTTP 412). We deliberately do NOT
        retry: the payload we hold reflects the *pre-race* state, so
        retrying with a fresh generation but the same payload would
        overwrite a racing writer's updates. Returning False signals the
        caller to abandon this attempt; the next credential refresh will
        read the latest state and try again.
        """
        creds_data = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
            "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
        }
        payload = json.dumps(creds_data).encode()

        blob = self._bucket.blob(self._blob_name(user_email))
        try:
            try:
                blob.reload()
                generation = blob.generation
            except self._NotFound:
                generation = 0  # must-not-exist precondition

            blob.upload_from_string(
                payload,
                content_type="application/json",
                if_generation_match=generation,
            )
            logger.info(f"Stored credentials for {user_email}")
            return True
        except self._PreconditionFailed:
            logger.warning(
                f"Concurrent write detected for {user_email}; "
                f"abandoning this write so next refresh can merge current state"
            )
            return False
        except Exception as e:
            logger.error(f"Error storing credentials for {user_email}: {e}")
            return False

    def delete_credential(self, user_email: str) -> bool:
        """Delete a user's credentials object; idempotent."""
        blob = self._bucket.blob(self._blob_name(user_email))
        try:
            blob.delete()
            logger.info(f"Deleted credentials for {user_email}")
            return True
        except self._NotFound:
            return True
        except Exception as e:
            logger.error(f"Error deleting credentials for {user_email}: {e}")
            return False

    def list_users(self) -> List[str]:
        """Not supported by this backend.

        Designed for multi-user OAuth 2.1 mode where users are looked up
        individually by email. Enumerating all users requires a bucket LIST
        which is semantically wrong for that flow. Use
        ``LocalDirectoryCredentialStore`` if you need single-user mode.
        """
        raise NotImplementedError(
            "GCSCredentialStore does not support listing users. "
            "This backend is designed for multi-user OAuth 2.1 mode where "
            "users are looked up individually by email. Use "
            "LocalDirectoryCredentialStore if you need single-user mode."
        )


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})


def _parse_bool_env(value: Optional[str]) -> bool:
    """Parse a boolean env var value, failing loudly on anything unrecognised.

    Accepts (case-insensitive, whitespace-trimmed):
        true:  ``1``, ``true``, ``yes``, ``on``
        false: ``0``, ``false``, ``no``, ``off``, empty string, None

    Raises ValueError for any other input. The strict parsing matters for
    security-relevant flags (e.g. ``WORKSPACE_MCP_GCS_REQUIRE_CMEK``) where
    a typo like ``"treu"`` would otherwise silently disable the flag.
    """
    if value is None:
        return False
    normalised = value.strip().lower()
    if normalised in _TRUE_VALUES:
        return True
    if normalised in _FALSE_VALUES:
        return False
    raise ValueError(
        f"Invalid boolean env var value: {value!r}. "
        f"Expected one of: {sorted((_TRUE_VALUES | _FALSE_VALUES) - {''})}"
    )


def get_selected_backend() -> str:
    """Return the configured credential store backend."""
    return (
        os.getenv("WORKSPACE_MCP_CREDENTIAL_STORE_BACKEND", "").strip().lower()
        or "local_directory"
    )


def _selected_backend() -> str:
    """Backward-compatible alias for callers not yet using the public helper."""
    return get_selected_backend()


# Global credential store instance
_credential_store: Optional[CredentialStore] = None


def get_credential_store() -> CredentialStore:
    """
    Get the global credential store instance.

    Returns:
        Configured credential store instance
    """
    global _credential_store

    if _credential_store is None:
        backend = get_selected_backend()
        if backend == "gcs":
            # GCS backend does not support list_users(), which is required for
            # single-user mode. Reject unless OAuth 2.1 is enabled.
            oauth21_enabled = _parse_bool_env(os.getenv("MCP_ENABLE_OAUTH21", "false"))
            if not oauth21_enabled:
                raise ValueError(
                    "GCSCredentialStore requires MCP_ENABLE_OAUTH21=true. "
                    "The GCS backend does not support list_users(), which is "
                    "required for single-user mode. Use LocalDirectoryCredentialStore "
                    "for single-user deployments, or enable OAuth 2.1 mode."
                )
            _credential_store = GCSCredentialStore()
        elif backend == "local_directory":
            _credential_store = LocalDirectoryCredentialStore()
        else:
            raise ValueError(
                f"Unsupported WORKSPACE_MCP_CREDENTIAL_STORE_BACKEND: {backend!r}. "
                f"Expected 'local_directory' or 'gcs'."
            )
        logger.info(f"Initialized credential store: {type(_credential_store).__name__}")

    return _credential_store


def set_credential_store(store: CredentialStore):
    """
    Set the global credential store instance.

    Args:
        store: Credential store instance to use
    """
    global _credential_store
    _credential_store = store
    logger.info(f"Set credential store: {type(store).__name__}")
