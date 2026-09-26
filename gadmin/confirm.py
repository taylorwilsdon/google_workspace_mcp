"""One-use, short-lived confirmations for high-impact admin operations.

A proposal records the exact actor, customer, target, operation, and payload.
Only a hash of the confirmation token, bound to a digest of those fields, is
stored, so neither a leaked record nor an edited payload yields a usable token.
Records live on disk (directory 0700, files 0600) rather than in process memory,
and are claimed by an atomic rename so a token is accepted at most once even
under concurrent calls. Any failed attempt consumes the proposal.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from auth.google_auth import get_default_credentials_dir
from gadmin.guard import AdminContext
from gadmin.registry import (
    OperationSpec,
    classify_risk,
    registered_spec,
    validate_call,
)

DEFAULT_TTL_SECONDS = 300

_PROPOSAL_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class ConfirmationError(PermissionError):
    """The confirmation is unknown, used, expired, or does not match."""


@dataclass(frozen=True)
class Proposal:
    id: str
    operation_id: str
    actor_email: str
    customer_id: str
    target: str | None
    params: dict
    body: dict | None
    risk: str
    expires_at: float
    # The tool that issued the proposal and the actor's immutable user ID.
    purpose: str = ""
    actor_id: str = ""

    @property
    def digest(self) -> str:
        """Hash of every field, so editing any stored value voids the token."""
        fields = asdict(self) | {"actor_email": self.actor_email.casefold()}
        canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


def _token_hash(token: str, digest: str) -> str:
    return hashlib.sha256(f"{token}:{digest}".encode()).hexdigest()


class ConfirmationStore:
    def __init__(
        self,
        directory: Path | str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ):
        self.directory = Path(directory)
        self.ttl_seconds = ttl_seconds
        self.clock = clock

    def _ensure_directory(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)

    def _path(self, proposal_id: str) -> Path:
        if not isinstance(proposal_id, str) or not _PROPOSAL_ID.fullmatch(proposal_id):
            raise ConfirmationError("Unknown or already used confirmation.")
        return self.directory / f"{proposal_id}.json"

    def _prune_expired(self) -> None:
        now = self.clock()
        for path in self.directory.glob("*.json"):
            try:
                expires_at = json.loads(path.read_text())["proposal"]["expires_at"]
                if expires_at <= now:
                    path.unlink()
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def issue(self, proposal: Proposal) -> str:
        """Persist ``proposal`` and return its confirmation token."""
        self._ensure_directory()
        self._prune_expired()
        token = secrets.token_urlsafe(32)
        record = {
            "proposal": asdict(proposal),
            "token_hash": _token_hash(token, proposal.digest),
        }
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle)
            os.replace(tmp, self._path(proposal.id))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return token

    def consume(
        self, proposal_id: str, token: str, actor_email: str, customer_id: str
    ) -> Proposal:
        """Return the proposal if ``token`` confirms it for this actor and customer."""
        path = self._path(proposal_id)
        claimed = path.with_name(f"{path.name}.{secrets.token_hex(8)}.claimed")
        try:
            os.rename(path, claimed)
        except OSError:
            raise ConfirmationError("Unknown or already used confirmation.") from None
        try:
            record = json.loads(claimed.read_text(encoding="utf-8"))
            proposal = Proposal(**record["proposal"])
            expected_hash = record["token_hash"]
        except (OSError, ValueError, KeyError, TypeError):
            raise ConfirmationError("The confirmation record is unreadable.") from None
        finally:
            claimed.unlink(missing_ok=True)

        if proposal.expires_at <= self.clock():
            raise ConfirmationError("The confirmation has expired; propose again.")
        if (
            proposal.actor_email.casefold() != actor_email.casefold()
            or proposal.customer_id != customer_id
        ):
            raise ConfirmationError("The confirmation belongs to another admin.")
        if not isinstance(token, str) or not hmac.compare_digest(
            _token_hash(token, proposal.digest), expected_hash
        ):
            raise ConfirmationError("The confirmation token does not match.")
        return proposal


def default_store() -> ConfirmationStore:
    return ConfirmationStore(
        Path(get_default_credentials_dir()) / "admin-state" / "confirmations"
    )


def propose_operation(
    context: AdminContext,
    spec: OperationSpec | str,
    params: dict | None,
    body: dict | None = None,
    store: ConfirmationStore | None = None,
    purpose: str = "",
    target: str | None = None,
) -> tuple[Proposal, str]:
    """Validate and persist a proposal; return it with its confirmation token.

    ``target`` defaults to the operation's target parameter; ``purpose`` names the
    tool that may confirm it.
    """
    store = store or default_store()
    spec = registered_spec(spec)
    params, body = validate_call(spec, params, body)
    if not context.actor_email or not context.customer_id:
        raise ConfirmationError("The admin context is incomplete.")
    if target is None and spec.target_param:
        target = params.get(spec.target_param)
    proposal = Proposal(
        id=secrets.token_urlsafe(18),
        operation_id=spec.id,
        actor_email=context.actor_email,
        customer_id=context.customer_id,
        target=target,
        params=params,
        body=body,
        risk=classify_risk(spec, body),
        expires_at=store.clock() + store.ttl_seconds,
        purpose=purpose,
        actor_id=context.actor_id,
    )
    return proposal, store.issue(proposal)


def confirm_operation(
    proposal_id: str,
    token: str,
    context: AdminContext,
    store: ConfirmationStore | None = None,
    purpose: str = "",
) -> Proposal:
    """Consume the confirmation and return the exact proposal it approved."""
    store = store or default_store()
    proposal = store.consume(
        proposal_id, token, context.actor_email, context.customer_id
    )
    if proposal.actor_id != context.actor_id:
        raise ConfirmationError("The confirmation belongs to another admin.")
    if proposal.purpose != purpose:
        raise ConfirmationError("The confirmation was issued by another tool.")
    return proposal
