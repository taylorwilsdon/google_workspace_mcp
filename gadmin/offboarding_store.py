"""Private, atomic workflow snapshots and cross-process advance locks."""

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from auth.google_auth import get_default_credentials_dir
from auth.oauth_config import is_stateless_mode

_WORKFLOW_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class WorkflowStoreError(ValueError):
    """A workflow is missing, invalid, or currently being advanced."""


class WorkflowStore:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)

    def _path(self, workflow_id: str) -> Path:
        if not isinstance(workflow_id, str) or not _WORKFLOW_ID.fullmatch(workflow_id):
            raise WorkflowStoreError("Unknown offboarding workflow.")
        return self.directory / f"{workflow_id}.json"

    def _ensure_directory(self) -> None:
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.directory, 0o700)

    def load(self, workflow_id: str) -> dict:
        try:
            record = json.loads(self._path(workflow_id).read_text(encoding="utf-8"))
            if record["workflow_id"] != workflow_id or not isinstance(
                record["steps"], list
            ):
                raise ValueError("Invalid workflow contents")
            return record
        except (OSError, ValueError, KeyError, TypeError):
            raise WorkflowStoreError(
                "Unknown or unreadable offboarding workflow."
            ) from None

    def save(self, workflow: dict) -> None:
        self._ensure_directory()
        path = self._path(workflow["workflow_id"])
        fd, name = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(workflow, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise

    def create(self, workflow: dict) -> None:
        """Create a new workflow ID without replacing an existing one."""
        self._ensure_directory()
        path = self._path(workflow["workflow_id"])
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(workflow, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @contextmanager
    def locked(self, workflow_id: str) -> Iterator[None]:
        """Serialize advances across processes, not just inside one MCP worker."""
        self._ensure_directory()
        lock_path = self._path(workflow_id).with_suffix(".lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise WorkflowStoreError(
                    "This workflow is being advanced by another call."
                ) from None
            yield
        finally:
            os.close(fd)


def default_store() -> WorkflowStore:
    if is_stateless_mode():
        raise WorkflowStoreError(
            "Offboarding requires durable storage; stateless mode is unsupported."
        )
    return WorkflowStore(
        Path(get_default_credentials_dir()) / "admin-state" / "workflows"
    )
