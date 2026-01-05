"""
Tests for OAuth state persistence functionality.

These tests verify that OAuth states are properly persisted to disk
and can survive server restarts.
"""

import json
import os
import tempfile
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


class TestOAuthStatePersistence:
    """Test OAuth state persistence to disk."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create a temporary directory for test credentials
        self.temp_dir = tempfile.mkdtemp()
        self.states_file = os.path.join(self.temp_dir, "oauth_states.json")

    def teardown_method(self):
        """Clean up test fixtures."""
        # Clean up temp files
        if os.path.exists(self.states_file):
            os.remove(self.states_file)
        if os.path.exists(self.temp_dir):
            os.rmdir(self.temp_dir)

    def test_get_oauth_states_file_path_with_env_var(self):
        """Test that file path respects GOOGLE_MCP_CREDENTIALS_DIR env var."""
        from auth.oauth21_session_store import _get_oauth_states_file_path

        with patch.dict(os.environ, {"GOOGLE_MCP_CREDENTIALS_DIR": self.temp_dir}):
            path = _get_oauth_states_file_path()
            assert path == os.path.join(self.temp_dir, "oauth_states.json")

    def test_store_oauth_state_persists_to_disk(self):
        """Test that storing an OAuth state persists it to disk."""
        from auth.oauth21_session_store import OAuth21SessionStore

        with patch.dict(os.environ, {"GOOGLE_MCP_CREDENTIALS_DIR": self.temp_dir}):
            store = OAuth21SessionStore()
            store._states_file_path = self.states_file

            # Store a state
            test_state = "test_state_12345"
            store.store_oauth_state(test_state, session_id="test_session")

            # Verify file was created
            assert os.path.exists(self.states_file)

            # Verify content
            with open(self.states_file, "r") as f:
                data = json.load(f)

            assert test_state in data
            assert data[test_state]["session_id"] == "test_session"
            assert "expires_at" in data[test_state]
            assert "created_at" in data[test_state]

    def test_load_oauth_states_from_disk(self):
        """Test that OAuth states are loaded from disk on initialization."""
        from auth.oauth21_session_store import OAuth21SessionStore

        # Create a states file with valid data
        future_expiry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        created_at = datetime.now(timezone.utc).isoformat()

        test_data = {
            "persisted_state_abc": {
                "session_id": "persisted_session",
                "expires_at": future_expiry,
                "created_at": created_at,
            }
        }

        with open(self.states_file, "w") as f:
            json.dump(test_data, f)

        with patch.dict(os.environ, {"GOOGLE_MCP_CREDENTIALS_DIR": self.temp_dir}):
            store = OAuth21SessionStore()
            store._states_file_path = self.states_file
            store._load_oauth_states_from_disk()

            # Verify state was loaded
            assert "persisted_state_abc" in store._oauth_states
            assert (
                store._oauth_states["persisted_state_abc"]["session_id"]
                == "persisted_session"
            )

    def test_expired_states_cleaned_on_load(self):
        """Test that expired states are cleaned up when loading from disk."""
        from auth.oauth21_session_store import OAuth21SessionStore

        # Create a states file with expired data
        past_expiry = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        future_expiry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        created_at = datetime.now(timezone.utc).isoformat()

        test_data = {
            "expired_state": {
                "session_id": "expired_session",
                "expires_at": past_expiry,
                "created_at": created_at,
            },
            "valid_state": {
                "session_id": "valid_session",
                "expires_at": future_expiry,
                "created_at": created_at,
            },
        }

        with open(self.states_file, "w") as f:
            json.dump(test_data, f)

        with patch.dict(os.environ, {"GOOGLE_MCP_CREDENTIALS_DIR": self.temp_dir}):
            store = OAuth21SessionStore()
            store._states_file_path = self.states_file
            store._load_oauth_states_from_disk()

            # Expired state should be cleaned up
            assert "expired_state" not in store._oauth_states
            # Valid state should remain
            assert "valid_state" in store._oauth_states

    def test_validate_and_consume_persists_deletion(self):
        """Test that consuming a state persists the deletion to disk."""
        from auth.oauth21_session_store import OAuth21SessionStore

        with patch.dict(os.environ, {"GOOGLE_MCP_CREDENTIALS_DIR": self.temp_dir}):
            store = OAuth21SessionStore()
            store._states_file_path = self.states_file

            # Store a state
            test_state = "state_to_consume"
            store.store_oauth_state(test_state, session_id="test_session")

            # Verify it's in the file
            with open(self.states_file, "r") as f:
                data = json.load(f)
            assert test_state in data

            # Consume the state
            store.validate_and_consume_oauth_state(test_state)

            # Verify it's removed from the file
            with open(self.states_file, "r") as f:
                data = json.load(f)
            assert test_state not in data

    def test_state_survives_store_recreation(self):
        """Test that states survive when the store is recreated (simulating server restart)."""
        from auth.oauth21_session_store import OAuth21SessionStore

        with patch.dict(os.environ, {"GOOGLE_MCP_CREDENTIALS_DIR": self.temp_dir}):
            # Create first store and add a state
            store1 = OAuth21SessionStore()
            store1._states_file_path = self.states_file

            test_state = "survivor_state"
            store1.store_oauth_state(test_state, session_id="survivor_session")

            # Create a new store (simulating server restart)
            store2 = OAuth21SessionStore()
            store2._states_file_path = self.states_file
            store2._load_oauth_states_from_disk()

            # Verify the state survived
            assert test_state in store2._oauth_states

            # Verify we can validate it
            state_info = store2.validate_and_consume_oauth_state(test_state)
            assert state_info["session_id"] == "survivor_session"

    def test_handles_missing_file_gracefully(self):
        """Test that loading handles missing file gracefully."""
        from auth.oauth21_session_store import OAuth21SessionStore

        with patch.dict(os.environ, {"GOOGLE_MCP_CREDENTIALS_DIR": self.temp_dir}):
            store = OAuth21SessionStore()
            store._states_file_path = os.path.join(self.temp_dir, "nonexistent.json")

            # Should not raise an exception
            store._load_oauth_states_from_disk()

            # Store should be empty
            assert len(store._oauth_states) == 0

    def test_handles_corrupted_file_gracefully(self):
        """Test that loading handles corrupted JSON gracefully."""
        from auth.oauth21_session_store import OAuth21SessionStore

        # Write corrupted JSON
        with open(self.states_file, "w") as f:
            f.write("{ invalid json }")

        with patch.dict(os.environ, {"GOOGLE_MCP_CREDENTIALS_DIR": self.temp_dir}):
            store = OAuth21SessionStore()
            store._states_file_path = self.states_file

            # Should not raise an exception
            store._load_oauth_states_from_disk()

            # Store should be empty
            assert len(store._oauth_states) == 0
