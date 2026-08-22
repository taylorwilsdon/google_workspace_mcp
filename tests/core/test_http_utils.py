"""Findings 6, 7 and 49: SSRF classification and cancellation safety in core.http_utils.

Findings 6/7: the check used to be `if not ip.is_global`, delegating the whole security
decision to a stdlib classification that has changed repeatedly. Python 3.10 reports
fc00::/7 and 100.64.0.0/10 as global, so both were reachable. The classification was
corrected again in 3.10.15 and 3.13, so "require a newer Python" would still leave the
code trusting a moving target -- hence an explicit table.

Finding 49: `ssrf_safe_stream` only closed its httpx client on `Exception`, but
`asyncio.CancelledError` derives from `BaseException`, so a cancelled task leaked the
client and its connection pool.
"""

import asyncio
import ipaddress
import socket

import httpx
import pytest

import core.http_utils as http_utils
from core.http_utils import (
    _DENIED_IPV4_NETWORKS,
    _DENIED_IPV6_NETWORKS,
    blocked_ip_reason,
    resolve_and_validate_host,
    ssrf_safe_stream,
)


class TestBlockedRanges:
    @pytest.mark.parametrize(
        "ip",
        [
            # Findings 6/7 specifically: these are the addresses Python 3.10 mislabels.
            "fc00::1",
            "fd12:3456:789a::1",
            "100.64.0.1",
            "100.127.255.254",
            # The rest of the private/internal space.
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.1.1",
            "169.254.169.254",  # cloud instance metadata
            "0.0.0.0",
            "224.0.0.1",
            "240.0.0.1",
            "255.255.255.255",
            "198.18.0.1",
            "192.0.2.1",
            "198.51.100.1",
            "203.0.113.1",
            "192.88.99.1",
            "192.0.0.1",
            "::",
            "::1",
            "fe80::1",
            "ff02::1",
            "2001:db8::1",
            "100::1",
            "2001:10::1",
            "2001:20::1",
        ],
    )
    def test_blocked(self, ip):
        assert blocked_ip_reason(ip) is not None

    @pytest.mark.parametrize(
        "ip",
        [
            "8.8.8.8",
            "1.1.1.1",
            "93.184.216.34",
            "2606:4700:4700::1111",
            "2a00:1450:4001:800::200e",
        ],
    )
    def test_allowed(self, ip):
        assert blocked_ip_reason(ip) is None

    @pytest.mark.parametrize(
        "ip",
        [
            # Same private destination written as IPv6. Checking only the IPv6 table
            # would miss these, so the embedded address is what gets validated.
            "::ffff:10.0.0.1",
            "::ffff:a00:1",
            "::ffff:127.0.0.1",
            "::ffff:169.254.169.254",
        ],
    )
    def test_ipv4_mapped_forms_are_unwrapped(self, ip):
        reason = blocked_ip_reason(ip)
        assert reason is not None
        assert "embeds" in reason

    @pytest.mark.parametrize(
        "ip",
        [
            # These prefixes carry an arbitrary embedded IPv4 destination, so the whole
            # prefix is refused rather than trying to parse each variant out.
            "64:ff9b::10.0.0.1",
            "64:ff9b::8.8.8.8",
            "2002:0a00:0001::",  # 6to4 wrapping 10.0.0.1
            "2001::1",  # Teredo
        ],
    )
    def test_ipv4_embedding_prefixes_are_refused_wholesale(self, ip):
        assert blocked_ip_reason(ip) is not None

    def test_unparsable_input_fails_closed(self):
        assert blocked_ip_reason("not-an-ip") is not None
        assert blocked_ip_reason("") is not None


