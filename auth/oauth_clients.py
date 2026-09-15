"""
Multi-client OAuth registry.

A single Google Cloud project can only issue OAuth credentials that its own
consent screen permits. A project whose consent screen is configured as
``Internal`` rejects every account outside its Workspace organization with
``Error 403: org_internal``. Serving accounts that live in different
organizations therefore requires more than one OAuth client.

This module maps a Google account to the OAuth client that is allowed to
authorize it. Resolution is by exact email first, then by domain, then by a
configured default, so the common single-client deployment needs no
configuration at all and keeps working unchanged.

Note that only the *initial authorization* consults this registry. Stored
credentials already embed the ``client_id`` and ``client_secret`` they were
issued with (see ``auth.credential_store``), so refreshing an existing token
uses that account's original client without reference to this registry.

Configuration (first match wins):

1. ``GOOGLE_OAUTH_CLIENTS_FILE`` — path to a JSON registry file.
2. ``GOOGLE_OAUTH_CLIENTS`` — the same JSON document, inline.
3. ``GOOGLE_OAUTH_CLIENT_ID`` / ``GOOGLE_OAUTH_CLIENT_SECRET`` — the legacy
   single-client variables, used as a registry of exactly one client.

Registry document shape::

    {
      "default": "personal",
      "clients": {
        "personal": {"client_id": "...", "client_secret": "..."},
        "work":     {"client_id": "...", "client_secret": "..."}
      },
      "domains": {"example.com": "work"},
      "emails":  {"someone@gmail.com": "personal"}
    }

``client_secret`` is optional; omitting it declares a public (PKCE) client.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

# Environment variables read by this module.
# Provenance of a registry. A registry document declares its clients per Cloud
# project deliberately, so the single-client client_secret.json fallback has no
# authority to amend its entries; a registry synthesized from the legacy
# GOOGLE_OAUTH_CLIENT_ID/SECRET pair describes the same one client that fallback
# is completing, so there it must.
SOURCE_REGISTRY_DOCUMENT = "registry-document"
SOURCE_LEGACY_ENV = "legacy-env"

ENV_CLIENTS_FILE = "GOOGLE_OAUTH_CLIENTS_FILE"
ENV_CLIENTS_INLINE = "GOOGLE_OAUTH_CLIENTS"
ENV_LEGACY_CLIENT_ID = "GOOGLE_OAUTH_CLIENT_ID"
ENV_LEGACY_CLIENT_SECRET = "GOOGLE_OAUTH_CLIENT_SECRET"

# Key assigned to the client synthesized from the legacy single-client env vars.
LEGACY_CLIENT_KEY = "default"


class OAuthClientRegistryError(ValueError):
    """Raised when the OAuth client registry is malformed."""


class OAuthClientResolutionError(ValueError):
    """Raised when a configured registry can select no client for a request.

    This is deliberately an exception and not a second ``None``. "The registry
    refused this account" and "no OAuth client is configured at all" are
    opposite situations that used to share one sentinel, and every caller read
    it as the second: the refusal fell through to whatever ``client_secret.json``
    happened to be on disk, authorizing an unmapped account against an
    arbitrary Cloud project. A sentinel obliges every present and future caller
    to remember to check; an exception is fail-closed by omission, which is the
    property that was missing.

    Subclasses ``ValueError`` so existing handlers keep working.
    """


@dataclass(frozen=True)
class OAuthClient:
    """One OAuth client, i.e. one Google Cloud project's credentials."""

    key: str
    client_id: str
    # repr=False, not merely "call describe() instead": the generated repr is
    # what "%r" logging, f-strings, container reprs and tracebacks-with-locals
    # all reach for, and none of those call describe(). The secret is excluded
    # from the repr so there is no rendering of this object that leaks it.
    # client_id stays visible — it is not a credential (it is published in
    # every authorization URL) and it is what makes a log line diagnosable.
    client_secret: Optional[str] = field(default=None, repr=False)

    @property
    def is_public(self) -> bool:
        """True when no secret is configured (a PKCE/desktop client)."""
        return not self.client_secret

    def describe(self) -> str:
        """Log-safe description. Never includes the secret."""
        suffix = "public" if self.is_public else "confidential"
        return f"{self.key} ({self.client_id[:16]}…, {suffix})"


