"""Tests for atomic credential writes in LocalDirectoryCredentialStore.

A failed or interrupted save must never empty or corrupt the existing
credential file, and must never leave a temp file that looks like an account.
"""

import json
import os
import stat
from pathlib import Path
from typing import Any, Callable, NoReturn
from unittest.mock import MagicMock

import pytest

import auth.credential_store as credential_store_module
from auth.credential_store import CredentialStore, LocalDirectoryCredentialStore

EMAIL = "user@example.com"
FILENAME = f"{EMAIL}{CredentialStore.FILE_EXTENSION}"

posix_only = pytest.mark.skipif(
    os.name != "posix", reason="checks POSIX file modes and replace semantics"
)


@pytest.fixture
def cred_store(tmp_path: Path) -> LocalDirectoryCredentialStore:
    """Create a LocalDirectoryCredentialStore with a temp base directory."""
    return LocalDirectoryCredentialStore(base_dir=str(tmp_path / "creds"))


def _mock_creds(token: str, refresh_token: str) -> MagicMock:
    creds = MagicMock()
    creds.token = token
    creds.refresh_token = refresh_token
    creds.token_uri = "https://oauth2.googleapis.com/token"
    creds.client_id = "cid"
    creds.client_secret = "csec"
    creds.scopes = ["openid"]
    creds.expiry = None
    return creds


def _partial_dump(obj: Any, f: Any, **kwargs: Any) -> NoReturn:
    """Write the start of the JSON, then fail as a full disk would."""
    f.write('{"token": "new')
    raise OSError(28, "No space left on device")


def _raise_oserror(*args: Any, **kwargs: Any) -> NoReturn:
    raise OSError(5, "Input/output error")


FAILURES: list[tuple[str, str, Callable[..., NoReturn]]] = [
    ("json", "dump", _partial_dump),
    ("os", "fsync", _raise_oserror),
    ("os", "replace", _raise_oserror),
]


@pytest.mark.parametrize(
    "module_attr,func_name,failure",
    FAILURES,
    ids=["json.dump", "os.fsync", "os.replace"],
)
def test_failed_save_keeps_existing_credential(
    cred_store: LocalDirectoryCredentialStore,
    monkeypatch: pytest.MonkeyPatch,
    module_attr: str,
    func_name: str,
    failure: Callable[..., NoReturn],
) -> None:
    """A save that fails part-way leaves the old file byte-for-byte intact."""
    assert cred_store.store_credential(EMAIL, _mock_creds("old_tok", "old_rtok"))
    cred_path = os.path.join(cred_store.base_dir, FILENAME)
    with open(cred_path, "rb") as f:
        before = f.read()

    monkeypatch.setattr(
        getattr(credential_store_module, module_attr), func_name, failure
    )
    result = cred_store.store_credential(EMAIL, _mock_creds("new_tok", "new_rtok"))
    monkeypatch.undo()

    assert result is False
    with open(cred_path, "rb") as f:
        assert f.read() == before
    loaded = cred_store.get_credential(EMAIL)
    assert loaded.token == "old_tok"
    assert loaded.refresh_token == "old_rtok"
    assert os.listdir(cred_store.base_dir) == [FILENAME]


@pytest.mark.parametrize(
    "module_attr,func_name,failure",
    FAILURES,
    ids=["json.dump", "os.fsync", "os.replace"],
)
def test_failed_first_save_creates_no_file(
    cred_store: LocalDirectoryCredentialStore,
    monkeypatch: pytest.MonkeyPatch,
    module_attr: str,
    func_name: str,
    failure: Callable[..., NoReturn],
) -> None:
    """A failed first save leaves neither a credential file nor a temp file."""
    monkeypatch.setattr(
        getattr(credential_store_module, module_attr), func_name, failure
    )
    result = cred_store.store_credential(EMAIL, _mock_creds("tok", "rtok"))
    monkeypatch.undo()

    assert result is False
    assert os.listdir(cred_store.base_dir) == []
    assert cred_store.list_users() == []


