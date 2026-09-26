"""Data Transfer and Licensing are separate opt-in services with pinned, typed
operations, bound to the same verified admin and customer as Directory."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import auth.permissions as permissions
import auth.scopes as scopes
import gadmin.auth as admin_auth
from auth.scopes import (
    ADMIN_DATATRANSFER_READONLY_SCOPE,
    ADMIN_DATATRANSFER_SCOPE,
    LICENSING_SCOPE,
    get_scopes_for_tools,
)
from gadmin.auth import (
    AdminAuthenticationError,
    AdminPermissionError,
    GUARD_SCOPES,
    assert_admin_permission,
    get_admin_service,
)
from gadmin.execute import AdminApiError, execute_operation
from gadmin.guard import AdminBoundaryError, AdminContext, guard_target
from gadmin.offboarding import (
    USER_LICENSE_PRODUCTS,
    TransferApplication,
    choose_transfer_applications,
    find_transfers,
    find_user_licenses,
    list_transfer_applications,
    read_transfer,
    transfer_request,
)
from gadmin.registry import InvalidOperationInput, get_operation, iter_operations
from gadmin.registry import validate_call
from tests.gadmin.fake_directory import FakeDirectory, http_error
from tests.gadmin.fake_google import FakeGoogleApi

ADMIN = "admin@op.example"
LEAVER = "leaver@op.example"
CONTEXT = AdminContext(actor_email=ADMIN, customer_id="C01", actor_id="U_ADMIN")
DRIVE = {
    "id": "55656082996",
    "name": "Drive and Docs",
    "transferParams": [{"key": "PRIVACY_LEVEL", "value": ["PRIVATE", "SHARED"]}],
}
CALENDAR = {
    "id": "435070579839",
    "name": "Calendar",
    "transferParams": [{"key": "RELEASE_RESOURCES", "value": ["TRUE"]}],
}
TRANSFER_BODY = {
    "oldOwnerUserId": "U_LEAVER",
    "newOwnerUserId": "U_MANAGER",
    "applicationDataTransfers": [
        {
            "applicationId": "55656082996",
            "applicationTransferParams": [
                {"key": "PRIVACY_LEVEL", "value": ["PRIVATE", "SHARED"]}
            ],
        }
    ],
}


@pytest.fixture(autouse=True)
def _reset_selection(monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", None)
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", False)
    monkeypatch.setattr(permissions, "_PERMISSIONS", None)


def _pages(items_key: str, pages: list[list[dict]]):
    """Handler that serves ``pages`` in order, linked by nextPageToken."""

    def handler(pageToken=None, **kwargs):
        index = int(pageToken or 0)
        response = {items_key: pages[index]}
        if index + 1 < len(pages):
            response["nextPageToken"] = str(index + 1)
        return response

    return handler


# --- Pinned, typed operations ------------------------------------------------------


def test_transfer_and_license_are_different_service_clients():
    transfer = get_operation("datatransfer.transfers.insert")
    license_delete = get_operation("licensing.licenseAssignments.delete")

    assert (transfer.service, transfer.version) == ("admin", "datatransfer_v1")
    assert (license_delete.service, license_delete.version) == ("licensing", "v1")


def test_transfer_and_licensing_operations_have_pinned_risks():
    risks = {
        spec.id: spec.risk
        for spec in iter_operations()
        if (spec.service, spec.version)
        in {("admin", "datatransfer_v1"), ("licensing", "v1")}
    }

    assert risks == {
        "datatransfer.applications.list": "read",
        "datatransfer.applications.get": "read",
        "datatransfer.transfers.get": "read",
        "datatransfer.transfers.list": "read",
        "datatransfer.transfers.insert": "manage",
        "licensing.licenseAssignments.get": "read",
        "licensing.licenseAssignments.listForProduct": "read",
        "licensing.licenseAssignments.listForProductAndSku": "read",
        "licensing.licenseAssignments.insert": "manage",
        "licensing.licenseAssignments.patch": "manage",
        "licensing.licenseAssignments.delete": "destructive",
    }


def test_transfer_body_is_validated_all_the_way_down():
    spec = get_operation("datatransfer.transfers.insert")

    _, body = validate_call(spec, None, TRANSFER_BODY)

    assert body == TRANSFER_BODY


def _with_first_transfer(**changes) -> dict:
    app = {**TRANSFER_BODY["applicationDataTransfers"][0], **changes}
    return {**TRANSFER_BODY, "applicationDataTransfers": [app]}


@pytest.mark.parametrize(
    "body",
    [
        {**TRANSFER_BODY, "overallTransferStatusCode": "completed"},  # read-only
        {**TRANSFER_BODY, "applicationDataTransfers": {"applicationId": "1"}},
        {**TRANSFER_BODY, "applicationDataTransfers": ["55656082996"]},
        _with_first_transfer(applicationTransferStatus="completed"),
        _with_first_transfer(applicationId=55656082996),
        _with_first_transfer(applicationTransferParams=[{"key": "K", "value": "V"}]),
        _with_first_transfer(applicationTransferParams=[{"key": "K", "value": [1]}]),
        _with_first_transfer(
            applicationTransferParams=[{"key": "K", "value": ["https://evil.test"]}]
        ),
        _with_first_transfer(
            applicationTransferParams=[{"key": "K", "value": ["V"], "extra": "x"}]
        ),
    ],
)
def test_malformed_transfer_body_is_rejected(body):
    with pytest.raises(InvalidOperationInput):
        validate_call(get_operation("datatransfer.transfers.insert"), None, body)


def test_transfer_submission_sends_exactly_the_validated_body():
    api = FakeGoogleApi(
        "datatransfer",
        {
            "datatransfer.transfers.insert": lambda body: {
                **body,
                "id": "T1",
                "overallTransferStatusCode": "new",
                "etag": '"secret"',
            }
        },
    )

    result = execute_operation(
        api, "datatransfer.transfers.insert", None, TRANSFER_BODY
    )

    assert api.calls == [("datatransfer.transfers.insert", {"body": TRANSFER_BODY})]
    assert result["id"] == "T1" and "etag" not in result


# --- Transfer applications and status ----------------------------------------------


def test_allowed_applications_come_from_google():
    api = FakeGoogleApi(
        "datatransfer",
        {
            "datatransfer.applications.list": _pages(
                "applications", [[DRIVE], [CALENDAR]]
            )
        },
    )

    available = list_transfer_applications(api, "C01")

    assert [call[1].get("customerId") for call in api.calls] == ["C01", "C01"]
    assert available == (
        TransferApplication(
            id="55656082996",
            name="Drive and Docs",
            params=(("PRIVACY_LEVEL", ("PRIVATE", "SHARED")),),
        ),
        TransferApplication(
            id="435070579839",
            name="Calendar",
            params=(("RELEASE_RESOURCES", ("TRUE",)),),
        ),
    )


def test_unsupported_applications_are_reported_separately():
    available = (
        TransferApplication("55656082996", "Drive and Docs"),
        TransferApplication("435070579839", "Calendar"),
    )

    chosen, unsupported = choose_transfer_applications(
        available, ["drive and docs", "435070579839", "Looker Studio"]
    )
    everything, none_missing = choose_transfer_applications(available, None)

    assert [app.name for app in chosen] == ["Drive and Docs", "Calendar"]
    assert unsupported == ("Looker Studio",)
    assert everything == available and none_missing == ()


def test_transfer_request_uses_googles_listed_parameters():
    drive = TransferApplication(
        "55656082996", "Drive and Docs", (("PRIVACY_LEVEL", ("PRIVATE", "SHARED")),)
    )

    assert transfer_request("U_LEAVER", "U_MANAGER", [drive]) == TRANSFER_BODY


@pytest.mark.parametrize(
    ("overall", "per_application", "state"),
    [
        ("completed", "completed", "completed"),
        ("inProgress", "inProgress", "pending"),
        ("new", None, "pending"),
        ("failed", "failed", "failed"),
        ("completed", "failed", "failed"),
        ("completed", "inProgress", "pending"),
        ("completed", None, "pending"),
        ("SOMETHING_NEW", None, "pending"),
        (None, None, "pending"),
    ],
)
def test_transfer_state_is_googles_reported_state(overall, per_application, state):
    transfer = {"id": "T1", "oldOwnerUserId": "U_LEAVER", "newOwnerUserId": "U_M"}
    if overall is not None:
        transfer["overallTransferStatusCode"] = overall
    app = {"applicationId": "55656082996"}
    if per_application is not None:
        app["applicationTransferStatus"] = per_application
    transfer["applicationDataTransfers"] = [app]
    api = FakeGoogleApi(
        "datatransfer", {"datatransfer.transfers.get": lambda dataTransferId: transfer}
    )

    result = read_transfer(api, "T1")

    assert api.calls == [("datatransfer.transfers.get", {"dataTransferId": "T1"})]
    assert (result.id, result.state) == ("T1", state)
    assert result.google_status == (overall or "")


def test_find_transfers_reads_customer_transfers_between_the_two_users():
    transfers = [
        {
            "id": "T1",
            "overallTransferStatusCode": "completed",
            "applicationDataTransfers": [
                {
                    "applicationId": "55656082996",
                    "applicationTransferStatus": "completed",
                }
            ],
        },
        {"id": "T2", "overallTransferStatusCode": "inProgress"},
    ]
    api = FakeGoogleApi(
        "datatransfer",
        {
            "datatransfer.transfers.list": _pages(
                "dataTransfers", [[t] for t in transfers]
            )
        },
    )

    found = find_transfers(api, "C01", "U_LEAVER", "U_MANAGER")

    assert [(t.id, t.state) for t in found] == [("T1", "completed"), ("T2", "pending")]
    assert api.calls[0] == (
        "datatransfer.transfers.list",
        {
            "customerId": "C01",
            "oldOwnerUserId": "U_LEAVER",
            "newOwnerUserId": "U_MANAGER",
        },
    )


# --- Licenses ------------------------------------------------------------------------


def _license(product: str, sku: str, user: str) -> dict:
    return {
        "productId": product,
        "skuId": sku,
        "skuName": f"{sku} name",
        "userId": user,
    }


def test_license_lookup_checks_every_pinned_product_in_the_customer():
    handlers = {
        "licensing.licenseAssignments.listForProduct": lambda productId, **kw: {
            "Google-Apps": _pages(
                "items",
                [
                    [_license("Google-Apps", "1010020028", "other@op.example")],
                    [_license("Google-Apps", "1010020028", LEAVER.upper())],
                ],
            ),
            "Google-Vault": _pages(
                "items", [[_license("Google-Vault", "Google-Vault", LEAVER)]]
            ),
        }.get(productId, _pages("items", [[]]))(**kw)
    }
    api = FakeGoogleApi("licensing", handlers)

    lookup = find_user_licenses(api, "C01", LEAVER)

    assert lookup.complete and lookup.unchecked_products == ()
    assert [(a.product_id, a.sku_id) for a in lookup.assignments] == [
        ("Google-Apps", "1010020028"),
        ("Google-Vault", "Google-Vault"),
    ]
    checked = [kw["productId"] for _, kw in api.calls if "pageToken" not in kw]
    assert checked == list(USER_LICENSE_PRODUCTS)
    assert all(kw["customerId"] == "C01" for _, kw in api.calls)


def test_partial_license_lookup_failure_is_reported_not_hidden():
    def handler(productId, customerId, **kw):
        if productId == "Google-Vault":
            raise http_error(503, "Backend Error")
        if productId == "101001":
            raise http_error(
                403, "Insufficient Permission", code="insufficientPermissions"
            )
        if productId == "Google-Apps":
            return {"items": [_license("Google-Apps", "1010020028", LEAVER)]}
        return {"items": []}

    api = FakeGoogleApi(
        "licensing", {"licensing.licenseAssignments.listForProduct": handler}
    )

    lookup = find_user_licenses(api, "C01", LEAVER)

    assert not lookup.complete
    assert lookup.unchecked_products == (
        ("Google-Vault", "api_error"),
        ("101001", "missing_scope"),
    )
    assert [a.product_id for a in lookup.assignments] == ["Google-Apps"]


def test_license_removal_dispatches_the_exact_assignment():
    api = FakeGoogleApi(
        "licensing", {"licensing.licenseAssignments.delete": lambda **kw: {}}
    )
    params = {"productId": "Google-Apps", "skuId": "1010020028", "userId": LEAVER}

    execute_operation(api, "licensing.licenseAssignments.delete", params)

    assert api.calls == [("licensing.licenseAssignments.delete", params)]
    with pytest.raises(InvalidOperationInput):
        execute_operation(
            api,
            "licensing.licenseAssignments.delete",
            {**params, "skuId": "../../users"},
        )
    assert len(api.calls) == 1


def test_missing_scope_on_transfer_is_classified():
    api = FakeGoogleApi(
        "datatransfer",
        {
            "datatransfer.transfers.insert": http_error(
                403, "Insufficient Permission", code="insufficientPermissions"
            )
        },
    )

    with pytest.raises(AdminApiError) as raised:
        execute_operation(api, "datatransfer.transfers.insert", None, TRANSFER_BODY)

    assert (raised.value.operation_id, raised.value.category) == (
        "datatransfer.transfers.insert",
        "missing_scope",
    )


# --- The same customer guard applies to transfer and license writes -----------------


@pytest.fixture
def directory():
    fake = FakeDirectory(customer_id="C01")
    fake.add_user(ADMIN, "U_ADMIN", super_admin=True)
    fake.add_user("second-admin@op.example", "U_ADMIN2", super_admin=True)
    fake.add_user(LEAVER, "U_LEAVER")
    fake.add_user("outsider@other.example", "U_OUT", customer_id="C_OTHER")
    return fake


def test_transfer_recipient_in_another_customer_is_refused(directory):
    with pytest.raises(AdminBoundaryError, match="another customer"):
        guard_target(
            CONTEXT,
            "outsider@other.example",
            "datatransfer.transfers.insert",
            directory,
        )


def test_license_removal_runs_destructive_target_checks(directory):
    with pytest.raises(AdminBoundaryError, match="own account"):
        guard_target(CONTEXT, ADMIN, "licensing.licenseAssignments.delete", directory)

    target = guard_target(
        CONTEXT, LEAVER, "licensing.licenseAssignments.delete", directory
    )
    assert (target.user_id, target.customer_id) == ("U_LEAVER", "C01")


# --- Opt-in selection, scopes, and permission levels --------------------------------


def test_default_launches_request_no_transfer_or_licensing_scope():
    for selection in (None, ["gmail", "calendar", "drive", "docs", "sheets"]):
        selected = set(get_scopes_for_tools(selection))
        assert not selected & {
            ADMIN_DATATRANSFER_SCOPE,
            ADMIN_DATATRANSFER_READONLY_SCOPE,
            LICENSING_SCOPE,
        }


def test_services_are_opt_in_and_request_their_own_scopes():
    import main

    for service in ("admin-datatransfer", "admin-licensing"):
        assert service in scopes.OPT_IN_SERVICES
        assert service in main.SERVICE_MODULES
        assert service not in main.DEFAULT_SERVICES

    assert {ADMIN_DATATRANSFER_SCOPE, ADMIN_DATATRANSFER_READONLY_SCOPE} <= set(
        get_scopes_for_tools(["admin-datatransfer"])
    )
    assert LICENSING_SCOPE in get_scopes_for_tools(["admin-licensing"])
    assert LICENSING_SCOPE not in get_scopes_for_tools(["admin-datatransfer"])


def test_read_only_launch_requests_no_write_scope(monkeypatch):
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)

    selected = set(get_scopes_for_tools(["admin-datatransfer", "admin-licensing"]))

    assert ADMIN_DATATRANSFER_READONLY_SCOPE in selected
    # Licensing has no read-only scope, so a read-only launch gets none.
    assert not selected & {ADMIN_DATATRANSFER_SCOPE, LICENSING_SCOPE}


def test_permission_levels_are_cumulative():
    level = permissions.get_scopes_for_permission

    assert level("admin-datatransfer", "readonly") == [
        ADMIN_DATATRANSFER_READONLY_SCOPE
    ]
    assert set(level("admin-datatransfer", "manage")) == {
        ADMIN_DATATRANSFER_READONLY_SCOPE,
        ADMIN_DATATRANSFER_SCOPE,
    }
    assert level("admin-licensing", "readonly") == []
    assert level("admin-licensing", "manage") == [LICENSING_SCOPE]
    assert level("admin-licensing", "destructive") == [LICENSING_SCOPE]


def test_operation_needs_its_own_service_selected(monkeypatch):
    insert = get_operation("datatransfer.transfers.insert")
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["admin-directory"])

    with pytest.raises(AdminPermissionError, match="admin-datatransfer"):
        assert_admin_permission(insert)

    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["admin-datatransfer"])
    assert_admin_permission(insert)
    with pytest.raises(AdminPermissionError, match="admin-licensing"):
        assert_admin_permission(get_operation("licensing.licenseAssignments.get"))


def test_read_only_mode_allows_transfer_reads_only(monkeypatch):
    monkeypatch.setattr(
        scopes, "_ENABLED_TOOLS", ["admin-datatransfer", "admin-licensing"]
    )
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)

    assert_admin_permission(get_operation("datatransfer.transfers.get"))
    for operation_id in (
        "datatransfer.transfers.insert",
        "licensing.licenseAssignments.listForProduct",
    ):
        with pytest.raises(AdminPermissionError):
            assert_admin_permission(get_operation(operation_id))


@pytest.mark.parametrize(
    ("service", "level", "operation_id", "allowed"),
    [
        ("admin-datatransfer", "readonly", "datatransfer.transfers.list", True),
        ("admin-datatransfer", "readonly", "datatransfer.transfers.insert", False),
        ("admin-datatransfer", "manage", "datatransfer.transfers.insert", True),
        ("admin-licensing", "readonly", "licensing.licenseAssignments.get", False),
        ("admin-licensing", "manage", "licensing.licenseAssignments.get", True),
        ("admin-licensing", "manage", "licensing.licenseAssignments.delete", False),
        ("admin-licensing", "destructive", "licensing.licenseAssignments.delete", True),
    ],
)
def test_permission_level_limits_each_service(
    monkeypatch, service, level, operation_id, allowed
):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", [service])
    monkeypatch.setattr(permissions, "_PERMISSIONS", {service: level})

    if allowed:
        assert_admin_permission(get_operation(operation_id))
    else:
        with pytest.raises(AdminPermissionError):
            assert_admin_permission(get_operation(operation_id))


# --- Clients bound to the verified admin ----------------------------------------------


@pytest.fixture
def authenticate(monkeypatch):
    monkeypatch.setattr(admin_auth, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(admin_auth, "is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(admin_auth, "_current_session_id", lambda: None)
    fake = SimpleNamespace(service=MagicMock(name="client"))
    fake.mock = AsyncMock(return_value=(fake.service, ADMIN))
    monkeypatch.setattr(admin_auth, "_authenticate_service", fake.mock)
    return fake


@pytest.mark.parametrize(
    ("operation_id", "pair", "scope"),
    [
        (
            "datatransfer.transfers.insert",
            ("admin", "datatransfer_v1"),
            ADMIN_DATATRANSFER_SCOPE,
        ),
        (
            "licensing.licenseAssignments.delete",
            ("licensing", "v1"),
            LICENSING_SCOPE,
        ),
    ],
)
@pytest.mark.asyncio
async def test_each_family_gets_its_own_bound_client(
    authenticate, operation_id, pair, scope
):
    service = await get_admin_service(ADMIN, get_operation(operation_id), None)

    args = authenticate.mock.await_args
    assert service is authenticate.service
    assert (args.args[1], args.args[2]) == pair
    assert args.args[5] == [scope]
    assert not set(args.args[5]) & set(GUARD_SCOPES)
    assert args.kwargs["verify_account"] is True


@pytest.mark.asyncio
async def test_client_for_another_account_is_rejected_and_closed(authenticate):
    authenticate.mock.return_value = (authenticate.service, "someone@op.example")

    with pytest.raises(AdminAuthenticationError):
        await get_admin_service(
            ADMIN, get_operation("licensing.licenseAssignments.delete"), None
        )
    authenticate.service.close.assert_called_once()
