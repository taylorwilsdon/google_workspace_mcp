"""Schema-driven CLI output for tool listing and discovery."""

from typing import Optional

from core.tool_schema import SchemaRegistry, ToolTier


def list_tools_by_schema(
    registry: SchemaRegistry,
    tier: Optional[ToolTier] = None,
    service_filter: Optional[str] = None,
) -> str:
    """Schema-aware tool listing grouped by service."""
    lines = []
    total = 0

    for service in sorted(registry.all_services()):
        if service_filter and service != service_filter:
            continue

        schemas = registry.by_service(service)
        if tier:
            order = [ToolTier.CORE, ToolTier.EXTENDED, ToolTier.COMPLETE]
            allowed = set(order[: order.index(tier) + 1])
            schemas = [s for s in schemas if s.tier in allowed]

        if not schemas:
            continue

        lines.append(f"  {service.upper()} ({len(schemas)} tools):")
        for schema in sorted(schemas, key=lambda s: (s.tier.value, s.name)):
            tier_badge = {"core": "[C]", "extended": "[E]", "complete": "[+]"}[
                schema.tier.value
            ]
            rw = "R" if schema.read_only else "W"
            desc = (
                schema.description[:65] + "..."
                if len(schema.description) > 65
                else schema.description
            )
            lines.append(f"    {tier_badge} {rw} {schema.name}")
            if desc:
                lines.append(f"         {desc}")
            total += 1
        lines.append("")

    header = f"Available tools ({total}):"
    if tier:
        header += f" [tier: {tier.value}]"
    return header + "\n\n" + "\n".join(lines)


def discover_service(
    registry: SchemaRegistry, service: str, query: Optional[str] = None
) -> str:
    """Detailed discovery for a specific service."""
    schemas = registry.by_service(service)
    if not schemas:
        return f"No tools found for service '{service}'."

    if query:
        schemas = [
            s
            for s in schemas
            if query.lower() in s.name.lower()
            or any(query.lower() in t for t in s.tags)
        ]

    lines = [f"Service: {service} ({len(schemas)} tools)", ""]

    for schema in sorted(schemas, key=lambda s: s.name):
        lines.append(f"  {schema.name}")
        lines.append(
            f"    Tier: {schema.tier.value} | "
            f"{'Read-only' if schema.read_only else 'Read-write'}"
        )
        lines.append(f"    Scopes: {', '.join(schema.scopes) or 'none'}")
        if schema.tags:
            lines.append(f"    Tags: {', '.join(schema.tags)}")
        if schema.paginated:
            lines.append("    Paginated: yes")
        if schema.multi_service:
            lines.append("    Multi-service: yes")
        lines.append("")

    return "\n".join(lines)
