"""High-impact proposals need a one-use, short-lived confirmation token tied to
the exact actor, customer, target, operation, and payload. Audit records keep
the facts of an action and drop tokens, secrets, and payload values."""

import json
import stat
import threading

import pytest

from gadmin.audit import AuditSink
from gadmin.confirm import (
    ConfirmationError,
    ConfirmationStore,
    confirm_operation,
    propose_operation,
)
from gadmin.guard import AdminContext
from gadmin.registry import InvalidOperationInput, get_operation

ADMIN = "admin@op.example"
SUSPEND = get_operation("directory.users.update")


class Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def confirm_store(tmp_path, clock):
    return ConfirmationStore(tmp_path / "confirmations", ttl_seconds=300, clock=clock)


@pytest.fixture
def context():
    return AdminContext(actor_email=ADMIN, customer_id="C01", actor_id="U_ADMIN")


@pytest.fixture
def issued(confirm_store, context):
    return propose_operation(
        context,
        SUSPEND,
        {"userKey": "staff@op.example"},
        {"suspended": True},
        store=confirm_store,
    )


@pytest.fixture
def proposal(issued):
    return issued[0]


def test_proposal_describes_exact_action(issued, clock):
    proposal, token = issued

    assert (proposal.operation_id, proposal.target, proposal.risk) == (
        "directory.users.update",
        "staff@op.example",
        "destructive",
    )
    assert (proposal.actor_email, proposal.customer_id) == (ADMIN, "C01")
    assert proposal.body == {"suspended": True}
    assert proposal.expires_at == clock.now + 300
    assert len(token) >= 32 and token not in proposal.id


def test_confirmation_is_one_use(confirm_store, proposal):
    token = confirm_store.issue(proposal)
    assert confirm_store.consume(proposal.id, token, proposal.actor_email, "C01")
    with pytest.raises(ConfirmationError):
        confirm_store.consume(proposal.id, token, proposal.actor_email, "C01")


def test_confirm_operation_returns_the_stored_proposal(issued, confirm_store, context):
    proposal, token = issued

    confirmed = confirm_operation(proposal.id, token, context, store=confirm_store)

    assert confirmed == proposal
    with pytest.raises(ConfirmationError):
        confirm_operation(proposal.id, token, context, store=confirm_store)


def test_expired_confirmation_fails(issued, confirm_store, context, clock):
    proposal, token = issued
    clock.now += 301

    with pytest.raises(ConfirmationError, match="expired"):
        confirm_operation(proposal.id, token, context, store=confirm_store)


def test_wrong_token_fails_and_burns_proposal(issued, confirm_store, context):
    proposal, token = issued

    with pytest.raises(ConfirmationError):
        confirm_operation(proposal.id, "x" * 43, context, store=confirm_store)
    with pytest.raises(ConfirmationError):
        confirm_operation(proposal.id, token, context, store=confirm_store)


def test_token_is_not_transferable_between_proposals(confirm_store, context):
    first, first_token = propose_operation(
        context, SUSPEND, {"userKey": "a@op.example"}, {"suspended": True},
        store=confirm_store,
    )  # fmt: skip
    second, _ = propose_operation(
        context, SUSPEND, {"userKey": "b@op.example"}, {"suspended": True},
        store=confirm_store,
    )  # fmt: skip

    with pytest.raises(ConfirmationError):
        confirm_operation(second.id, first_token, context, store=confirm_store)


def test_changed_payload_fails(issued, confirm_store, context):
    proposal, token = issued
    path = confirm_store.directory / f"{proposal.id}.json"
    record = json.loads(path.read_text())
    record["proposal"]["params"]["userKey"] = "owner@op.example"
    path.write_text(json.dumps(record))

    with pytest.raises(ConfirmationError):
        confirm_operation(proposal.id, token, context, store=confirm_store)


@pytest.mark.parametrize(
    "other",
    [
        AdminContext(actor_email="other@op.example", customer_id="C01"),
        AdminContext(actor_email=ADMIN, customer_id="C02"),
    ],
    ids=["other-actor", "other-customer"],
)
def test_confirmation_is_bound_to_actor_and_customer(issued, confirm_store, other):
    proposal, token = issued

    with pytest.raises(ConfirmationError):
        confirm_operation(proposal.id, token, other, store=confirm_store)


def test_confirmation_is_bound_to_the_actors_immutable_id(issued, confirm_store):
    proposal, token = issued
    recreated = AdminContext(actor_email=ADMIN, customer_id="C01", actor_id="U_NEW")

    assert proposal.actor_id == "U_ADMIN"
    with pytest.raises(ConfirmationError, match="another admin"):
        confirm_operation(proposal.id, token, recreated, store=confirm_store)


