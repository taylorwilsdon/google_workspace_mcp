"""Transport for a real googleapiclient client in tests.

The client builds its own request from the discovery document; this transport
records what reaches the wire as ``(verb, path, query, body)`` and answers from
handlers keyed by ``(verb, path)``. A handler is a response dict, a callable
taking ``(query, body)``, or an int HTTP error status. An unmodeled request
fails with 404.
"""

import json
from urllib.parse import parse_qsl, unquote, urlsplit

import httplib2
from googleapiclient.discovery import build

from gadmin.client import pinned_client


class RoutingHttp:
    def __init__(self, handlers: dict | None = None):
        self.handlers = dict(handlers or {})
        self.requests: list[tuple[str, str, dict, dict | None]] = []
        self.closed = False

    def close(self):
        self.closed = True

    def request(self, uri, method="GET", body=None, headers=None, **_):
        parts = urlsplit(uri)
        path = unquote(parts.path)
        query = {k: v for k, v in parse_qsl(parts.query) if k != "alt"}
        payload = json.loads(body) if body else None
        self.requests.append((method, path, query, payload))
        handler = self.handlers.get((method, path), 404)
        if callable(handler):
            handler = handler(query, payload)
        if isinstance(handler, int):
            error = {"error": {"code": handler, "message": f"{path} is private"}}
            return httplib2.Response({"status": handler}), json.dumps(error).encode()
        return httplib2.Response({"status": 200}), json.dumps(handler).encode()


def cloud_identity_client(handlers: dict | None = None, version: str = "v1"):
    """A real Cloud Identity client, rebuilt from the pinned document as in
    production, over a RoutingHttp; returns (client, transport)."""
    http = RoutingHttp(handlers)
    static = build("cloudidentity", version, http=http)
    return pinned_client(static, "cloudidentity", version), http


def chrome_client(service: str, handlers: dict | None = None):
    """A real Chrome Management or Chrome Policy client, built as in production
    (Chrome Management from its pinned document), over a RoutingHttp; returns
    (client, transport)."""
    http = RoutingHttp(handlers)
    return pinned_client(build(service, "v1", http=http), service, "v1"), http


def directory_client(handlers: dict | None = None):
    """A real Directory client from the installed static document over a
    RoutingHttp; returns (client, transport)."""
    http = RoutingHttp(handlers)
    return build("admin", "directory_v1", http=http), http
