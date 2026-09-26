"""Recording stand-in for a googleapiclient service other than Directory.

Each request is recorded as ``(operation_id, kwargs)``, where the operation ID
is ``<family>.<resource>...<method>`` as in the registry. Responses come from
per-operation handlers; an operation without a handler fails the test. An
attribute is a method when its ID has a handler or it is called with arguments,
and a nested resource otherwise. ``root`` names the resource that holds an API's
top-level methods (Alert Center's ``v1beta1``), which adds nothing to the ID.
"""

from typing import Callable


class _Request:
    def __init__(self, run):
        self._run = run

    def execute(self):
        return self._run()


class FakeGoogleApi:
    def __init__(
        self,
        family: str,
        handlers: dict[str, Callable] | None = None,
        root: str = "",
    ):
        self.family = family
        self.root = root
        self.handlers = dict(handlers or {})
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def close(self):
        self.closed = True

    def __getattr__(self, resource: str):
        if resource.startswith("_"):
            raise AttributeError(resource)
        prefix = self.family if resource == self.root else f"{self.family}.{resource}"
        return lambda: _Resource(self, prefix)

    def dispatch(self, operation_id: str, kwargs: dict):
        if operation_id not in self.handlers:
            raise AssertionError(f"FakeGoogleApi does not model {operation_id}")
        response = self.handlers[operation_id]
        if isinstance(response, Exception):
            raise response
        return response(**kwargs) if callable(response) else response


class _Resource:
    def __init__(self, api: FakeGoogleApi, prefix: str):
        self._api = api
        self._prefix = prefix

    def __getattr__(self, name: str):
        operation_id = f"{self._prefix}.{name}"

        def call(**kwargs):
            if not kwargs and operation_id not in self._api.handlers:
                return _Resource(self._api, operation_id)
            self._api.calls.append((operation_id, kwargs))
            return _Request(lambda: self._api.dispatch(operation_id, kwargs))

        return call
