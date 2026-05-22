"""
Tests for check_credentials_directory_permissions() — focuses on the
concurrent-callers regression where two processes sharing a credentials
directory raced on a fixed ``.permission_test`` filename and one would
ENOENT during write/remove.
"""

import multiprocessing as mp
import os

import pytest

from core.utils import check_credentials_directory_permissions


def test_existing_writable_directory(tmp_path):
    """Happy path: an existing writable directory passes the check."""
    check_credentials_directory_permissions(str(tmp_path))


def test_creates_missing_directory(tmp_path):
    """Function creates the directory if it doesn't exist."""
    target = tmp_path / "new_creds_dir"
    assert not target.exists()
    check_credentials_directory_permissions(str(target))
    assert target.is_dir()


def test_does_not_leave_probe_files(tmp_path):
    """The probe file must be cleaned up regardless of how the check exits."""
    check_credentials_directory_permissions(str(tmp_path))
    leftovers = list(tmp_path.iterdir())
    assert leftovers == [], f"probe file leak: {leftovers}"


def test_read_only_directory_raises_permission_error(tmp_path):
    """A read-only directory must raise PermissionError."""
    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()
    ro_dir.chmod(0o555)
    try:
        with pytest.raises(PermissionError):
            check_credentials_directory_permissions(str(ro_dir))
    finally:
        ro_dir.chmod(0o755)


def _worker(creds_dir: str, iterations: int, result_queue: "mp.Queue") -> None:
    """Call the permission check N times; report any exception."""
    try:
        for _ in range(iterations):
            check_credentials_directory_permissions(creds_dir)
        result_queue.put(("ok", os.getpid(), None))
    except Exception as exc:  # noqa: BLE001 — test deliberately wide
        result_queue.put(("error", os.getpid(), f"{type(exc).__name__}: {exc}"))


def test_concurrent_callers_do_not_race_on_probe_filename(tmp_path):
    """
    Regression test for the ``.permission_test`` race.

    Pre-fix, multiple processes sharing a credentials directory would
    collide on the fixed probe filename — one would ENOENT during the
    write/remove sequence and raise PermissionError. The fix uses
    ``tempfile.mkstemp`` with a random suffix so each call gets a unique
    probe path.
    """
    creds_dir = tmp_path / "shared_creds"
    creds_dir.mkdir()

    procs = []
    queue = mp.Queue()
    n_workers = 8
    iterations = 50

    for _ in range(n_workers):
        p = mp.Process(target=_worker, args=(str(creds_dir), iterations, queue))
        p.start()
        procs.append(p)

    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0, (
            f"worker pid={p.pid} exited with code {p.exitcode}"
        )

    results = []
    while not queue.empty():
        results.append(queue.get_nowait())

    errors = [r for r in results if r[0] == "error"]
    assert errors == [], f"concurrent callers raced: {errors}"
    assert len(results) == n_workers, f"missing worker reports: {results}"

    leftovers = list(creds_dir.iterdir())
    assert leftovers == [], f"probe file leak after concurrent run: {leftovers}"
