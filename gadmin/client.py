"""Google clients built from discovery documents pinned in this package.

The installed google-api-python-client ships static Cloud Identity and Chrome
Management documents that are older than the pinned revision and lack
registered methods, and Google publishes no document for Contact Delegation.
For those APIs, the client authenticated through the verified path is rebuilt
from the complete document in ``gadmin/discovery`` on the same authorized
transport and endpoint, so credentials and account binding are unchanged.
Contact Delegation is authenticated as the Directory API, which shares its
endpoint. Access Context Manager calls also find the customer's Google
Cloud organization with the installed Cloud Resource Manager document on the
same transport. Nothing is fetched at call time.
"""

from functools import lru_cache
from pathlib import Path

from googleapiclient.discovery import build, build_from_document

DISCOVERY_DIR = Path(__file__).parent / "discovery"
PINNED_APIS = frozenset(
    {
        ("cloudidentity", "v1"),
        ("cloudidentity", "v1beta1"),
        ("chromemanagement", "v1"),
        ("admin", "contacts_v1"),
    }
)
# Pinned APIs without an installed document, authenticated as an installed API
# on the same endpoint before the client is rebuilt.
TRANSPORT_APIS = {("admin", "contacts_v1"): ("admin", "directory_v1")}


@lru_cache(maxsize=None)
def pinned_document(service: str, version: str) -> str | None:
    """The pinned discovery document for an API, or None if it uses the static one."""
    if (service, version) not in PINNED_APIS:
        return None
    return (DISCOVERY_DIR / f"{service}_{version}.json").read_text(encoding="utf-8")


def pinned_client(client, service: str, version: str):
    """Return ``client`` rebuilt from the pinned document when the API has one.

    The rebuilt client shares ``client``'s transport, so close only one of them.
    """
    document = pinned_document(service, version)
    if document is None:
        return client
    return build_from_document(
        document,
        http=client._http,
        client_options={"api_endpoint": client._baseUrl},
    )


def organization_client(client):
    """A Cloud Resource Manager v3 client from the installed static document on
    ``client``'s transport, so the organization lookup uses the same verified
    account and token as the operation. Close only ``client``."""
    return build("cloudresourcemanager", "v3", http=client._http, static_discovery=True)
