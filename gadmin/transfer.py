"""Typed Data Transfer and Licensing reads used by user offboarding.

Every call goes through the pinned registry with a client that gadmin.auth bound
to the verified admin. Google's state is reported as Google gives it: a transfer
is complete only when Google says so, and a license product that could not be
checked is listed rather than treated as holding no licenses. Calls are
synchronous; run them with ``asyncio.to_thread``.
"""

from dataclasses import dataclass
from typing import Iterator, Sequence

from gadmin.execute import AdminApiError, execute_operation

# Product IDs that carry per-user licenses, from Google's "Products and SKUs"
# table (https://developers.google.com/workspace/admin/licensing/v1/how-tos/products,
# last updated 2026-09-18, checked 2026-09-25). Chrome device licenses are not
# per-user and are left out. Licensing offers no "licenses for one user" method,
# so each product is listed and filtered by the user's primary email.
USER_LICENSE_PRODUCTS = (
    "Google-Apps",
    "101068",
    "101047",
    "101034",
    "101031",
    "101037",
    "101038",
    "101054",
    "Google-Vault",
    "101001",
    "101005",
    "101052",
    "101050",
    "101049",
    "101036",
    "101043",
    "101033",
    "101039",
    "101040",
    "101035",
)

# Google does not publish the full set of transfer status codes. Only these two
# are acted on; any other or missing value means the transfer is still pending.
_COMPLETED = "completed"
_FAILED = "failed"


@dataclass(frozen=True)
class TransferApplication:
    id: str
    name: str
    params: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class TransferState:
    id: str
    state: str  # "completed", "failed", or "pending"
    google_status: str
    request: dict | None = None


@dataclass(frozen=True)
class LicenseAssignment:
    product_id: str
    sku_id: str
    sku_name: str


@dataclass(frozen=True)
class LicenseLookup:
    assignments: tuple[LicenseAssignment, ...]
    # (product ID, error category) for each product Google did not answer for.
    unchecked_products: tuple[tuple[str, str], ...]

    @property
    def complete(self) -> bool:
        return not self.unchecked_products


def _list_all(service, operation_id: str, params: dict, items_key: str) -> Iterator:
    page_token = None
    while True:
        page_params = {**params, "pageToken": page_token} if page_token else params
        page = execute_operation(service, operation_id, page_params)
        yield from page.get(items_key, [])
        page_token = page.get("nextPageToken")
        if not page_token:
            return


def list_transfer_applications(
    service, customer_id: str
) -> tuple[TransferApplication, ...]:
    """Applications whose data Google can transfer for this customer."""
    return tuple(
        TransferApplication(
            id=str(app.get("id", "")),
            name=app.get("name", ""),
            params=tuple(
                (param.get("key", ""), tuple(param.get("value", ())))
                for param in app.get("transferParams", [])
            ),
        )
        for app in _list_all(
            service,
            "datatransfer.applications.list",
            {"customerId": customer_id},
            "applications",
        )
    )


def choose_transfer_applications(
    available: Sequence[TransferApplication], requested: Sequence[str] | None
) -> tuple[tuple[TransferApplication, ...], tuple[str, ...]]:
    """Return (supported, unsupported) for ``requested`` application names or
    IDs; None requests every application Google lists."""
    if requested is None:
        return tuple(available), ()
    chosen, unsupported = [], []
    for wanted in requested:
        match = next(
            (
                app
                for app in available
                if wanted == app.id or wanted.casefold() == app.name.casefold()
            ),
            None,
        )
        if match is None:
            unsupported.append(wanted)
        elif match not in chosen:
            chosen.append(match)
    return tuple(chosen), tuple(unsupported)


def transfer_request(
    old_owner_id: str,
    new_owner_id: str,
    applications: Sequence[TransferApplication],
) -> dict:
    """Request body for a transfer of every listed parameter value, which for
    Drive means both private and shared files."""
    return {
        "oldOwnerUserId": old_owner_id,
        "newOwnerUserId": new_owner_id,
        "applicationDataTransfers": [
            {
                "applicationId": app.id,
                "applicationTransferParams": [
                    {"key": key, "value": list(values)} for key, values in app.params
                ],
            }
            for app in applications
        ],
    }


def _transfer_state(transfer: dict) -> TransferState:
    overall = transfer.get("overallTransferStatusCode") or ""
    per_application = [
        app.get("applicationTransferStatus")
        for app in transfer.get("applicationDataTransfers", [])
        if isinstance(app, dict)
    ]
    if overall == _FAILED or _FAILED in per_application:
        state = "failed"
    elif (
        overall == _COMPLETED
        and per_application
        and all(status == _COMPLETED for status in per_application)
    ):
        state = "completed"
    else:
        state = "pending"
    request = {
        "oldOwnerUserId": transfer.get("oldOwnerUserId"),
        "newOwnerUserId": transfer.get("newOwnerUserId"),
        "applicationDataTransfers": transfer.get("applicationDataTransfers"),
    }
    return TransferState(
        id=transfer.get("id", ""),
        state=state,
        google_status=overall,
        request=request,
    )


def read_transfer(service, transfer_id: str) -> TransferState:
    return _transfer_state(
        execute_operation(
            service, "datatransfer.transfers.get", {"dataTransferId": transfer_id}
        )
    )


def find_transfers(
    service, customer_id: str, old_owner_id: str, new_owner_id: str
) -> tuple[TransferState, ...]:
    """Transfers Google holds from ``old_owner_id`` to ``new_owner_id``."""
    params = {
        "customerId": customer_id,
        "oldOwnerUserId": old_owner_id,
        "newOwnerUserId": new_owner_id,
    }
    return tuple(
        _transfer_state(transfer)
        for transfer in _list_all(
            service, "datatransfer.transfers.list", params, "dataTransfers"
        )
    )


def find_user_licenses(service, customer_id: str, user_email: str) -> LicenseLookup:
    """Licenses assigned to ``user_email`` across the pinned products."""
    assignments, unchecked = [], []
    for product_id in USER_LICENSE_PRODUCTS:
        try:
            items = list(
                _list_all(
                    service,
                    "licensing.licenseAssignments.listForProduct",
                    {"productId": product_id, "customerId": customer_id},
                    "items",
                )
            )
        except AdminApiError as exc:
            unchecked.append((product_id, exc.category))
            continue
        assignments += [
            LicenseAssignment(
                product_id=item.get("productId", product_id),
                sku_id=item.get("skuId", ""),
                sku_name=item.get("skuName", ""),
            )
            for item in items
            if str(item.get("userId", "")).casefold() == user_email.casefold()
        ]
    return LicenseLookup(tuple(assignments), tuple(unchecked))