class TestRangesPythonReportsAsGlobal:
    """Ranges that must be blocked even though ``is_global`` says otherwise.

    A review flagged that the explicit table omitted ``fec0::/10`` and ``::/96``,
    suggesting the ``is_global`` check it replaced had covered them. It had not:
    ``ipaddress`` does not list either prefix, so both were allowed before and after.
    These tests pin the ranges down independently of what the stdlib believes.
    """

    @pytest.mark.parametrize(
        "ip",
        [
            "fec0::1",  # RFC 3879 site-local: deprecated, but still routed in situ
            "fecf::1",
            "feff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
        ],
    )
    def test_deprecated_site_local_is_blocked(self, ip):
        assert blocked_ip_reason(ip) is not None

    @pytest.mark.parametrize(
        "ip",
        [
            # ::/96 is the deprecated IPv4-compatible form. Unlike ::ffff:0:0/96 it is
            # not recognised by `ipv4_mapped`, so an internal IPv4 target written this
            # way used to sail through.
            "::10.0.0.1",
            "::a00:1",
            "::127.0.0.1",
            "::169.254.169.254",
            "::192.168.0.1",
            # The prefix is refused wholesale, so even a public embedded address is
            # blocked rather than being unwrapped and re-checked.
            "::8.8.8.8",
        ],
    )
    def test_ipv4_compatible_form_is_blocked(self, ip):
        assert blocked_ip_reason(ip) is not None

    @pytest.mark.parametrize(
        "ip",
        [
            "2001:2::1",  # benchmarking
            "2001:4:112::1",  # AS112
            "2001:1f::1",  # inside 2001::/23, between the sub-prefixes
            "3fff::1",  # RFC 9637 documentation
            "5f00::1",  # RFC 9602 SRv6 SIDs
        ],
    )
    def test_other_non_globally_reachable_prefixes_are_blocked(self, ip):
        assert blocked_ip_reason(ip) is not None

    @pytest.mark.parametrize(
        "ip",
        [
            "2001:0db9::1",  # one past the documentation prefix
            "2001:200::1",  # first address above 2001::/23
            "4000::1",  # above 3fff::/20
            "6000::1",  # above 5f00::/16
            "2a02:ffff::1",  # ordinary global unicast
            "::ffff:8.8.8.8",  # IPv4-mapped public address stays reachable
        ],
    )
    def test_addresses_just_outside_blocked_ranges_stay_allowed(self, ip):
        assert blocked_ip_reason(ip) is None

    def test_table_covers_every_range_python_treats_as_private(self):
        """Guard against a future table edit dropping below stdlib coverage.

        ``::ffff:0:0/96`` is excluded because it is handled by unwrapping the embedded
        IPv4 address instead of by a table entry.
        """
        ipv4_mapped = ipaddress.ip_network("::ffff:0.0.0.0/96")
        for family, table in (
            (ipaddress.IPv4Network, _DENIED_IPV4_NETWORKS),
            (ipaddress.IPv6Network, _DENIED_IPV6_NETWORKS),
        ):
            for reserved in family._constants._private_networks:
                if reserved == ipv4_mapped:
                    continue
                assert any(reserved.subnet_of(entry) for entry in table), (
                    f"{reserved} is not covered by the denylist"
                )


class TestHostResolution:
    @pytest.mark.asyncio
    async def test_every_resolved_address_is_checked(self, monkeypatch):
        """One public and one private answer must still be refused."""

        def fake_getaddrinfo(host, port):  # noqa: ARG001
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0)),
            ]

        monkeypatch.setattr(http_utils.socket, "getaddrinfo", fake_getaddrinfo)

        with pytest.raises(ValueError, match="10.0.0.0/8"):
            await resolve_and_validate_host("rebind.example")

    @pytest.mark.asyncio
    async def test_public_answers_are_returned_for_pinning(self, monkeypatch):
        def fake_getaddrinfo(host, port):  # noqa: ARG001
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::1", 0, 0, 0)),
            ]

        monkeypatch.setattr(http_utils.socket, "getaddrinfo", fake_getaddrinfo)

        ips = await resolve_and_validate_host("example.com")

        # De-duplicated, order preserved: these are what the connection is pinned to.
        assert ips == ["93.184.216.34", "2606:4700::1"]

    @pytest.mark.asyncio
    async def test_scoped_ipv6_answer_is_still_blocked(self, monkeypatch):
        """A zone suffix must not stop the address from parsing (and being refused)."""

        def fake_getaddrinfo(host, port):  # noqa: ARG001
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%en0", 0, 0, 2))
            ]

        monkeypatch.setattr(http_utils.socket, "getaddrinfo", fake_getaddrinfo)

        with pytest.raises(ValueError, match="fe80::/10"):
            await resolve_and_validate_host("linklocal.example")

    @pytest.mark.asyncio
    async def test_dns_failure_fails_closed(self, monkeypatch):
        def fake_getaddrinfo(host, port):  # noqa: ARG001
            raise socket.gaierror("nope")

        monkeypatch.setattr(http_utils.socket, "getaddrinfo", fake_getaddrinfo)

        with pytest.raises(ValueError, match="fail-closed"):
            await resolve_and_validate_host("unresolvable.example")