def test_confirmation_is_bound_to_the_proposing_tool(confirm_store, context):
    proposal, token = propose_operation(
        context,
        SUSPEND,
        {"userKey": "staff@op.example"},
        {"suspended": True},
        store=confirm_store,
        purpose="admin_operation",
        target="U_STAFF",
    )

    assert proposal.target == "U_STAFF"
    with pytest.raises(ConfirmationError, match="another tool"):
        confirm_operation(proposal.id, token, context, store=confirm_store)


def test_concurrent_consume_succeeds_once(issued, confirm_store, context):
    proposal, token = issued
    results = []

    def attempt():
        try:
            confirm_operation(proposal.id, token, context, store=confirm_store)
            results.append("ok")
        except ConfirmationError:
            results.append("denied")

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["denied"] * 7 + ["ok"]


@pytest.mark.parametrize("proposal_id", ["../escape", "a/b", "", "x" * 200])
def test_malformed_proposal_id_is_rejected(confirm_store, context, proposal_id):
    with pytest.raises(ConfirmationError):
        confirm_operation(proposal_id, "t" * 43, context, store=confirm_store)


def test_proposal_is_validated_before_storage(confirm_store, context):
    with pytest.raises(InvalidOperationInput):
        propose_operation(
            context,
            SUSPEND,
            {"userKey": "https://evil.example"},
            {"suspended": True},
            store=confirm_store,
        )
    assert not list(confirm_store.directory.glob("*"))


def test_incomplete_context_cannot_propose(confirm_store):
    with pytest.raises(ConfirmationError):
        propose_operation(
            AdminContext(actor_email=ADMIN, customer_id=""),
            SUSPEND,
            {"userKey": "staff@op.example"},
            {"suspended": True},
            store=confirm_store,
        )


def test_store_is_private_and_holds_no_plain_token(issued, confirm_store):
    proposal, token = issued
    path = confirm_store.directory / f"{proposal.id}.json"

    assert stat.S_IMODE(confirm_store.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert token not in path.read_text()


def test_expired_proposals_are_pruned(confirm_store, context, clock, issued):
    stale, _ = issued
    clock.now += 301

    propose_operation(
        context, SUSPEND, {"userKey": "a@op.example"}, {"suspended": True},
        store=confirm_store,
    )  # fmt: skip

    assert not (confirm_store.directory / f"{stale.id}.json").exists()


# --- Audit ------------------------------------------------------------------------


def test_audit_records_facts_without_secrets(tmp_path, issued):
    proposal, token = issued
    sink = AuditSink(tmp_path / "audit" / "admin-audit.jsonl")

    sink.record(
        {
            "operation_id": "directory.users.update",
            "actor": ADMIN,
            "customer": "C01",
            "target": "staff@op.example",
            "method": "users.update",
            "outcome": "success",
            "request_id": "req-123",
            "proposal_id": proposal.id,
            "confirmation_token": token,
            "access_token": "ya29.secret-access-token",
            "params": {"userKey": "staff@op.example", "pageToken": "page-secret"},
            "body": {"suspended": True, "name": {"givenName": "Private"}},
            "vault_export": "privileged vault content",
        }
    )

    path = tmp_path / "audit" / "admin-audit.jsonl"
    text = path.read_text()
    (record,) = [json.loads(line) for line in text.splitlines()]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for secret in (token, "ya29", "page-secret", "Private", "vault content"):
        assert secret not in text
    assert record["body_fields"] == ["name", "suspended"]
    assert record["param_names"] == ["pageToken", "userKey"]
    assert {
        k: record[k]
        for k in (
            "operation_id",
            "actor",
            "customer",
            "target",
            "outcome",
            "request_id",
        )
    } == {
        "operation_id": "directory.users.update",
        "actor": ADMIN,
        "customer": "C01",
        "target": "staff@op.example",
        "outcome": "success",
        "request_id": "req-123",
    }
    assert isinstance(record["time"], str)


def test_audit_appends_one_line_per_event(tmp_path):
    sink = AuditSink(tmp_path / "admin-audit.jsonl")
    sink.record({"operation_id": "a", "outcome": "denied"})
    sink.record({"operation_id": "b", "outcome": "success"})

    lines = (tmp_path / "admin-audit.jsonl").read_text().splitlines()
    assert [json.loads(line)["operation_id"] for line in lines] == ["a", "b"]
