"""
Middleware that advertises tool input schemas every function-calling client accepts.
"""

from typing import Any, Dict, Sequence

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool

_NULL_SCHEMA = {"type": "null"}
# Keywords whose value is a single subschema, a list of them, or a name -> subschema map.
_SUBSCHEMA_KEYS = ("items", "additionalProperties", "not")
_SUBSCHEMA_LIST_KEYS = ("anyOf", "oneOf", "allOf", "prefixItems")
_SUBSCHEMA_MAP_KEYS = ("properties", "patternProperties", "$defs", "definitions")


def portable_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``schema`` without null unions or ``const``.

    Pydantic renders ``Optional[T] = None`` as ``anyOf: [T, {type: null}]`` and
    ``Literal["x"]`` as ``const: "x"``. Gemini's function-declaration schema
    has neither list-valued ``type`` nor ``const``, so it rejects the whole
    catalog. Optional parameters are already absent from ``required``, and
    arguments are validated against the Python signature rather than this
    schema, so dropping the null branch changes nothing at runtime.
    """
    node = dict(schema)
    for key in _SUBSCHEMA_KEYS:
        if isinstance(node.get(key), dict):
            node[key] = portable_schema(node[key])
    for key in _SUBSCHEMA_LIST_KEYS:
        if key in node:
            node[key] = [portable_schema(sub) for sub in node[key]]
    for key in _SUBSCHEMA_MAP_KEYS:
        if key in node:
            node[key] = {name: portable_schema(sub) for name, sub in node[key].items()}

    if "const" in node:
        node["enum"] = [node.pop("const")]
    if isinstance(node.get("type"), list):
        types = [t for t in node["type"] if t != "null"]
        node["type"] = types[0] if len(types) == 1 else types
    for key in ("anyOf", "oneOf"):
        if _NULL_SCHEMA not in node.get(key, ()):
            continue
        variants = [sub for sub in node.pop(key) if sub != _NULL_SCHEMA]
        if len(variants) == 1:
            node = {**variants[0], **node}
        else:
            node[key] = variants
    return node


class PortableSchemaMiddleware(Middleware):
    """Rewrite each listed tool's input schema with :func:`portable_schema`.

    See https://github.com/taylorwilsdon/google_workspace_mcp/issues/1099
    """

    async def on_list_tools(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Sequence[Tool]:
        tools = await call_next(context)
        return [
            tool.model_copy(update={"parameters": portable_schema(tool.parameters)})
            for tool in tools
        ]
