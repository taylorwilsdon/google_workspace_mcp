"""Firestore-backed key/value store for FastMCP OAuth proxy persistence.

Implements the py-key-value-aio BaseStore interface so it can be passed as
`client_storage` to fastmcp.server.auth.OAuthProxy. Persisting the JTI
mapping and upstream token sets in Firestore lets the proxy survive Cloud
Run instance restarts and scale-to-zero without losing client sessions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from key_value.aio.stores.base import BaseStore
from key_value.shared.utils.compound import compound_key
from key_value.shared.utils.managed_entry import ManagedEntry
from typing_extensions import override

try:
    from google.cloud import firestore
except ImportError as e:
    msg = "FirestoreStore requires google-cloud-firestore"
    raise ImportError(msg) from e


_JSON_FIELD = "json"
_EXPIRES_AT_FIELD = "expires_at"


class FirestoreStore(BaseStore):
    """A Firestore-backed key/value store.

    All entries live in a single Firestore collection. Document IDs are the
    compound key `collection:key` so the multi-collection BaseStore semantics
    map cleanly onto a flat Firestore namespace. Each document has two
    fields: the serialized ManagedEntry JSON, and an `expires_at` Timestamp
    that the project's Firestore TTL policy uses for auto-deletion.
    """

    _client: firestore.AsyncClient
    _collection_name: str

    def __init__(
        self,
        *,
        project: str | None = None,
        database: str | None = None,
        collection: str = "workspace_mcp_oauth_kv",
        default_collection: str | None = None,
    ) -> None:
        self._client = firestore.AsyncClient(
            project=project,
            database=database or "(default)",
        )
        self._collection_name = collection

        super().__init__(
            default_collection=default_collection,
            stable_api=True,
        )

    def _doc(self, *, collection: str, key: str) -> Any:
        doc_id = compound_key(collection=collection, key=key)
        return self._client.collection(self._collection_name).document(doc_id)

    @override
    async def _get_managed_entry(
        self, *, key: str, collection: str
    ) -> ManagedEntry | None:
        snapshot = await self._doc(collection=collection, key=key).get()
        if not snapshot.exists:
            return None

        data = snapshot.to_dict() or {}
        json_str = data.get(_JSON_FIELD)
        if not isinstance(json_str, str):
            return None

        managed_entry: ManagedEntry = self._serialization_adapter.load_json(
            json_str=json_str
        )

        # Firestore TTL deletion is eventual (up to 24h lag), so an expired
        # doc may still be present. Honor expires_at on read.
        expires_at = data.get(_EXPIRES_AT_FIELD)
        if expires_at is not None:
            if isinstance(expires_at, datetime):
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                managed_entry.expires_at = expires_at
                if expires_at <= datetime.now(tz=timezone.utc):
                    return None

        return managed_entry

    @override
    async def _put_managed_entry(
        self,
        *,
        key: str,
        collection: str,
        managed_entry: ManagedEntry,
    ) -> None:
        json_str = self._serialization_adapter.dump_json(
            entry=managed_entry, key=key, collection=collection
        )
        payload: dict[str, Any] = {
            _JSON_FIELD: json_str,
            _EXPIRES_AT_FIELD: managed_entry.expires_at,
        }
        await self._doc(collection=collection, key=key).set(payload)

    @override
    async def _delete_managed_entry(self, *, key: str, collection: str) -> bool:
        doc_ref = self._doc(collection=collection, key=key)
        snapshot = await doc_ref.get()
        if not snapshot.exists:
            return False
        await doc_ref.delete()
        return True
