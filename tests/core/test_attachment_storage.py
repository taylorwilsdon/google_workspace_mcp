"""Tests for filename handling and storage in core.attachment_storage."""

import base64
import os
import stat
import time
import unicodedata
from pathlib import Path
from unittest.mock import Mock

import pytest

import core.attachment_storage as attachment_storage
from core.attachment_storage import AttachmentStorage, sanitize_attachment_filename

# Built via chr() so the source stays pure ASCII and the exact code points are
# unambiguous (these characters are visually indistinguishable from a space).
NARROW_NBSP = chr(0x202F)  # macOS screenshot time separator (NARROW NO-BREAK SPACE)


class TestSanitizeAttachmentFilename:
    """Unit tests for sanitize_attachment_filename."""

    def test_plain_filename_unchanged(self):
        assert sanitize_attachment_filename("report.pdf") == "report.pdf"

    def test_empty_returns_default(self):
        assert sanitize_attachment_filename("") == "attachment"

    def test_none_returns_default(self):
        assert sanitize_attachment_filename(None) == "attachment"

    def test_reserved_characters_replaced(self):
        assert sanitize_attachment_filename("a/b:c*d?.png") == "a_b_c_d_.png"

    def test_windows_reserved_name_prefixed(self):
        assert sanitize_attachment_filename("CON.txt") == "_CON.txt"

    def test_regular_ascii_space_preserved(self):
        assert sanitize_attachment_filename("my file.txt") == "my file.txt"

    def test_narrow_no_break_space_normalized_to_ascii_space(self):
        # macOS screenshots use U+202F (NARROW NO-BREAK SPACE) before "AM"/"PM".
        # Clients that echo the saved path back into a read/open call often
        # normalize U+202F to a regular space (U+0020); the saved name must use a
        # regular space too, or that read fails with "file not found".
        original = f"Screenshot 2026-05-28 at 3.44.08{NARROW_NBSP}PM.png"
        result = sanitize_attachment_filename(original)
        assert NARROW_NBSP not in result
        assert result == "Screenshot 2026-05-28 at 3.44.08 PM.png"

    def test_various_unicode_spaces_normalized(self):
        # NO-BREAK SPACE, THIN SPACE, NARROW NO-BREAK SPACE, IDEOGRAPHIC SPACE --
        # all Unicode "Zs" (space separator) code points.
        for cp in (0x00A0, 0x2009, 0x202F, 0x3000):
            sep = chr(cp)
            assert unicodedata.category(sep) == "Zs"
            assert sanitize_attachment_filename(f"a{sep}b.txt") == "a b.txt"


class TestSaveAttachment:
    """save_attachment writes base64 payloads to storage."""

    @pytest.fixture
    def storage(self, tmp_path, monkeypatch):
        monkeypatch.setattr(attachment_storage, "STORAGE_DIR", tmp_path / "attachments")
        return AttachmentStorage()

    def test_ids_sharing_a_prefix_get_distinct_files(self, storage, monkeypatch):
        # The on-disk name once used only file_id[:8], so two ids sharing that
        # prefix overwrote each other and the first attachment was lost.
        ids = [
            "abcdef12-0000-4000-8000-000000000001",
            "abcdef12-0000-4000-8000-000000000002",
        ]
        monkeypatch.setattr(
            attachment_storage.uuid, "uuid4", Mock(side_effect=list(ids))
        )

        first = storage.save_attachment(
            base64.urlsafe_b64encode(b"first").decode(), filename="report.pdf"
        )
        second = storage.save_attachment(
            base64.urlsafe_b64encode(b"second").decode(), filename="report.pdf"
        )

        assert first.path != second.path
        assert Path(first.path).read_bytes() == b"first"
        assert Path(second.path).read_bytes() == b"second"
        assert storage.get_attachment_path(first.file_id) == Path(first.path)
        assert storage.get_attachment_path(second.file_id) == Path(second.path)

    def test_save_attachment_bytes_avoids_base64_round_trip(self, storage):
        payload = bytes(range(256))

        saved = storage.save_attachment_bytes(
            payload, filename="payload.bin", mime_type="application/octet-stream"
        )

        assert Path(saved.path).read_bytes() == payload
        assert storage.get_attachment_metadata(saved.file_id)["size"] == len(payload)


