"""Append-only, redacted record of admin operations.

Only allow-listed facts are written: who acted, in which customer, on what
target, with which operation, and the outcome. Parameter and body values are
replaced by their field names, and anything else in the event, such as tokens,
credentials, or Vault content, is dropped rather than filtered by pattern.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

_RECORDED_FIELDS = (
    "operation_id",
    "method",
    "actor",
    "customer",
    "target",
    "outcome",
    "status",
    "category",
    "request_id",
    "proposal_id",
    "workflow_id",
)


def redact_event(event: Mapping) -> dict:
    record = {"time": datetime.now(timezone.utc).isoformat()}
    for key in _RECORDED_FIELDS:
        value = event.get(key)
        if isinstance(value, (str, int, bool)):
            record[key] = value
    for source, name in (("params", "param_names"), ("body", "body_fields")):
        if isinstance(event.get(source), Mapping):
            record[name] = sorted(event[source])
    return record


class AuditSink:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def record(self, event: Mapping) -> dict:
        """Append the redacted event as one JSON line and return what was written."""
        record = redact_event(event)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return record
