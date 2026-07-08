"""Short-TTL encrypted credential cache for signed attachment streaming (Design A).

The signed ``/attachments/signed`` route needs the attachment owner's Google
credentials, but the GET carries no bearer token and — behind multiple replicas —
may land on a different process than the tool call that minted the URL. In-memory
session state (``auth.oauth21_session_store``) is per-process, so it is not a
reliable source there.

This module gives the route a *shared*, short-lived credential lookup keyed by
email. When the tool mints a signed URL (inside the authenticated request, where
the credentials are in hand) it stashes a minimal credential record here; the
route reads it back. Records are Fernet-encrypted with the same key derivation as
the OAuth proxy's Valkey storage, and expire on the same horizon as the URL, so a
credential record never outlives the link it backs.

Backed by Valkey when ``WORKSPACE_MCP_OAUTH_PROXY_VALKEY_HOST`` is set (the
stateless / hosted deployment, and the local PoC stack). Falls back to an
in-process store otherwise, which still works locally because the single container
serves both the tool and the route.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

_COLLECTION = "attachment_cred_cache"

# Module-level singleton. The tool and route run in the same uvicorn event loop,
# so a single lazily-built store is reused across requests.
_store = None
_store_built = False


def _derive_storage_key() -> bytes:
    """Derive this cache's Fernet key (distinct salt = distinct crypto context).

    Same inputs as the OAuth proxy's storage encryption (JWT signing key
    override → else client secret) but a *distinct* salt, so this cache is a
    separate cryptographic context from the proxy's client storage even though
    both live in the same shared store.
    """
    from core.storage import derive_shared_fernet_key

    return derive_shared_fernet_key("workspace-attachment-cred-cache")


def _build_store():
    """Build the encrypted key-value store once (Valkey if configured, else memory)."""
    global _store, _store_built
    if _store_built:
        return _store
    _store_built = True

    try:
        from cryptography.fernet import Fernet
        from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

        # Mirror server.py's backend selection: an explicit STORAGE_BACKEND=valkey
        # (host defaulting to localhost) is a valid config and must not silently
        # fall back to per-process memory, which would break cross-replica recovery.
        storage_backend = (
            os.getenv("WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND", "").strip().lower()
        )
        valkey_host = os.getenv("WORKSPACE_MCP_OAUTH_PROXY_VALKEY_HOST", "").strip()
        use_valkey = storage_backend == "valkey" or bool(valkey_host)

        if use_valkey:
            if not valkey_host:
                valkey_host = "localhost"
            from key_value.aio.stores.valkey import ValkeyStore

            port = int(
                os.getenv("WORKSPACE_MCP_OAUTH_PROXY_VALKEY_PORT", "6379").strip()
            )
            db = int(os.getenv("WORKSPACE_MCP_OAUTH_PROXY_VALKEY_DB", "0").strip())
            username = (
                os.getenv("WORKSPACE_MCP_OAUTH_PROXY_VALKEY_USERNAME", "").strip()
                or None
            )
            password = (
                os.getenv("WORKSPACE_MCP_OAUTH_PROXY_VALKEY_PASSWORD", "").strip()
                or None
            )

            base = ValkeyStore(
                host=valkey_host, port=port, db=db, username=username, password=password
            )

            # Mirror the proxy's TLS/timeout handling so remote/TLS Valkey doesn't
            # trip Glide's 250ms default request timeout.
            tls_raw = (
                os.getenv("WORKSPACE_MCP_OAUTH_PROXY_VALKEY_USE_TLS", "")
                .strip()
                .lower()
            )
            use_tls = tls_raw in ("1", "true", "yes") if tls_raw else port == 6380
            glide_config = getattr(base, "_client_config", None)
            if glide_config is not None:
                glide_config.use_tls = use_tls
                is_remote = valkey_host not in {"localhost", "127.0.0.1"}
                if use_tls or is_remote:
                    glide_config.request_timeout = 5000
                    from glide_shared.config import AdvancedGlideClientConfiguration

                    glide_config.advanced_config = AdvancedGlideClientConfiguration(
                        connection_timeout=10000
                    )

            _store = FernetEncryptionWrapper(
                key_value=base, fernet=Fernet(key=_derive_storage_key())
            )
            logger.info(
                "Attachment credential cache: using encrypted ValkeyStore (host=%s, port=%s, db=%s)",
                valkey_host,
                port,
                db,
            )
        else:
            from key_value.aio.stores.memory import MemoryStore

            _store = MemoryStore()
            logger.info(
                "Attachment credential cache: no Valkey configured, using in-process store "
                "(single-instance only)."
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Attachment credential cache unavailable: %s", exc)
        _store = None

    return _store


def _credentials_to_record(credentials: Credentials) -> dict:
    expiry = credentials.expiry
    expiry_iso = None
    if expiry is not None:
        # Credentials.expiry is naive UTC; serialize as ISO for transport.
        expiry_iso = expiry.replace(tzinfo=timezone.utc).isoformat()
    return {
        "access_token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes or []),
        "expiry": expiry_iso,
    }


def _record_to_credentials(record: dict) -> Credentials:
    expiry = None
    if record.get("expiry"):
        parsed = datetime.fromisoformat(record["expiry"])
        # google-auth expects a naive UTC datetime.
        expiry = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return Credentials(
        token=record.get("access_token"),
        refresh_token=record.get("refresh_token"),
        token_uri=record.get("token_uri"),
        client_id=record.get("client_id"),
        client_secret=record.get("client_secret"),
        scopes=record.get("scopes") or [],
        expiry=expiry,
    )


async def stash_credentials(
    user_email: str, credentials: Credentials, ttl_seconds: float
) -> bool:
    """Cache the owner's credentials for the lifetime of a signed URL.

    Returns True if stored, False if no store is available. Failures are
    swallowed (best-effort): the same-process in-memory session path still covers
    the local case, so a cache miss degrades rather than breaks.
    """
    store = _build_store()
    if store is None:
        return False
    try:
        await store.put(
            user_email,
            _credentials_to_record(credentials),
            collection=_COLLECTION,
            ttl=ttl_seconds,
        )
        return True
    except Exception as exc:
        logger.warning(
            "Failed to cache attachment credentials for %s: %s", user_email, exc
        )
        return False


async def load_credentials(user_email: str) -> Optional[Credentials]:
    """Recover cached credentials by email, or None if absent/expired/unavailable."""
    store = _build_store()
    if store is None:
        return None
    try:
        record = await store.get(user_email, collection=_COLLECTION)
    except Exception as exc:
        logger.warning("Failed to read cached credentials for %s: %s", user_email, exc)
        return None
    if not record:
        return None
    try:
        return _record_to_credentials(record)
    except Exception as exc:
        logger.warning("Malformed cached credential record for %s: %s", user_email, exc)
        return None
