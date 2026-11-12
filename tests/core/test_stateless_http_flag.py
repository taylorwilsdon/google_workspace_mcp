"""WORKSPACE_MCP_STATELESS_HTTP turns on sessionless streamable HTTP on its own."""

import pytest

from core.server import stateless_http_flag_set


@pytest.mark.parametrize(
    "flag, expected",
    [
        (None, False),
        ("true", True),
        ("TRUE", True),
        (" true ", True),
        ("false", False),
        ("1", False),
    ],
)
def test_stateless_http_flag_set(monkeypatch, flag, expected):
    if flag is None:
        monkeypatch.delenv("WORKSPACE_MCP_STATELESS_HTTP", raising=False)
    else:
        monkeypatch.setenv("WORKSPACE_MCP_STATELESS_HTTP", flag)
    assert stateless_http_flag_set() is expected
