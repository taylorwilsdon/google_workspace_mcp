"""Cloud Identity clients come from the pinned discovery documents.

The installed google-api-python-client's static Cloud Identity documents are
older than the pinned revision and lack methods the registry covers. The client
authenticated through the verified path is rebuilt from the pinned document on
the same transport, and these tests show the rebuilt client puts the expected
verb and path on the wire without fetching discovery."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from googleapiclient.discovery import build
from googleapiclient.discovery_cache import get_static_doc

import gadmin.auth as admin_auth
from gadmin.auth import get_admin_service
from gadmin.client import pinned_client, pinned_document
from tests.gadmin.fake_http import RoutingHttp

ADMIN = "admin@op.example"
# (method ID, kwargs, verb, path) for each v1 method the static document lacks.
MISSING_FROM_STATIC = [
    (
        "cloudidentity.allowlistedDomains.create",
        {"body": {"domain": "partner.example"}},
        "POST",
        "/v1/allowlistedDomains",
    ),
    (
        "cloudidentity.allowlistedDomains.delete",
        {"name": "allowlistedDomains/d1"},
        "DELETE",
        "/v1/allowlistedDomains/d1",
    ),
    (
        "cloudidentity.allowlistedDomains.get",
        {"name": "allowlistedDomains/d1"},
        "GET",
        "/v1/allowlistedDomains/d1",
    ),
    ("cloudidentity.allowlistedDomains.list", {}, "GET", "/v1/allowlistedDomains"),
    (
        "cloudidentity.policies.create",
        {"body": {"customer": "customers/C01"}},
        "POST",
        "/v1/policies",
    ),
    (
        "cloudidentity.policies.delete",
        {"name": "policies/p1"},
        "DELETE",
        "/v1/policies/p1",
    ),
    (
        "cloudidentity.policies.patch",
        {"name": "policies/p1", "body": {"setting": {"type": "t"}}},
        "PATCH",
        "/v1/policies/p1",
    ),
]


def _methods(resources: dict) -> set[str]:
    found = set()
    for resource in resources.values():
        found |= {m["id"] for m in resource.get("methods", {}).values()}
        found |= _methods(resource.get("resources", {}))
    return found


def _method(client, operation_id: str):
    *resources, method = operation_id.split(".")[1:]
    for name in resources:
        client = getattr(client, name)()
    return getattr(client, method)


@pytest.mark.parametrize(("version", "missing"), [("v1", 7), ("v1beta1", 4)])
def test_installed_static_documents_lack_methods_the_pinned_documents_have(
    version, missing
):
    static = _methods(json.loads(get_static_doc("cloudidentity", version))["resources"])
    pinned_doc = json.loads(pinned_document("cloudidentity", version))

    assert pinned_doc["revision"] == "20260923"
    assert static < _methods(pinned_doc["resources"])
    assert len(_methods(pinned_doc["resources"]) - static) == missing


def test_other_apis_keep_their_installed_client():
    client = object()

    assert pinned_document("vault", "v1") is None
    assert pinned_client(client, "vault", "v1") is client


@pytest.mark.parametrize(
    ("operation_id", "kwargs", "verb", "path"),
    MISSING_FROM_STATIC,
    ids=[m[0] for m in MISSING_FROM_STATIC],
)
def test_pinned_client_invokes_methods_the_static_client_lacks(
    operation_id, kwargs, verb, path
):
    http = RoutingHttp({(verb, path): {"name": "operations/o1", "done": True}})
    static = build("cloudidentity", "v1", http=http)
    with pytest.raises(AttributeError):
        _method(static, operation_id)

    client = pinned_client(static, "cloudidentity", "v1")
    response = _method(client, operation_id)(**kwargs).execute()

    assert response == {"name": "operations/o1", "done": True}
    assert http.requests == [(verb, path, {}, kwargs.get("body"))]
    # The rebuilt client reuses the verified transport and endpoint.
    assert client._http is static._http
    assert client._baseUrl == static._baseUrl


def test_pinned_beta_client_reaches_org_unit_memberships():
    path = "/v1beta1/orgUnits/o1/memberships/m1:move"
    http = RoutingHttp({("POST", path): {"name": "operations/o1"}})
    client = pinned_client(
        build("cloudidentity", "v1beta1", http=http), "cloudidentity", "v1beta1"
    )

    body = {"customer": "customers/C01", "destinationOrgUnit": "orgUnits/o2"}
    client.orgUnits().memberships().move(
        name="orgUnits/o1/memberships/m1", body=body
    ).execute()

    assert http.requests == [("POST", path, {}, body)]


@pytest.mark.asyncio
async def test_get_admin_service_rebuilds_the_verified_client_from_the_pinned_doc(
    monkeypatch,
):
    from gadmin.registry import get_operation

    monkeypatch.setattr(admin_auth, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(admin_auth, "is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(admin_auth, "_current_session_id", lambda: None)
    http = RoutingHttp({("GET", "/v1/allowlistedDomains"): {"allowlistedDomains": []}})
    verified = build("cloudidentity", "v1", http=http)
    fake = SimpleNamespace(mock=AsyncMock(return_value=(verified, ADMIN)))
    monkeypatch.setattr(admin_auth, "_authenticate_service", fake.mock)

    client = await get_admin_service(
        ADMIN, get_operation("cloudidentity.allowlistedDomains.list"), None
    )
    client.allowlistedDomains().list().execute()

    assert fake.mock.await_args.kwargs["verify_account"] is True
    assert client is not verified and client._http is http
    assert http.requests == [("GET", "/v1/allowlistedDomains", {}, None)]
