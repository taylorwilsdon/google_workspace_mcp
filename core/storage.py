"""Shared helpers for creating disk-backed key-value stores."""

import os
import string

from key_value.aio._utils.sanitization import HybridSanitizationStrategy
from key_value.aio.stores.filetree import FileTreeStore

SAFE_FILENAME_CHARS = string.ascii_letters + string.digits + "-_."
"""Characters allowed in on-disk file names for key-value stores."""


def make_sanitized_file_store(data_directory: str) -> FileTreeStore:
    """Return a ``FileTreeStore`` using the project-wide sanitization rules.

    Both the OAuth-proxy server storage and the CLI token storage need
    identical sanitization; this factory keeps them in sync.
    """
    return FileTreeStore(
        data_directory=data_directory,
        key_sanitization_strategy=HybridSanitizationStrategy(
            allowed_characters=SAFE_FILENAME_CHARS,
        ),
    )


def derive_shared_fernet_key(salt: str) -> bytes:
    """Derive a Fernet key for encrypting records in the shared KV store.

    Mirrors the OAuth proxy's storage-encryption derivation (JWT signing key
    override → else Google client secret) so every consumer keys off the same
    deployment secret, but with a caller-chosen salt so each consumer is a
    distinct cryptographic context inside the same store.
    """
    from fastmcp.server.auth.jwt_issuer import derive_jwt_key

    override = os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()

    if override:
        jwt_key = derive_jwt_key(
            low_entropy_material=override, salt="fastmcp-jwt-signing-key"
        )
    elif client_secret:
        jwt_key = derive_jwt_key(
            high_entropy_material=client_secret, salt="fastmcp-jwt-signing-key"
        )
    else:
        raise ValueError(
            "Encrypted shared storage requires GOOGLE_OAUTH_CLIENT_SECRET or "
            "FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY."
        )

    return derive_jwt_key(high_entropy_material=jwt_key.decode(), salt=salt)
