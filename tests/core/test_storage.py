"""Tests for the shared storage helpers in core.storage."""

import os
import string
import tempfile

import pytest

from core.storage import SAFE_FILENAME_CHARS, make_sanitized_file_store


# ---------------------------------------------------------------------------
# SAFE_FILENAME_CHARS constant
# ---------------------------------------------------------------------------


class TestSafeFilenameChars:
    """Verify the character-set constant contains exactly what we expect."""

    def test_includes_ascii_letters(self):
        for ch in string.ascii_letters:
            assert ch in SAFE_FILENAME_CHARS

    def test_includes_digits(self):
        for ch in string.digits:
            assert ch in SAFE_FILENAME_CHARS

    def test_includes_hyphen_underscore_dot(self):
        for ch in "-_.":
            assert ch in SAFE_FILENAME_CHARS

    def test_excludes_filesystem_unsafe_characters(self):
        """Characters that are problematic on common filesystems."""
        for ch in "/\\:*?\"<>|@ !#$%^&(){}[]+=`~',;":
            assert ch not in SAFE_FILENAME_CHARS


# ---------------------------------------------------------------------------
# make_sanitized_file_store – factory function
# ---------------------------------------------------------------------------


class TestMakeSanitizedFileStore:
    """Unit tests for the factory function."""

    def test_returns_filetree_store(self):
        from key_value.aio.stores.filetree import FileTreeStore

        with tempfile.TemporaryDirectory() as td:
            store = make_sanitized_file_store(td)
            assert isinstance(store, FileTreeStore)

    def test_store_uses_given_directory(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_sanitized_file_store(td)
            # Resolve both sides to handle macOS /var -> /private/var symlink
            assert os.path.realpath(store._data_directory) == os.path.realpath(td)

    def test_store_has_sanitization_strategy(self):
        from key_value.aio._utils.sanitization import HybridSanitizationStrategy

        with tempfile.TemporaryDirectory() as td:
            store = make_sanitized_file_store(td)
            assert isinstance(
                store._key_sanitization_strategy, HybridSanitizationStrategy
            )

    def test_sanitization_preserves_safe_keys(self):
        """Keys composed entirely of safe chars should pass through unchanged."""
        with tempfile.TemporaryDirectory() as td:
            store = make_sanitized_file_store(td)
            strategy = store._key_sanitization_strategy
            for key in ("simple-key", "dots.and_underscores", "ABC123"):
                assert strategy.sanitize(key) == key

    def test_sanitization_rewrites_unsafe_keys(self):
        """Keys with characters outside the safe set must be transformed."""
        with tempfile.TemporaryDirectory() as td:
            store = make_sanitized_file_store(td)
            strategy = store._key_sanitization_strategy
            for key in ("user@example.com", "key with spaces", "path/to/thing"):
                sanitized = strategy.sanitize(key)
                assert sanitized != key
                # The sanitized form must only contain safe chars plus the
                # prefix/hash characters the strategy adds ('S' prefix, '-')
                for ch in sanitized:
                    assert ch in SAFE_FILENAME_CHARS or ch in "S"


# ---------------------------------------------------------------------------
# Integration: async round-trip through the store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFileStoreRoundTrip:
    """End-to-end tests exercising actual disk I/O."""

    async def test_put_and_get(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_sanitized_file_store(td)
            await store.setup()

            await store.put("my-key", {"data": "hello"})
            result = await store.get("my-key")
            assert result == {"data": "hello"}

    async def test_get_missing_key_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_sanitized_file_store(td)
            await store.setup()

            assert await store.get("nonexistent") is None

    async def test_delete_removes_value(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_sanitized_file_store(td)
            await store.setup()

            await store.put("ephemeral", {"v": 1})
            assert await store.get("ephemeral") is not None

            await store.delete("ephemeral")
            assert await store.get("ephemeral") is None

    async def test_overwrite_replaces_value(self):
        with tempfile.TemporaryDirectory() as td:
            store = make_sanitized_file_store(td)
            await store.setup()

            await store.put("key", {"version": 1})
            await store.put("key", {"version": 2})
            assert (await store.get("key")) == {"version": 2}

    async def test_special_character_keys_round_trip(self):
        """OAuth stores use email addresses and URLs as keys."""
        with tempfile.TemporaryDirectory() as td:
            store = make_sanitized_file_store(td)
            await store.setup()

            keys = [
                "user@example.com",
                "https://accounts.google.com/token",
                "key with spaces",
                "slashes/in/path",
            ]
            for key in keys:
                payload = {"key": key}
                await store.put(key, payload)
                assert await store.get(key) == payload

    async def test_files_written_to_correct_directory(self):
        """Data files should land inside the specified directory."""
        with tempfile.TemporaryDirectory() as td:
            store = make_sanitized_file_store(td)
            await store.setup()

            await store.put("check-dir", {"ok": True})

            # At least one .json data file should exist under td
            json_files = []
            for root, _dirs, files in os.walk(td):
                for f in files:
                    if f.endswith(".json"):
                        json_files.append(os.path.join(root, f))

            assert len(json_files) > 0
            for path in json_files:
                assert path.startswith(td)

    async def test_on_disk_filenames_are_filesystem_safe(self):
        """Even when the key contains unsafe chars, filenames must be safe."""
        with tempfile.TemporaryDirectory() as td:
            store = make_sanitized_file_store(td)
            await store.setup()

            await store.put("user@example.com", {"token": "x"})

            for root, _dirs, files in os.walk(td):
                for f in files:
                    # Strip the .json extension for the check
                    basename = f.rsplit(".", 1)[0] if "." in f else f
                    for ch in basename:
                        assert ch in SAFE_FILENAME_CHARS or ch in "S", (
                            f"Unexpected char {ch!r} in filename {f!r}"
                        )

    async def test_multiple_stores_same_directory_share_data(self):
        """Two store instances pointing at the same dir see the same data."""
        with tempfile.TemporaryDirectory() as td:
            store_a = make_sanitized_file_store(td)
            store_b = make_sanitized_file_store(td)
            await store_a.setup()
            await store_b.setup()

            await store_a.put("shared", {"from": "a"})
            assert (await store_b.get("shared")) == {"from": "a"}


# ---------------------------------------------------------------------------
# Consistency: CLI and server use the same factory
# ---------------------------------------------------------------------------


class TestConsistency:
    """Verify CLI and server both delegate to the shared factory."""

    def test_cli_imports_shared_factory(self):
        """core.cli must use make_sanitized_file_store, not inline config."""
        import inspect

        import core.cli as cli_module

        source = inspect.getsource(cli_module._get_token_storage)
        assert "make_sanitized_file_store" in source
        # Must NOT contain the old inline pattern
        assert "HybridSanitizationStrategy" not in source

    def test_server_references_shared_factory(self):
        """core.server must use make_sanitized_file_store, not inline config."""
        import inspect

        import core.server as server_module

        source = inspect.getsource(server_module.configure_server_for_http)
        assert "make_sanitized_file_store" in source
        # Must NOT contain the old inline pattern
        assert "HybridSanitizationStrategy" not in source
