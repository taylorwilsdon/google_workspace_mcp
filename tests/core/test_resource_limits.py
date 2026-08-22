"""The fixed resource ceilings from core.limits, and the per-tool guards using them.

Findings 1, 11, 12, 20, 32, 44 and 50 are one bug repeated across services: a caller
could ask the process to materialise an arbitrarily large payload. These tests pin the
constants themselves (so a future change is a deliberate, visible edit) and the guards
that are not already covered where their tool lives.
"""

import base64

import pytest

from core import limits


class TestConstants:
    """The values are compiled in; an env var must not be able to raise them."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("MAX_HTTP_REQUEST_BODY_BYTES", 50 * 1024 * 1024),
            ("MAX_ATTACHMENT_BYTES", 50 * 1024 * 1024),
            ("MAX_EMAIL_ATTACHMENT_BYTES", 25 * 1024 * 1024),
            ("MAX_CHAT_ATTACHMENT_BYTES", 50 * 1024 * 1024),
            ("MAX_DRIVE_INLINE_BASE64_BYTES", 32 * 1024 * 1024),
            ("MAX_DRIVE_STREAMED_BYTES", 2 * 1024 * 1024 * 1024),
            ("MAX_DOC_CONTENT_BYTES", 50 * 1024 * 1024),
            ("MAX_SCRIPT_FILES", 100),
            ("MAX_SCRIPT_FILE_BYTES", 5 * 1024 * 1024),
            ("MAX_SCRIPT_TOTAL_BYTES", 10 * 1024 * 1024),
        ],
    )
    def test_limit_value(self, name, expected):
        assert getattr(limits, name) == expected

    def test_modules_use_the_shared_constants(self):
        """Per-module aliases must not drift from core.limits."""
        import core.attachment_storage as attachment_storage
        import core.server as server
        import gdrive.drive_helpers as drive_helpers
        import gmail.gmail_tools as gmail_tools

        assert server.MAX_HTTP_REQUEST_BODY_BYTES == limits.MAX_HTTP_REQUEST_BODY_BYTES
        assert attachment_storage.MAX_ATTACHMENT_BYTES == limits.MAX_ATTACHMENT_BYTES
        assert (
            gmail_tools.MAX_EMAIL_ATTACHMENT_BYTES == limits.MAX_EMAIL_ATTACHMENT_BYTES
        )
        assert drive_helpers.MAX_DOWNLOAD_BYTES == limits.MAX_DRIVE_STREAMED_BYTES


class TestBase64Ceiling:
    """Encoded length is checked first so decoded bytes are never allocated."""

    @pytest.mark.parametrize("decoded_size", [0, 1, 2, 3, 4, 100, 1024, 32 * 1024**2])
    def test_ceiling_admits_the_real_encoding(self, decoded_size):
        encoded = base64.b64encode(b"x" * decoded_size)
        assert len(encoded) <= limits.max_base64_length_for(decoded_size)

    def test_ceiling_rejects_anything_longer(self):
        # One byte over the ceiling cannot decode to <= 3 bytes.
        ceiling = limits.max_base64_length_for(3)
        assert ceiling == 4
        assert len(base64.b64encode(b"xxxx")) > ceiling


class TestAppsScriptLimits:
    """Finding 32: script payloads are bounded by count, per file, and in total."""

    def _files(self, count, source="x"):
        return [
            {"name": f"f{i}", "type": "SERVER_JS", "source": source}
            for i in range(count)
        ]

    def test_file_count_is_bounded(self):
        from core.utils import UserInputError
        from gappsscript.apps_script_tools import _validate_script_payload_size

        with pytest.raises(UserInputError, match="file limit"):
            _validate_script_payload_size(self._files(limits.MAX_SCRIPT_FILES + 1))

    def test_single_file_size_is_bounded(self, monkeypatch):
        from core.utils import UserInputError
        from gappsscript import apps_script_tools

        monkeypatch.setattr(apps_script_tools, "MAX_SCRIPT_FILE_BYTES", 8)

        with pytest.raises(UserInputError, match="exceeds the 8 byte limit"):
            apps_script_tools._validate_script_payload_size(
                [{"name": "big", "type": "SERVER_JS", "source": "x" * 9}]
            )

    def test_total_size_is_bounded_even_when_each_file_fits(self, monkeypatch):
        """Many small files are as effective a DoS as one large one."""
        from core.utils import UserInputError
        from gappsscript import apps_script_tools

        monkeypatch.setattr(apps_script_tools, "MAX_SCRIPT_FILE_BYTES", 8)
        monkeypatch.setattr(apps_script_tools, "MAX_SCRIPT_TOTAL_BYTES", 16)

        with pytest.raises(UserInputError, match="total limit"):
            apps_script_tools._validate_script_payload_size(
                self._files(3, source="x" * 8)
            )

    def test_multibyte_source_is_measured_in_bytes(self, monkeypatch):
        """A byte limit must not be under-counted by using len() on the string."""
        from core.utils import UserInputError
        from gappsscript import apps_script_tools

        monkeypatch.setattr(apps_script_tools, "MAX_SCRIPT_FILE_BYTES", 8)

        # 4 characters, 12 UTF-8 bytes.
        with pytest.raises(UserInputError, match="exceeds the 8 byte limit"):
            apps_script_tools._validate_script_payload_size(
                [{"name": "jp", "type": "SERVER_JS", "source": "あいうえ"}]
            )

    def test_payload_within_all_limits_passes(self):
        from gappsscript.apps_script_tools import _validate_script_payload_size

        _validate_script_payload_size(self._files(3, source="function f() {}"))
