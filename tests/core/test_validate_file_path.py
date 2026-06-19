import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import core.attachment_storage as attachment_storage
from core.utils import _get_allowed_file_dirs, validate_file_path


def test_validate_file_path_allows_attachment_storage_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOWED_FILE_DIRS", raising=False)
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", tmp_path)

    file_path = tmp_path / "downloaded.txt"
    file_path.write_text("attachment", encoding="utf-8")

    assert validate_file_path(str(file_path)) == file_path.resolve()


def test_validate_file_path_blocks_home_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOWED_FILE_DIRS", raising=False)
    storage_dir = tmp_path / "attachments"
    storage_dir.mkdir()
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", storage_dir)

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home_dir))

    secret_file = home_dir / ".bash_history"
    secret_file.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="outside permitted directories"):
        validate_file_path(str(secret_file))


def test_validate_file_path_keeps_attachment_storage_allowed_with_custom_allowlist(
    tmp_path, monkeypatch
):
    storage_dir = tmp_path / "attachments"
    storage_dir.mkdir()
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", storage_dir)

    extra_dir = tmp_path / "shared"
    extra_dir.mkdir()
    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(extra_dir))

    stored_file = storage_dir / "saved.txt"
    stored_file.write_text("attachment", encoding="utf-8")
    shared_file = extra_dir / "report.txt"
    shared_file.write_text("report", encoding="utf-8")

    assert validate_file_path(str(stored_file)) == stored_file.resolve()
    assert validate_file_path(str(shared_file)) == shared_file.resolve()


def test_validate_file_path_strips_whitespace_from_allowed_file_dirs(
    tmp_path, monkeypatch
):
    storage_dir = tmp_path / "attachments"
    storage_dir.mkdir()
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", storage_dir)

    spaced_dir = tmp_path / "shared"
    spaced_dir.mkdir()
    monkeypatch.setenv("ALLOWED_FILE_DIRS", f"  {spaced_dir}  ")

    shared_file = spaced_dir / "report.txt"
    shared_file.write_text("report", encoding="utf-8")

    assert validate_file_path(str(shared_file)) == shared_file.resolve()


def test_get_allowed_file_dirs_strips_whitespace(monkeypatch, tmp_path):
    storage_dir = tmp_path / "attachments"
    storage_dir.mkdir()
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", storage_dir)

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    monkeypatch.setenv("ALLOWED_FILE_DIRS", f"  {allowed_dir}  ")

    assert _get_allowed_file_dirs() == [storage_dir.resolve(), allowed_dir.resolve()]


def test_validate_file_path_blocks_dot_ssh_anywhere(monkeypatch, tmp_path):
    storage_dir = tmp_path / "attachments"
    storage_dir.mkdir()
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", storage_dir)

    allowed_dir = tmp_path / "allowed"
    secret_dir = allowed_dir / "nested" / ".ssh"
    secret_dir.mkdir(parents=True)
    secret_file = secret_dir / "config"
    secret_file.write_text("host example", encoding="utf-8")

    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(allowed_dir))

    with pytest.raises(ValueError, match="commonly contains secrets or credentials"):
        validate_file_path(str(secret_file))


def test_validate_file_path_blocks_dot_aws_anywhere(monkeypatch, tmp_path):
    storage_dir = tmp_path / "attachments"
    storage_dir.mkdir()
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", storage_dir)

    allowed_dir = tmp_path / "allowed"
    secret_dir = allowed_dir / "team" / ".aws"
    secret_dir.mkdir(parents=True)
    secret_file = secret_dir / "credentials"
    secret_file.write_text("[default]", encoding="utf-8")

    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(allowed_dir))

    with pytest.raises(ValueError, match="commonly contains secrets or credentials"):
        validate_file_path(str(secret_file))


def test_validate_file_path_blocks_dot_env_variant_anywhere(monkeypatch, tmp_path):
    storage_dir = tmp_path / "attachments"
    storage_dir.mkdir()
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", storage_dir)

    allowed_dir = tmp_path / "allowed"
    secret_dir = allowed_dir / "nested"
    secret_dir.mkdir(parents=True)
    secret_file = secret_dir / ".env.production"
    secret_file.write_text("API_KEY=secret", encoding="utf-8")

    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(allowed_dir))

    with pytest.raises(ValueError, match="\\.env files may contain secrets"):
        validate_file_path(str(secret_file))
