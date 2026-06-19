"""Tests for ``core.log_formatter`` log-directory resolution and filters."""

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from core import log_formatter
from core.log_formatter import SuppressStatelessTransportTerminationFilter


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Ensure each test starts with the relevant env vars unset."""
    monkeypatch.delenv("WORKSPACE_MCP_LOG_DIR", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_STATELESS_MODE", raising=False)
    yield


def test_resolve_log_dir_defaults_to_home_workspace_mcp_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        os.path, "expanduser", lambda p: "/home/user" if p == "~" else p
    )

    resolved = log_formatter._resolve_log_dir()

    assert resolved == os.path.join("/home/user", ".google_workspace_mcp", "logs")


def test_resolve_log_dir_honors_workspace_mcp_log_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORKSPACE_MCP_LOG_DIR", str(tmp_path))

    resolved = log_formatter._resolve_log_dir()

    assert resolved == str(tmp_path)


def test_resolve_log_dir_expands_user_home_in_env_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKSPACE_MCP_LOG_DIR", "~/custom-logs")
    monkeypatch.setattr(
        os.path,
        "expanduser",
        lambda p: "/home/user/custom-logs" if p == "~/custom-logs" else p,
    )

    resolved = log_formatter._resolve_log_dir()

    assert resolved == "/home/user/custom-logs"


def test_configure_file_logging_writes_into_override_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORKSPACE_MCP_LOG_DIR", str(tmp_path))

    logger_name: str = "tests.log_formatter.override"
    target_logger: logging.Logger = logging.getLogger(logger_name)
    # Drop any prior handlers so we don't leak state between tests
    for handler in list(target_logger.handlers):
        target_logger.removeHandler(handler)

    try:
        assert log_formatter.configure_file_logging(logger_name) is True
        expected_path = tmp_path / "mcp_server_debug.log"
        assert expected_path.exists()
        file_handlers = [
            h for h in target_logger.handlers if isinstance(h, logging.FileHandler)
        ]
        assert any(
            os.path.abspath(h.baseFilename) == os.path.abspath(str(expected_path))
            for h in file_handlers
        )
    finally:
        for handler in list(target_logger.handlers):
            handler.close()
            target_logger.removeHandler(handler)


def test_configure_file_logging_disabled_in_stateless_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORKSPACE_MCP_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_MODE", "true")

    assert (
        log_formatter.configure_file_logging("tests.log_formatter.stateless") is False
    )
    assert not (tmp_path / "mcp_server_debug.log").exists()


def _record(name: str, message: str, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_stateless_transport_none_termination_log_is_suppressed() -> None:
    log_filter = SuppressStatelessTransportTerminationFilter()

    assert not log_filter.filter(
        _record("mcp.server.streamable_http", "Terminating session: None")
    )


def test_transport_termination_with_real_session_is_not_suppressed() -> None:
    log_filter = SuppressStatelessTransportTerminationFilter()

    assert log_filter.filter(
        _record("mcp.server.streamable_http", "Terminating session: session-123")
    )


def test_unrelated_none_message_is_not_suppressed() -> None:
    log_filter = SuppressStatelessTransportTerminationFilter()

    assert log_filter.filter(_record("auth.google_auth", "Terminating session: None"))