class OAuthClientRegistry:
    """Resolves a Google account to the OAuth client that may authorize it."""

    def __init__(
        self,
        clients: Mapping[str, OAuthClient],
        default_key: Optional[str] = None,
        domains: Optional[Mapping[str, str]] = None,
        emails: Optional[Mapping[str, str]] = None,
        source: str = SOURCE_REGISTRY_DOCUMENT,
    ):
        # Where this registry came from. Recorded rather than re-derived by
        # callers: whether the single-client secrets-file fallback may amend the
        # default entry depends on it, and a caller re-reading the environment
        # to answer that would be a second copy of the precedence rules in
        # load_registry_from_env(), free to drift out of agreement with it.
        self.source = source
        self._clients: Dict[str, OAuthClient] = dict(clients)
        # Match case-insensitively; Google addresses and domains are not
        # case-sensitive, and a config typo here fails silently otherwise.
        self._domains: Dict[str, str] = {
            k.lower(): v for k, v in (domains or {}).items()
        }
        self._emails: Dict[str, str] = {k.lower(): v for k, v in (emails or {}).items()}

        if default_key is not None and default_key not in self._clients:
            raise OAuthClientRegistryError(
                f"default client {default_key!r} is not defined in 'clients' "
                f"(defined: {sorted(self._clients) or 'none'})"
            )
        # With exactly one client and no explicit default, that client is the
        # default. With several, an unset default means "no fallback": an
        # unmapped account is an error rather than a silent wrong-project auth.
        if default_key is None and len(self._clients) == 1:
            default_key = next(iter(self._clients))
        self._default_key = default_key

        for label, mapping in (("domains", self._domains), ("emails", self._emails)):
            for source, target in mapping.items():
                if target not in self._clients:
                    raise OAuthClientRegistryError(
                        f"{label} entry {source!r} refers to undefined client "
                        f"{target!r} (defined: {sorted(self._clients) or 'none'})"
                    )

    def __len__(self) -> int:
        return len(self._clients)

    @property
    def keys(self):
        return sorted(self._clients)

    @property
    def default(self) -> Optional[OAuthClient]:
        if self._default_key is None:
            return None
        return self._clients[self._default_key]

    def get(self, key: str) -> Optional[OAuthClient]:
        """Look up a client by its registry key."""
        return self._clients.get(key)

    def resolve(self, user_google_email: Optional[str] = None) -> Optional[OAuthClient]:
        """
        Resolve the client for an account: exact email, then domain, then default.

        Returns None when nothing matches and no default is configured, which
        the caller must treat as a configuration error rather than falling back
        to an arbitrary client.
        """
        if user_google_email:
            email = user_google_email.strip().lower()
            key = self._emails.get(email)
            if key:
                logger.debug("OAuth client %r matched email %s", key, email)
                return self._clients[key]

            _, _, domain = email.partition("@")
            if domain:
                key = self._domains.get(domain)
                if key:
                    logger.debug("OAuth client %r matched domain %s", key, domain)
                    return self._clients[key]

        default = self.default
        if default is not None and user_google_email:
            logger.debug(
                "OAuth client %r used as default for %s",
                default.key,
                user_google_email,
            )
        return default


def _parse_client_entry(key: str, raw: Any) -> OAuthClient:
    if not isinstance(raw, dict):
        raise OAuthClientRegistryError(
            f"client {key!r} must be an object, got {type(raw).__name__}"
        )

    client_id = raw.get("client_id")
    if not isinstance(client_id, str) or not client_id.strip():
        raise OAuthClientRegistryError(
            f"client {key!r} is missing a non-empty string 'client_id'"
        )

    client_secret = raw.get("client_secret")
    if client_secret is not None and not isinstance(client_secret, str):
        raise OAuthClientRegistryError(
            f"client {key!r} has a non-string 'client_secret'"
        )
    # Treat "" as absent so a blank value in config means "public client"
    # rather than a confidential client with an empty secret.
    if isinstance(client_secret, str) and not client_secret.strip():
        client_secret = None

    return OAuthClient(
        key=key, client_id=client_id.strip(), client_secret=client_secret
    )


