"""
Unit tests for Drive SSRF protections and DNS pinning helpers.
"""

import os
import socket
import sys

import httpx
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gdrive import drive_tools


def test_resolve_and_validate_host_fails_closed_on_dns_error(monkeypatch):
    """DNS resolution failures must fail closed."""

    def fake_getaddrinfo(hostname, port):
        raise socket.gaierror("mocked resolution failure")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValueError, match="Refusing request \\(fail-closed\\)"):
        drive_tools._resolve_and_validate_host("example.com")


def test_resolve_and_validate_host_rejects_ipv6_private(monkeypatch):
    """IPv6 internal addresses must be rejected."""

    def fake_getaddrinfo(hostname, port):
        return [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                6,
                "",
                ("fd00::1", 0, 0, 0),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValueError, match="private/internal networks"):
        drive_tools._resolve_and_validate_host("ipv6-internal.example")


def test_resolve_and_validate_host_deduplicates_addresses(monkeypatch):
    """Duplicate DNS answers should be de-duplicated while preserving order."""

    def fake_getaddrinfo(hostname, port):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34", 0),
            ),
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34", 0),
            ),
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                6,
                "",
                ("2606:2800:220:1:248:1893:25c8:1946", 0, 0, 0),
            ),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    assert drive_tools._resolve_and_validate_host("example.com") == [
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946",
    ]


@pytest.mark.asyncio
async def test_fetch_url_with_pinned_ip_uses_pinned_target_and_host_header(monkeypatch):
    """Requests should target a validated IP while preserving Host + SNI hostname."""
    captured = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def build_request(self, method, url, headers=None, extensions=None):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["extensions"] = extensions or {}
            return {"url": url}

        async def send(self, request):
            return httpx.Response(200, request=httpx.Request("GET", request["url"]))

    monkeypatch.setattr(
        drive_tools, "_validate_url_not_internal", lambda url: ["93.184.216.34"]
    )
    monkeypatch.setattr(drive_tools.httpx, "AsyncClient", FakeAsyncClient)

    response = await drive_tools._fetch_url_with_pinned_ip(
        "https://example.com/path/to/file.txt?x=1"
    )

    assert response.status_code == 200
    assert captured["method"] == "GET"
    assert captured["url"] == "https://93.184.216.34/path/to/file.txt?x=1"
    assert captured["headers"]["Host"] == "example.com"
    assert captured["extensions"]["sni_hostname"] == "example.com"
    assert captured["client_kwargs"]["trust_env"] is False
    assert captured["client_kwargs"]["follow_redirects"] is False


@pytest.mark.asyncio
async def test_ssrf_safe_fetch_follows_relative_redirects(monkeypatch):
    """Relative redirects should be resolved and re-checked."""
    calls = []

    async def fake_fetch(url):
        calls.append(url)
        if len(calls) == 1:
            return httpx.Response(
                302,
                headers={"location": "/next"},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(200, request=httpx.Request("GET", url), content=b"ok")

    monkeypatch.setattr(drive_tools, "_fetch_url_with_pinned_ip", fake_fetch)

    response = await drive_tools._ssrf_safe_fetch("https://example.com/start")

    assert response.status_code == 200
    assert calls == ["https://example.com/start", "https://example.com/next"]


@pytest.mark.asyncio
async def test_ssrf_safe_fetch_rejects_disallowed_redirect_scheme(monkeypatch):
    """Redirects to non-http(s) schemes should be blocked."""

    async def fake_fetch(url):
        return httpx.Response(
            302,
            headers={"location": "file:///etc/passwd"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(drive_tools, "_fetch_url_with_pinned_ip", fake_fetch)

    with pytest.raises(ValueError, match="Redirect to disallowed scheme"):
        await drive_tools._ssrf_safe_fetch("https://example.com/start")