def test_unserializable_data_cleans_up_and_raises(
    cred_store: LocalDirectoryCredentialStore,
) -> None:
    """A non-OSError still propagates, with the old file and no temp file left."""
    assert cred_store.store_credential(EMAIL, _mock_creds("old_tok", "old_rtok"))
    cred_path = os.path.join(cred_store.base_dir, FILENAME)
    with open(cred_path, "rb") as f:
        before = f.read()

    bad = _mock_creds("new_tok", "new_rtok")
    bad.scopes = object()
    with pytest.raises(TypeError):
        cred_store.store_credential(EMAIL, bad)

    with open(cred_path, "rb") as f:
        assert f.read() == before
    assert os.listdir(cred_store.base_dir) == [FILENAME]


@posix_only
def test_successful_save_replaces_content(
    cred_store: LocalDirectoryCredentialStore,
) -> None:
    """A good save replaces the content: one file, mode 0600, one user."""
    assert cred_store.store_credential(EMAIL, _mock_creds("old_tok", "old_rtok"))
    assert cred_store.store_credential(EMAIL, _mock_creds("new_tok", "new_rtok"))

    cred_path = os.path.join(cred_store.base_dir, FILENAME)
    assert os.listdir(cred_store.base_dir) == [FILENAME]
    assert stat.S_IMODE(os.stat(cred_path).st_mode) == 0o600
    loaded = cred_store.get_credential(EMAIL)
    assert loaded.token == "new_tok"
    assert loaded.refresh_token == "new_rtok"
    assert cred_store.list_users() == [EMAIL]


def _temp_files(base_dir: str) -> list[str]:
    return sorted(name for name in os.listdir(base_dir) if name != FILENAME)


