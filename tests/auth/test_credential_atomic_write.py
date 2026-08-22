"""Finding 40: credential writes must be atomic.

`store_credential` opened the real path with `O_TRUNC` and wrote in place. Two
concurrent token refreshes for the same user -- routine, since every expired access
token triggers one -- could interleave: the file was emptied by the second writer
while the first was still writing, so a reader (or a crash) could observe a truncated
file and lose the refresh token, forcing re-authentication.
"""

import json
import os
import stat
import threading
from datetime import datetime, timezone

import pytest
from google.oauth2.credentials import Credentials

from auth.credential_store import LocalDirectoryCredentialStore

USER = "user@example.com"


def _credentials(token="access-token", refresh="refresh-token"):
    return Credentials(
        token=token,
        refresh_token=refresh,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )


@pytest.fixture
def store(tmp_path):
    return LocalDirectoryCredentialStore(base_dir=str(tmp_path))


class TestRoundTrip:
    def test_stored_credentials_are_readable(self, store):
        assert store.store_credential(USER, _credentials()) is True

        loaded = store.get_credential(USER)

        assert loaded is not None
        assert loaded.refresh_token == "refresh-token"

    def test_file_is_owner_only(self, store, tmp_path):
        store.store_credential(USER, _credentials())

        path = store._get_credential_path(USER)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_expiry_is_persisted(self, store):
        creds = _credentials()
        creds.expiry = datetime(2030, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)

        store.store_credential(USER, creds)

        path = store._get_credential_path(USER)
        assert json.loads(open(path).read())["expiry"].startswith("2030-01-01")

    def test_overwriting_replaces_the_contents(self, store):
        store.store_credential(USER, _credentials(refresh="first"))
        store.store_credential(USER, _credentials(refresh="second"))

        assert store.get_credential(USER).refresh_token == "second"


class TestAtomicity:
    def test_no_temporary_files_are_left_behind(self, store, tmp_path):
        store.store_credential(USER, _credentials())

        leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_the_final_path_is_never_truncated_in_place(self, store, monkeypatch):
        """The bug's mechanism: opening the real path with O_TRUNC.

        Asserting on the syscall rather than on a timing-dependent race is what makes
        this a durable regression test.
        """
        target = str(store._get_credential_path(USER))
        real_open = os.open
        truncating_opens = []

        def watching_open(path, flags, *args, **kwargs):
            if str(path) == target and flags & os.O_TRUNC:
                truncating_opens.append(str(path))
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", watching_open)

        store.store_credential(USER, _credentials())

        assert truncating_opens == []

    def test_concurrent_writes_never_expose_a_partial_file(self, store, tmp_path):
        """A reader interleaved with many writers must always see valid JSON."""
        path = store._get_credential_path(USER)
        store.store_credential(USER, _credentials(refresh="initial"))

        stop = threading.Event()
        corrupt_reads = []

        def reader():
            while not stop.is_set():
                try:
                    with open(path) as f:
                        data = json.load(f)
                    if not data.get("refresh_token"):
                        corrupt_reads.append(data)
                except FileNotFoundError:
                    # os.replace() is atomic, so the name is never briefly absent;
                    # record it if it ever happens.
                    corrupt_reads.append("missing")
                except json.JSONDecodeError:
                    corrupt_reads.append("truncated")

        def writer(index):
            for _ in range(20):
                store.store_credential(USER, _credentials(refresh=f"refresh-{index}"))

        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()
        writers = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for w in writers:
            w.start()
        for w in writers:
            w.join()
        stop.set()
        reader_thread.join(timeout=5)

        assert corrupt_reads == []
        # Last writer wins, which is correct: all four wrote a valid credential.
        assert store.get_credential(USER).refresh_token.startswith("refresh-")

    def test_a_failed_write_leaves_the_previous_credential_intact(
        self, store, monkeypatch
    ):
        """A crash mid-write must not destroy the credential that was already there."""
        store.store_credential(USER, _credentials(refresh="good"))

        def exploding_dump(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("auth.credential_store.json.dump", exploding_dump)

        assert store.store_credential(USER, _credentials(refresh="bad")) is False
        assert store.get_credential(USER).refresh_token == "good"

    def test_a_failed_write_leaves_no_temporary_file(
        self, store, tmp_path, monkeypatch
    ):
        def exploding_dump(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("auth.credential_store.json.dump", exploding_dump)

        store.store_credential(USER, _credentials())

        leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []
