"""
Code Mode engine for Workspace MCP.

Generates typed Python API stubs from SchemaRegistry and provides
an AST-validated sandbox for LLM-generated code execution.
"""

import ast
import io
import logging
import textwrap
from contextlib import redirect_stdout
from typing import Any, Dict

from core.tool_registry import get_tool_components
from core.tool_schema import SchemaRegistry

logger = logging.getLogger(__name__)

FORBIDDEN_NODES = (ast.Import, ast.ImportFrom)

FORBIDDEN_BUILTINS = frozenset(
    {
        "__import__",
        "exec",
        "eval",
        "compile",
        "open",
        "globals",
        "locals",
        "breakpoint",
        "exit",
        "quit",
        "input",
        "memoryview",
        "type",
        "__build_class__",
    }
)

EXECUTION_TIMEOUT = 30
MAX_OUTPUT_SIZE = 50_000


class CodeModeError(Exception):
    """Raised when code validation or execution fails."""


def generate_api_stubs(registry: SchemaRegistry, server) -> str:
    """Generate typed Python API surface from the schema registry."""
    tool_components = get_tool_components(server)
    lines = []

    for service_name in sorted(registry.all_services()):
        schemas = registry.by_service(service_name)
        if not schemas:
            continue

        lines.append(f"class {service_name}:")
        for schema in sorted(schemas, key=lambda s: s.name):
            component = tool_components.get(schema.name)
            if component is None:
                continue

            params = _extract_user_params(component)
            sig = ", ".join(params)
            method_name = _to_method_name(schema)
            desc = schema.description or schema.name

            lines.append(f"    async def {method_name}({sig}) -> str:")
            lines.append(f'        """{desc}"""')
            lines.append("        ...")
        lines.append("")

    return "\n".join(lines)


def _extract_user_params(tool_component) -> list[str]:
    """Extract user-facing parameter signatures from a tool component."""
    params = []
    if hasattr(tool_component, "parameters") and isinstance(
        tool_component.parameters, dict
    ):
        props = tool_component.parameters.get("properties", {})
        required = set(tool_component.parameters.get("required", []))
        for name, prop in props.items():
            ptype = prop.get("type", "Any")
            type_map = {
                "string": "str",
                "integer": "int",
                "boolean": "bool",
                "number": "float",
                "array": "list",
                "object": "dict",
            }
            py_type = type_map.get(ptype, "Any")
            default = prop.get("default")
            if name in required:
                params.append(f"{name}: {py_type}")
            elif default is not None:
                params.append(f"{name}: {py_type} = {default!r}")
            else:
                params.append(f"{name}: {py_type} = None")
    return params


def _to_method_name(schema) -> str:
    """Convert tool name to method name by stripping service prefix."""
    name = schema.name
    service = schema.service

    prefixes = [
        f"{service}_",
        f"g{service}_",
    ]
    prefix_overrides = {
        "appscript": ["script_", "apps_script_"],
        "search": ["search_custom"],
        "contacts": ["contact_", "contacts_"],
    }

    for prefix in prefixes:
        if name.startswith(prefix):
            return name[len(prefix) :]

    for prefix in prefix_overrides.get(service, []):
        if name.startswith(prefix):
            remainder = name[len(prefix) :]
            return remainder if remainder else name

    return name