class TestSaveAttachmentFromPath:
    """save_attachment_from_path adopts an already-downloaded file (#994)."""

    @pytest.fixture
    def storage(self, tmp_path, monkeypatch):
        monkeypatch.setattr(attachment_storage, "STORAGE_DIR", tmp_path / "attachments")
        return AttachmentStorage()

    @staticmethod
    def _source(tmp_path, data=b"payload"):
        src = tmp_path / "download.tmp"
        src.write_bytes(data)
        return src

    def test_moves_source_into_storage(self, storage, tmp_path):
        src = self._source(tmp_path)

        saved = storage.save_attachment_from_path(str(src), filename="clip.mp4")

        assert Path(saved.path).read_bytes() == b"payload"
        assert not src.exists()

    def test_refreshes_source_mtime_before_moving_into_storage(self, storage, tmp_path):
        src = self._source(tmp_path)
        past = time.time() - 7200
        os.utime(src, (past, past))

        saved = storage.save_attachment_from_path(str(src), filename="clip.mp4")

        assert Path(saved.path).stat().st_mtime > past
        assert storage.sweep_expired() == 0
        assert Path(saved.path).exists()

    def test_stored_file_is_owner_only(self, storage, tmp_path):
        src = self._source(tmp_path)

        saved = storage.save_attachment_from_path(str(src), filename="clip.mp4")

        assert stat.S_IMODE(Path(saved.path).stat().st_mode) == 0o600

    def test_records_the_same_metadata_as_save_attachment(self, storage, tmp_path):
        src = self._source(tmp_path)

        saved = storage.save_attachment_from_path(
            str(src), filename="clip.mp4", mime_type="video/mp4"
        )
        metadata = storage.get_attachment_metadata(saved.file_id)

        assert metadata["original_filename"] == "clip.mp4"
        assert metadata["mime_type"] == "video/mp4"
        assert metadata["size"] == len(b"payload")
        assert storage.get_attachment_path(saved.file_id) == Path(saved.path)

    def test_keeps_original_stem_and_extension(self, storage, tmp_path):
        src = self._source(tmp_path)

        saved = storage.save_attachment_from_path(str(src), filename="clip.mp4")

        name = Path(saved.path).name
        assert name.startswith("clip_")
        assert name.endswith(".mp4")

    def test_falls_back_to_mime_extension_without_filename(self, storage, tmp_path):
        src = self._source(tmp_path)

        saved = storage.save_attachment_from_path(str(src), mime_type="application/pdf")

        assert Path(saved.path).name == f"{saved.file_id}.pdf"

    @pytest.mark.parametrize("failure_point", ("chmod", "utime", "record"))
    def test_removes_partially_saved_file_when_finalizing_fails(
        self, storage, tmp_path, monkeypatch, failure_point
    ):
        # The move can land before a later step fails; a file left behind here
        # has no metadata entry, so nothing would ever expire it.
        src = self._source(tmp_path)
        error = OSError("nope")
        if failure_point == "chmod":
            monkeypatch.setattr(attachment_storage.os, "chmod", Mock(side_effect=error))
        elif failure_point == "utime":
            monkeypatch.setattr(attachment_storage.os, "utime", Mock(side_effect=error))
        else:
            monkeypatch.setattr(storage, "_record", Mock(side_effect=error))

        with pytest.raises(OSError) as exc_info:
            storage.save_attachment_from_path(str(src), filename="clip.mp4")

        assert exc_info.value is error
        assert list(attachment_storage.STORAGE_DIR.iterdir()) == []