def _parse_str_map(document: Mapping[str, Any], field: str) -> Dict[str, str]:
    raw = document.get(field)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise OAuthClientRegistryError(
            f"'{field}' must be an object, got {type(raw).__name__}"
        )
    parsed: Dict[str, str] = {}
    for source, target in raw.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise OAuthClientRegistryError(
                f"'{field}' entries must map string to string; "
                f"got {source!r} -> {target!r}"
            )
        parsed[source.strip()] = target.strip()
    return parsed


def parse_registry_document(document: Any) -> OAuthClientRegistry:
    """Build a registry from an already-decoded JSON document."""
    if not isinstance(document, dict):
        raise OAuthClientRegistryError(
            f"registry must be a JSON object, got {type(document).__name__}"
        )

    raw_clients = document.get("clients")
    if not isinstance(raw_clients, dict) or not raw_clients:
        raise OAuthClientRegistryError(
            "registry must define a non-empty 'clients' object"
        )

    clients = {
        key: _parse_client_entry(key, value) for key, value in raw_clients.items()
    }

    default_key = document.get("default")
    if default_key is not None and not isinstance(default_key, str):
        raise OAuthClientRegistryError(
            f"'default' must be a string, got {type(default_key).__name__}"
        )

    return OAuthClientRegistry(
        clients=clients,
        default_key=default_key,
        domains=_parse_str_map(document, "domains"),
        emails=_parse_str_map(document, "emails"),
    )


def _load_registry_from_file(path: str) -> OAuthClientRegistry:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except OSError as exc:
        raise OAuthClientRegistryError(
            f"could not read {ENV_CLIENTS_FILE} at {path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise OAuthClientRegistryError(
            f"{ENV_CLIENTS_FILE} at {path} is not valid JSON: {exc}"
        ) from exc
    return parse_registry_document(document)


def _load_registry_from_inline(raw: str) -> OAuthClientRegistry:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OAuthClientRegistryError(
            f"{ENV_CLIENTS_INLINE} is not valid JSON: {exc}"
        ) from exc
    return parse_registry_document(document)


def _load_legacy_single_client() -> Optional[OAuthClientRegistry]:
    client_id = os.getenv(ENV_LEGACY_CLIENT_ID)
    if not client_id or not client_id.strip():
        return None
    secret = os.getenv(ENV_LEGACY_CLIENT_SECRET)
    client = OAuthClient(
        key=LEGACY_CLIENT_KEY,
        client_id=client_id.strip(),
        client_secret=secret.strip() if secret and secret.strip() else None,
    )
    return OAuthClientRegistry(
        clients={LEGACY_CLIENT_KEY: client},
        default_key=LEGACY_CLIENT_KEY,
        source=SOURCE_LEGACY_ENV,
    )


def load_registry_from_env() -> Optional[OAuthClientRegistry]:
    """
    Build the registry from the environment.

    Returns None when no OAuth client is configured at all, which callers
    already handle by falling back to a client secrets file.

    Raises:
        OAuthClientRegistryError: if a registry is configured but malformed.
            This is deliberately fatal — silently ignoring a broken registry
            would authorize accounts against the wrong Cloud project.
    """
    path = os.getenv(ENV_CLIENTS_FILE)
    if path and path.strip():
        registry = _load_registry_from_file(path.strip())
        logger.info(
            "Loaded %d OAuth client(s) from %s: %s",
            len(registry),
            ENV_CLIENTS_FILE,
            ", ".join(registry.keys),
        )
        return registry

    inline = os.getenv(ENV_CLIENTS_INLINE)
    if inline and inline.strip():
        registry = _load_registry_from_inline(inline.strip())
        logger.info(
            "Loaded %d OAuth client(s) from %s: %s",
            len(registry),
            ENV_CLIENTS_INLINE,
            ", ".join(registry.keys),
        )
        return registry

    return _load_legacy_single_client()
