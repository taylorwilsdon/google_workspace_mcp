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


@pytest.mark.parametrize(
    "rel_dir",
    [".kube", ".gnupg", ".config/gcloud", ".google_workspace_mcp/credentials"],
)
def test_validate_file_path_blocks_files_inside_sensitive_home_dirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rel_dir: str
) -> None:
    storage_dir = tmp_path / "attachments"
    storage_dir.mkdir()
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", storage_dir)
    monkeypatch.delenv("WORKSPACE_MCP_CREDENTIALS_DIR", raising=False)
    monkeypatch.delenv("GOOGLE_MCP_CREDENTIALS_DIR", raising=False)

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home_dir))
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(home_dir))

    secret_dir = home_dir.joinpath(*rel_dir.split("/"))
    secret_dir.mkdir(parents=True)
    secret_file = secret_dir / "secret.json"
    secret_file.write_text("dummy", encoding="utf-8")

    with pytest.raises(ValueError, match="commonly contains secrets or credentials"):
        validate_file_path(str(secret_file))


def test_validate_file_path_blocks_custom_credentials_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage_dir = tmp_path / "attachments"
    storage_dir.mkdir()
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", storage_dir)

    allowed_dir = tmp_path / "allowed"
    creds_dir = allowed_dir / "tokens"
    creds_dir.mkdir(parents=True)
    token_file = creds_dir / "user@example.com.json"
    token_file.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(allowed_dir))
    monkeypatch.setenv("WORKSPACE_MCP_CREDENTIALS_DIR", str(creds_dir))

    with pytest.raises(ValueError, match="commonly contains secrets or credentials"):
        validate_file_path(str(token_file))


def test_validate_file_path_blocks_differently_cased_sensitive_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage_dir = tmp_path / "attachments"
    storage_dir.mkdir()
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", storage_dir)

    home_dir = tmp_path / "home"
    secret_dir = home_dir / ".kube"
    secret_dir.mkdir(parents=True)
    (secret_dir / "config").write_text("dummy", encoding="utf-8")
    if not (home_dir / ".KUBE").exists():
        pytest.skip("filesystem is case-sensitive")

    monkeypatch.delenv("WORKSPACE_MCP_CREDENTIALS_DIR", raising=False)
    monkeypatch.delenv("GOOGLE_MCP_CREDENTIALS_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home_dir))
    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(home_dir))

    with pytest.raises(ValueError, match="commonly contains secrets or credentials"):
        validate_file_path(str(home_dir / ".KUBE" / "config"))


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


def test_validate_file_path_blocks_docker_config_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage_dir = tmp_path / "attachments"
    storage_dir.mkdir()
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", storage_dir)

    allowed_dir = tmp_path / "allowed"
    docker_dir = allowed_dir / ".docker"
    docker_dir.mkdir(parents=True)
    secret_file = docker_dir / "config.json"
    secret_file.write_text('{"auths": {}}', encoding="utf-8")
    other_config = allowed_dir / "config.json"
    other_config.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(allowed_dir))

    with pytest.raises(ValueError, match="commonly contains secrets or credentials"):
        validate_file_path(str(secret_file))
    assert validate_file_path(str(other_config)) == other_config.resolve()


@pytest.mark.parametrize("link", ["symlink_to", "hardlink_to"])
def test_validate_file_path_blocks_aliased_home_docker_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, link: str
) -> None:
    storage_dir = tmp_path / "attachments"
    storage_dir.mkdir()
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", storage_dir)
    monkeypatch.delenv("WORKSPACE_MCP_CREDENTIALS_DIR", raising=False)
    monkeypatch.delenv("GOOGLE_MCP_CREDENTIALS_DIR", raising=False)

    home_dir = tmp_path / "home"
    docker_dir = home_dir / ".docker"
    docker_dir.mkdir(parents=True)
    target = home_dir / "dotfiles" / "docker" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"auths": {}}', encoding="utf-8")
    getattr(docker_dir / "config.json", link)(target)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home_dir))
    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(home_dir))

    for requested in (docker_dir / "config.json", target):
        with pytest.raises(
            ValueError, match="commonly contains secrets or credentials"
        ):
            validate_file_path(str(requested))