@posix_only
def test_temp_file_is_complete_and_private_when_synced(
    cred_store: LocalDirectoryCredentialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At fsync time the real temp file holds the full JSON, is 0600 and is not an account."""
    assert cred_store.store_credential(EMAIL, _mock_creds("old_tok", "old_rtok"))
    real_fsync = os.fsync
    seen: dict[str, Any] = {}

    def inspecting_fsync(fd: int) -> None:
        (name,) = _temp_files(cred_store.base_dir)
        path = os.path.join(cred_store.base_dir, name)
        with open(path, "rb") as f:
            seen["bytes"] = f.read()
        seen["name"] = name
        seen["mode"] = stat.S_IMODE(os.stat(path).st_mode)
        seen["users"] = cred_store.list_users()
        seen["token"] = cred_store.get_credential(EMAIL).token
        real_fsync(fd)

    monkeypatch.setattr(credential_store_module.os, "fsync", inspecting_fsync)
    assert cred_store.store_credential(EMAIL, _mock_creds("new_tok", "new_rtok"))
    monkeypatch.undo()

    cred_path = os.path.join(cred_store.base_dir, FILENAME)
    with open(cred_path, "rb") as f:
        assert seen["bytes"] == f.read()
    assert json.loads(seen["bytes"])["refresh_token"] == "new_rtok"
    assert seen["name"].startswith(".")
    assert seen["name"].endswith(".tmp")
    assert not seen["name"].endswith(CredentialStore.FILE_EXTENSION)
    assert seen["mode"] == 0o600
    assert seen["users"] == [EMAIL]
    assert seen["token"] == "old_tok"
    assert os.listdir(cred_store.base_dir) == [FILENAME]


def test_leftover_temp_file_is_not_an_account(
    cred_store: LocalDirectoryCredentialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A temp file left behind (as by a killed process) is not listed or loaded."""
    assert cred_store.store_credential(EMAIL, _mock_creds("old_tok", "old_rtok"))

    # Fail the swap and skip the cleanup, which is what a kill at that point leaves.
    monkeypatch.setattr(credential_store_module.os, "replace", _raise_oserror)
    monkeypatch.setattr(credential_store_module.os, "unlink", lambda path: None)
    assert not cred_store.store_credential(EMAIL, _mock_creds("new_tok", "new_rtok"))
    monkeypatch.undo()

    (leftover,) = _temp_files(cred_store.base_dir)
    assert sorted(os.listdir(cred_store.base_dir)) == sorted([leftover, FILENAME])
    assert cred_store.list_users() == [EMAIL]
    assert cred_store.get_credential(EMAIL).token == "old_tok"


# The tests below pin the behaviour changes that come from replacing the file
# instead of rewriting it.


def _store_old(cred_store: LocalDirectoryCredentialStore) -> tuple[str, bytes]:
    assert cred_store.store_credential(EMAIL, _mock_creds("old_tok", "old_rtok"))
    cred_path = os.path.join(cred_store.base_dir, FILENAME)
    with open(cred_path, "rb") as f:
        return cred_path, f.read()


@posix_only
def test_loose_permissions_become_0600_on_next_save(
    cred_store: LocalDirectoryCredentialStore,
) -> None:
    """An existing 0644 credential file is 0600 after its next save."""
    cred_path, _ = _store_old(cred_store)
    os.chmod(cred_path, 0o644)

    assert cred_store.store_credential(EMAIL, _mock_creds("new_tok", "new_rtok"))

    assert stat.S_IMODE(os.stat(cred_path).st_mode) == 0o600
    assert cred_store.get_credential(EMAIL).refresh_token == "new_rtok"
    assert os.listdir(cred_store.base_dir) == [FILENAME]


@posix_only
def test_read_only_file_in_writable_directory_is_replaced(
    cred_store: LocalDirectoryCredentialStore,
) -> None:
    """A read-only credential file in a writable directory is replaced, not kept."""
    cred_path, _ = _store_old(cred_store)
    os.chmod(cred_path, 0o400)

    assert cred_store.store_credential(EMAIL, _mock_creds("new_tok", "new_rtok"))

    assert stat.S_IMODE(os.stat(cred_path).st_mode) == 0o600
    assert cred_store.get_credential(EMAIL).refresh_token == "new_rtok"
    assert os.listdir(cred_store.base_dir) == [FILENAME]


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="needs POSIX permissions and a non-root user",
)
def test_directory_without_write_permission_fails_and_keeps_file(
    cred_store: LocalDirectoryCredentialStore,
) -> None:
    """Without write permission on the directory the save fails; the old file is kept."""
    cred_path, before = _store_old(cred_store)
    os.chmod(cred_store.base_dir, 0o500)
    try:
        result = cred_store.store_credential(EMAIL, _mock_creds("new_tok", "new_rtok"))
    finally:
        os.chmod(cred_store.base_dir, 0o700)

    assert result is False
    with open(cred_path, "rb") as f:
        assert f.read() == before
    assert cred_store.get_credential(EMAIL).refresh_token == "old_rtok"
    assert os.listdir(cred_store.base_dir) == [FILENAME]


@posix_only
def test_symlinked_credential_becomes_regular_file(
    cred_store: LocalDirectoryCredentialStore,
) -> None:
    """A symlink at the credential path is replaced by a file; its target is unchanged."""
    cred_path = cred_store._get_credential_path(EMAIL)
    target_path = os.path.join(cred_store.base_dir, ".target")
    target_bytes = b'{"token": "target_tok", "refresh_token": "target_rtok"}'
    with open(target_path, "wb") as f:
        f.write(target_bytes)
    os.symlink(".target", cred_path)

    assert cred_store.store_credential(EMAIL, _mock_creds("new_tok", "new_rtok"))

    assert not os.path.islink(cred_path)
    assert stat.S_ISREG(os.lstat(cred_path).st_mode)
    with open(target_path, "rb") as f:
        assert f.read() == target_bytes
    assert cred_store.get_credential(EMAIL).refresh_token == "new_rtok"
    assert sorted(os.listdir(cred_store.base_dir)) == [".target", FILENAME]
    assert cred_store.list_users() == [EMAIL]