class _ClientFactory:
    """httpx.AsyncClient stand-in whose send() behaviour is set per test.

    Behaviour lives on the factory rather than being patched onto a class, so no
    test can leave a mutated class behind for the next one.
    """

    def __init__(self, send_behaviour):
        self._send_behaviour = send_behaviour
        self.instances: list["_TrackingClient"] = []

    def __call__(self, *args, **kwargs):  # noqa: ARG002
        client = _TrackingClient(self._send_behaviour)
        self.instances.append(client)
        return client

    @property
    def all_closed(self) -> bool:
        return bool(self.instances) and all(c.closed for c in self.instances)


class _TrackingClient:
    """Records whether it was closed, which is the whole point of finding 49."""

    def __init__(self, send_behaviour):
        self.closed = False
        self._send_behaviour = send_behaviour

    def build_request(self, *args, **kwargs):  # noqa: ARG002
        return object()

    async def send(self, request, stream=False):  # noqa: ARG002
        return await self._send_behaviour()

    async def aclose(self):
        self.closed = True


class _FakeResponse:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    async def aclose(self):
        self.closed = True


@pytest.fixture
def install_client(monkeypatch):
    """Install a client factory with the given send() behaviour; return the factory."""

    async def fake_validate(url):  # noqa: ARG001
        return ["93.184.216.34"]

    monkeypatch.setattr(http_utils, "validate_url_not_internal", fake_validate)

    def _install(send_behaviour):
        factory = _ClientFactory(send_behaviour)
        monkeypatch.setattr(http_utils.httpx, "AsyncClient", factory)
        return factory

    return _install


class TestStreamCancellationSafety:
    @pytest.mark.asyncio
    async def test_client_is_closed_when_send_is_cancelled(self, install_client):
        """Finding 49: CancelledError is a BaseException, so `except Exception` missed it."""

        async def cancel_during_send():
            raise asyncio.CancelledError()

        factory = install_client(cancel_during_send)

        with pytest.raises(asyncio.CancelledError):
            async with ssrf_safe_stream("https://example.com/f"):
                pass

        assert factory.all_closed

    @pytest.mark.asyncio
    async def test_client_is_closed_when_the_body_is_cancelled(self, install_client):
        """Cancellation while consuming the stream must not leak either."""
        response = _FakeResponse()

        async def succeed():
            return response

        factory = install_client(succeed)

        with pytest.raises(asyncio.CancelledError):
            async with ssrf_safe_stream("https://example.com/f"):
                raise asyncio.CancelledError()

        assert response.closed
        assert factory.all_closed

    @pytest.mark.asyncio
    async def test_client_is_closed_on_normal_completion(self, install_client):
        response = _FakeResponse()

        async def succeed():
            return response

        factory = install_client(succeed)

        async with ssrf_safe_stream("https://example.com/f") as resp:
            assert resp is response

        assert response.closed
        assert factory.all_closed

    @pytest.mark.asyncio
    async def test_client_is_closed_when_every_ip_fails(self, install_client):
        async def fail():
            raise httpx.ConnectError("refused")

        factory = install_client(fail)

        with pytest.raises(http_utils.SSRFFetchError):
            async with ssrf_safe_stream("https://example.com/f"):
                pass

        assert factory.all_closed


class TestStreamRedirectValidation:
    @pytest.mark.asyncio
    async def test_every_redirect_hop_is_revalidated(self, monkeypatch):
        """A redirect into private space must be refused, not followed."""
        validated: list[str] = []

        async def fake_validate(url):
            from urllib.parse import urlparse

            validated.append(url)
            return await resolve_and_validate_host(urlparse(url).hostname)

        def fake_getaddrinfo(host, port):  # noqa: ARG001
            if host == "internal.example":
                return [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))
                ]
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(http_utils.socket, "getaddrinfo", fake_getaddrinfo)
        monkeypatch.setattr(http_utils, "validate_url_not_internal", fake_validate)

        redirect = _FakeResponse(
            302, headers={"location": "https://internal.example/metadata"}
        )

        async def succeed():
            return redirect

        factory = _ClientFactory(succeed)
        monkeypatch.setattr(http_utils.httpx, "AsyncClient", factory)

        with pytest.raises(ValueError, match="169.254.0.0/16"):
            async with ssrf_safe_stream("https://example.com/start"):
                pass

        # The redirect target was validated, not just the original URL.
        assert validated == [
            "https://example.com/start",
            "https://internal.example/metadata",
        ]
        # The client used for the first hop was released before the failure.
        assert factory.all_closed
