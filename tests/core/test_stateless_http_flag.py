"""WORKSPACE_MCP_STATELESS_HTTP turns on sessionless streamable HTTP on its own."""

import pytest

from core.server import get_stateless_http


@pytest.mark.parametrize(
    "flag, expected",
    [
        (None, False),
        ("", False),
        ("true", True),
        ("TRUE", True),
        (" true ", True),
        ("false", False),
    ],
)
def test_get_stateless_http(
    monkeypatch: pytest.MonkeyPatch, flag: str | None, expected: bool
) -> None:
    if flag is None:
        monkeypatch.delenv("WORKSPACE_MCP_STATELESS_HTTP", raising=False)
    else:
        monkeypatch.setenv("WORKSPACE_MCP_STATELESS_HTTP", flag)
    assert get_stateless_http() is expected


@pytest.mark.parametrize("flag", ["1", "yes", "on", "treu"])
def test_get_stateless_http_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_HTTP", flag)
    with pytest.raises(ValueError, match="WORKSPACE_MCP_STATELESS_HTTP"):
        get_stateless_http()
