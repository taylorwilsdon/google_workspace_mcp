"""Regression tests for check_credentials_directory_permissions.

Multiple server processes can initialize the same credentials directory
concurrently. These tests pin the concurrency-safe, non-destructive behavior.
"""

import os
import threading

from core.utils import check_credentials_directory_permissions


def test_concurrent_checks_on_shared_dir_all_succeed(tmp_path):
    """Many processes initializing the same (initially missing) credentials dir
    at once must all succeed."""
    target = str(tmp_path / "credentials")  # does not exist yet
    errors: list[str] = []
    n = 24
    barrier = threading.Barrier(n)

    def worker():
        try:
            barrier.wait()
            for _ in range(5):
                check_credentials_directory_permissions(target)
        except Exception as e:  # noqa: BLE001 - capture for assertion
            errors.append(repr(e))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"{len(errors)} concurrent failures, e.g. {errors[:3]}"
    assert os.path.isdir(target)


def test_check_preserves_existing_dir_and_contents(tmp_path):
    """The check must never delete the credentials dir or its contents, and must
    not leave probe files behind."""
    target = tmp_path / "credentials"
    target.mkdir()
    keep = target / "token.json"
    keep.write_text("secret")

    check_credentials_directory_permissions(str(target))

    assert target.is_dir()
    assert keep.read_text() == "secret"
    assert not list(target.glob(".permission_test*"))


def test_check_creates_missing_dir(tmp_path):
    target = tmp_path / "nested" / "credentials"
    check_credentials_directory_permissions(str(target))
    assert target.is_dir()