def validate_code(code: str) -> ast.Module:
    """Parse and validate code against sandbox restrictions."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise CodeModeError(f"Syntax error: {e}") from e

    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_NODES):
            raise CodeModeError(
                f"Forbidden: {type(node).__name__} is not allowed. "
                "Use the provided API classes directly."
            )
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_BUILTINS:
            raise CodeModeError(
                f"Forbidden: '{node.id}' is not available in the sandbox."
            )
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise CodeModeError(
                f"Forbidden: dunder attribute access '{node.attr}' is not allowed."
            )

    return tree


class ServiceProxy:
    """Thin async proxy that maps method calls to actual tool functions."""

    def __init__(self, service_name: str, tool_map: Dict[str, Any], auth_context: dict):
        self._service = service_name
        self._tools = tool_map
        self._auth = auth_context

    def __getattr__(self, method_name: str):
        tool_fn = self._tools.get(method_name)
        if tool_fn is None:
            available = ", ".join(sorted(self._tools.keys()))
            raise AttributeError(
                f"{self._service}.{method_name} does not exist. Available: {available}"
            )

        async def proxy(**kwargs):
            return await tool_fn(**kwargs)

        return proxy

    def __repr__(self) -> str:
        return f"<{self._service} API: {len(self._tools)} methods>"


def build_namespace(registry: SchemaRegistry, server, auth_context: dict) -> dict:
    """Build the sandbox namespace with API proxy classes."""
    tool_components = get_tool_components(server)
    namespace = {}

    safe_builtins = {
        "print": print,
        "len": len,
        "range": range,
        "enumerate": enumerate,
        "zip": zip,
        "map": map,
        "filter": filter,
        "sorted": sorted,
        "reversed": reversed,
        "list": list,
        "dict": dict,
        "set": set,
        "tuple": tuple,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "isinstance": isinstance,
        "hasattr": hasattr,
        "getattr": getattr,
        "min": min,
        "max": max,
        "sum": sum,
        "abs": abs,
        "round": round,
        "any": any,
        "all": all,
        "repr": repr,
        "True": True,
        "False": False,
        "None": None,
    }
    namespace["__builtins__"] = safe_builtins

    for service_name in sorted(registry.all_services()):
        schemas = registry.by_service(service_name)
        tool_map = {}
        for schema in schemas:
            component = tool_components.get(schema.name)
            if component is None:
                continue
            fn = getattr(component, "fn", None) or component
            method_name = _to_method_name(schema)
            tool_map[method_name] = fn

        if tool_map:
            namespace[service_name] = ServiceProxy(service_name, tool_map, auth_context)

    return namespace


async def execute_code(code: str, namespace: dict) -> str:
    """Execute LLM-generated code in the sandbox."""
    validate_code(code)

    indented = textwrap.indent(code, "    ")
    wrapped = f"async def __code_mode_main__():\n{indented}\n"

    compiled = compile(ast.parse(wrapped), "<code-mode>", "exec")
    exec(compiled, namespace)  # noqa: S102

    stdout_buf = io.StringIO()
    with redirect_stdout(stdout_buf):
        result = await namespace["__code_mode_main__"]()

    output_parts = []
    captured = stdout_buf.getvalue()
    if captured:
        output_parts.append(captured.rstrip())
    if result is not None:
        output_parts.append(f"\n=> {result}")

    output = "\n".join(output_parts) if output_parts else "(no output)"

    if len(output) > MAX_OUTPUT_SIZE:
        output = (
            output[:MAX_OUTPUT_SIZE] + f"\n... (truncated at {MAX_OUTPUT_SIZE} chars)"
        )

    return output


def register_code_mode(server) -> None:
    """Register the execute_code meta-tool on the server."""
    registry = SchemaRegistry.instance()
    stubs = generate_api_stubs(registry, server)

    description = (
        "Execute Python code against the Google Workspace API. "
        "The following typed API is available in the sandbox:\n\n"
        f"```python\n{stubs}\n```\n\n"
        "Call methods with `await`, e.g.: "
        "`results = await gmail.search_messages(query='from:boss')`\n"
        "Use `print()` to output results. No imports needed."
    )

    @server.tool(description=description)
    async def execute_workspace_code(code: str) -> str:
        """Execute Python code against the Google Workspace API."""
        auth_context = {}
        try:
            from fastmcp import Context

            ctx = Context.current()
            auth_context["access_token"] = await ctx.get_state("access_token")
            auth_context["user_email"] = await ctx.get_state("user_email")
        except Exception:
            pass

        ns = build_namespace(registry, server, auth_context)
        return await execute_code(code, ns)