class TestSweepExpired:
    """Tests for mtime-based expiry."""

    @pytest.fixture
    def storage(self, tmp_path, monkeypatch):
        monkeypatch.setattr(attachment_storage, "STORAGE_DIR", tmp_path / "attachments")
        return AttachmentStorage(expiration_seconds=3600)

    @staticmethod
    def _orphan(name="orphan.bin", age_seconds=0):
        """Write a file without a metadata entry."""
        attachment_storage._ensure_storage_dir()
        path = attachment_storage.STORAGE_DIR / name
        path.write_bytes(b"payload")
        if age_seconds:
            past = time.time() - age_seconds
            os.utime(path, (past, past))
        return path

    def test_removes_orphaned_file_past_expiration(self, storage):
        stale = self._orphan("stale.bin", age_seconds=7200)

        removed = storage.sweep_expired()

        assert removed == 1
        assert not stale.exists()

    def test_keeps_orphaned_file_inside_expiration_window(self, storage):
        fresh = self._orphan("fresh.bin", age_seconds=60)

        removed = storage.sweep_expired()

        assert removed == 0
        assert fresh.exists()

    def test_drops_metadata_for_files_it_sweeps(self, storage):
        saved = storage.save_attachment(
            base64.urlsafe_b64encode(b"payload").decode(), filename="doc.pdf"
        )
        past = time.time() - 7200
        os.utime(saved.path, (past, past))

        assert storage.sweep_expired() == 1
        assert saved.file_id not in storage._metadata

    def test_leaves_directories_alone(self, storage):
        attachment_storage._ensure_storage_dir()
        nested = attachment_storage.STORAGE_DIR / "nested"
        nested.mkdir()
        past = time.time() - 7200
        os.utime(nested, (past, past))

        assert storage.sweep_expired() == 0
        assert nested.is_dir()

    def test_missing_storage_dir_is_not_an_error(self, storage):
        assert storage.sweep_expired() == 0

    def test_one_unremovable_file_does_not_abort_the_sweep(self, storage, monkeypatch):
        self._orphan("first.bin", age_seconds=7200)
        self._orphan("second.bin", age_seconds=7200)
        real_unlink = Path.unlink
        calls = {"n": 0}

        def flaky_unlink(self, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("permission denied")
            return real_unlink(self, *a, **kw)

        monkeypatch.setattr(Path, "unlink", flaky_unlink)

        assert storage.sweep_expired() == 1


class TestGetAttachmentStorageSweeps:
    """Tests for sweeping on singleton access."""

    def test_singleton_creation_sweeps_orphans(self, tmp_path, monkeypatch):
        monkeypatch.setattr(attachment_storage, "STORAGE_DIR", tmp_path / "attachments")
        monkeypatch.setattr(attachment_storage, "_attachment_storage", None)
        attachment_storage._ensure_storage_dir()
        stale = attachment_storage.STORAGE_DIR / "left-by-previous-process.bin"
        stale.write_bytes(b"payload")
        past = time.time() - 7200
        os.utime(stale, (past, past))

        attachment_storage.get_attachment_storage()

        assert not stale.exists()

    def test_later_access_reconsiders_files_kept_on_first_sweep(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(attachment_storage, "STORAGE_DIR", tmp_path / "attachments")
        monkeypatch.setattr(attachment_storage, "_attachment_storage", None)
        attachment_storage._ensure_storage_dir()
        orphan = attachment_storage.STORAGE_DIR / "previous-process.bin"
        orphan.write_bytes(b"payload")

        first = attachment_storage.get_attachment_storage()
        assert orphan.exists()

        past = time.time() - 7200
        os.utime(orphan, (past, past))
        second = attachment_storage.get_attachment_storage()

        assert first is second
        assert not orphan.exists()

    def test_scan_failure_is_best_effort_and_retried(
        self, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.setattr(attachment_storage, "STORAGE_DIR", tmp_path / "attachments")
        monkeypatch.setattr(attachment_storage, "_attachment_storage", None)
        attachment_storage._ensure_storage_dir()
        stale = attachment_storage.STORAGE_DIR / "stale.bin"
        stale.write_bytes(b"payload")
        past = time.time() - 7200
        os.utime(stale, (past, past))

        real_iterdir = Path.iterdir
        calls = {"n": 0}

        def flaky_iterdir(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("permission denied")
            return real_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", flaky_iterdir)

        first = attachment_storage.get_attachment_storage()
        assert stale.exists()
        second = attachment_storage.get_attachment_storage()

        assert first is second
        assert not stale.exists()
        assert "Failed to scan attachment storage" in caplog.text
