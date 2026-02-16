"""
Unit tests for attachment storage and serving functionality.

Tests the AttachmentStorage class and the serve_attachment endpoint.
"""

import pytest
from unittest.mock import Mock, patch
import base64
import tempfile
from pathlib import Path
class TestAttachmentStorage:
    """Tests for the AttachmentStorage class."""

    def test_save_attachment_stores_metadata(self):
        """Test that save_attachment correctly stores file and metadata."""
        from core.attachment_storage import AttachmentStorage

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("core.attachment_storage.STORAGE_DIR", Path(tmpdir)):
                storage = AttachmentStorage()

                # Create test data (base64 URL-safe encoded)
                test_content = b"test image content"
                base64_data = base64.urlsafe_b64encode(test_content).decode()

                file_id = storage.save_attachment(
                    base64_data=base64_data,
                    filename="test.png",
                    mime_type="image/png",
                )

                # Verify file_id is returned
                assert file_id is not None
                assert len(file_id) == 36  # UUID format

                # Verify metadata is stored
                metadata = storage.get_attachment_metadata(file_id)
                assert metadata is not None
                assert metadata["filename"] == "test.png"
                assert metadata["mime_type"] == "image/png"
                assert metadata["size"] == len(test_content)

    def test_get_attachment_metadata_returns_none_for_unknown_id(self):
        """Test that get_attachment_metadata returns None for unknown file_id."""
        from core.attachment_storage import AttachmentStorage

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("core.attachment_storage.STORAGE_DIR", Path(tmpdir)):
                storage = AttachmentStorage()
                metadata = storage.get_attachment_metadata("nonexistent-uuid")

                assert metadata is None

    def test_get_attachment_path_returns_path_for_valid_id(self):
        """Test that get_attachment_path returns correct path."""
        from core.attachment_storage import AttachmentStorage

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("core.attachment_storage.STORAGE_DIR", Path(tmpdir)):
                storage = AttachmentStorage()

                test_content = b"test content"
                base64_data = base64.urlsafe_b64encode(test_content).decode()

                file_id = storage.save_attachment(
                    base64_data=base64_data,
                    filename="test.txt",
                    mime_type="text/plain",
                )

                path = storage.get_attachment_path(file_id)
                assert path is not None
                assert path.exists()

    def test_save_attachment_without_filename_uses_default(self):
        """Test that save_attachment uses default filename when not provided."""
        from core.attachment_storage import AttachmentStorage

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("core.attachment_storage.STORAGE_DIR", Path(tmpdir)):
                storage = AttachmentStorage()

                test_content = b"test content"
                base64_data = base64.urlsafe_b64encode(test_content).decode()

                file_id = storage.save_attachment(base64_data=base64_data)

                metadata = storage.get_attachment_metadata(file_id)
                assert metadata["filename"] == "attachment"
                assert metadata["mime_type"] == "application/octet-stream"


class TestServeAttachmentEndpoint:
    """Tests for the serve_attachment endpoint."""

    @pytest.mark.asyncio
    async def test_serve_attachment_extracts_file_id_from_path_params(self):
        """Test that serve_attachment correctly extracts file_id from request.path_params."""
        # Import the function (need to mock the storage)
        from core.server import serve_attachment

        # Create a mock Request object with path_params
        mock_request = Mock()
        mock_request.path_params = {"file_id": "test-uuid-1234"}

        # Mock the attachment storage to return valid metadata
        mock_storage = Mock()
        mock_storage.get_attachment_metadata.return_value = {
            "filename": "test.png",
            "mime_type": "image/png",
            "size": 1024,
        }
        mock_storage.get_attachment_path.return_value = Path("/tmp/test.png")

        # Patch where get_attachment_storage is imported from (inside the function)
        with patch(
            "core.attachment_storage.get_attachment_storage",
            return_value=mock_storage,
        ):
            with patch("core.server.FileResponse") as mock_file_response:
                mock_file_response.return_value = Mock()

                await serve_attachment(mock_request)

                # Verify get_attachment_metadata was called with the correct file_id
                mock_storage.get_attachment_metadata.assert_called_once_with(
                    "test-uuid-1234"
                )

    @pytest.mark.asyncio
    async def test_serve_attachment_returns_400_for_missing_file_id(self):
        """Test that serve_attachment returns 400 when file_id is missing."""
        from core.server import serve_attachment

        mock_request = Mock()
        mock_request.path_params = {}  # No file_id

        response = await serve_attachment(mock_request)

        assert response.status_code == 400
        assert b"Missing file_id" in response.body

    @pytest.mark.asyncio
    async def test_serve_attachment_returns_404_for_unknown_file(self):
        """Test that serve_attachment returns 404 for unknown file_id."""
        from core.server import serve_attachment

        mock_request = Mock()
        mock_request.path_params = {"file_id": "unknown-uuid"}

        mock_storage = Mock()
        mock_storage.get_attachment_metadata.return_value = None

        with patch(
            "core.attachment_storage.get_attachment_storage",
            return_value=mock_storage,
        ):
            response = await serve_attachment(mock_request)

            assert response.status_code == 404
            assert b"not found" in response.body.lower()

    @pytest.mark.asyncio
    async def test_serve_attachment_uses_request_object_not_string(self):
        """
        Regression test: Verify serve_attachment receives Request object
        and extracts file_id from path_params, not treating Request as file_id.

        This was the bug: FastMCP's custom_route passes Request as first arg,
        but the function signature expected file_id: str directly.
        """
        from core.server import serve_attachment
        import inspect

        # Verify the function signature expects a request parameter
        sig = inspect.signature(serve_attachment)
        params = list(sig.parameters.keys())

        # The first parameter should be named 'request' (not 'file_id')
        assert params[0] == "request", (
            "serve_attachment should accept 'request' as first parameter, "
            f"but got '{params[0]}'. FastMCP's custom_route passes Request object."
        )
