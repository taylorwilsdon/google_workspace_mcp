"""
Unified tool schema system.

Provides a single source of truth for tool metadata: service, tier,
scopes, read-only status, pagination support, and semantic tags.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Set


class ToolTier(str, Enum):
    CORE = "core"
    EXTENDED = "extended"
    COMPLETE = "complete"


@dataclass(frozen=True)
class ToolSchema:
    """Structured metadata for a single MCP tool."""

    name: str
    service: str
    tier: ToolTier
    scopes: tuple[str, ...]
    read_only: bool = True
    description: str = ""
    tags: tuple[str, ...] = ()
    paginated: bool = False
    supports_upload: bool = False
    supports_download: bool = False
    service_type: str = ""
    multi_service: bool = False


class SchemaRegistry:
    """Singleton collecting all tool schemas at decoration time."""

    _instance: Optional["SchemaRegistry"] = None
    _schemas: Dict[str, ToolSchema]

    def __init__(self) -> None:
        self._schemas = {}

    @classmethod
    def instance(cls) -> "SchemaRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset for testing."""
        cls._instance = None

    def register(self, schema: ToolSchema) -> None:
        self._schemas[schema.name] = schema

    def get(self, name: str) -> Optional[ToolSchema]:
        return self._schemas.get(name)

    def by_service(self, service: str) -> List[ToolSchema]:
        return [s for s in self._schemas.values() if s.service == service]

    def by_tier(self, tier: ToolTier) -> List[ToolSchema]:
        return [s for s in self._schemas.values() if s.tier == tier]

    def up_to_tier(
        self, tier: ToolTier, services: Optional[Set[str]] = None
    ) -> List[ToolSchema]:
        """Return schemas from core through the given tier."""
        order = [ToolTier.CORE, ToolTier.EXTENDED, ToolTier.COMPLETE]
        allowed = set(order[: order.index(tier) + 1])
        return [
            s
            for s in self._schemas.values()
            if s.tier in allowed and (services is None or s.service in services)
        ]

    def tool_names_up_to_tier(
        self, tier: ToolTier, services: Optional[Set[str]] = None
    ) -> List[str]:
        """Return tool name list for backwards-compat with set_enabled_tools."""
        return [s.name for s in self.up_to_tier(tier, services)]

    def services_for_tier(self, tier: ToolTier) -> Set[str]:
        """Which services have at least one tool in this tier or below."""
        return {s.service for s in self.up_to_tier(tier)}

    def all_services(self) -> Set[str]:
        return {s.service for s in self._schemas.values()}

    def all_schemas(self) -> Dict[str, ToolSchema]:
        return dict(self._schemas)

    def read_only_tools(self) -> List[ToolSchema]:
        return [s for s in self._schemas.values() if s.read_only]

    def write_tools(self) -> List[ToolSchema]:
        return [s for s in self._schemas.values() if not s.read_only]

    def paginated_tools(self) -> List[ToolSchema]:
        return [s for s in self._schemas.values() if s.paginated]


def tool_schema(
    service: str,
    tier: str | ToolTier,
    scopes: list[str],
    read_only: bool = True,
    tags: list[str] | None = None,
    paginated: bool = False,
    supports_upload: bool = False,
    supports_download: bool = False,
    service_type: str = "",
    multi_service: bool = False,
) -> Callable:
    """Attach structured schema metadata to a tool function.

    Must be the outermost decorator (before @server.tool()).
    Purely additive — does not alter the function's behavior.
    """

    def decorator(func: Callable) -> Callable:
        schema = ToolSchema(
            name=func.__name__,
            service=service,
            tier=ToolTier(tier) if isinstance(tier, str) else tier,
            scopes=tuple(scopes),
            read_only=read_only,
            description=(func.__doc__ or "").strip().split("\n")[0],
            tags=tuple(tags or []),
            paginated=paginated,
            supports_upload=supports_upload,
            supports_download=supports_download,
            service_type=service_type or service,
            multi_service=multi_service,
        )
        SchemaRegistry.instance().register(schema)
        func._tool_schema = schema
        return func

    return decorator


def register_comment_schemas(app_name: str, service: str) -> None:
    """Register schemas for factory-generated comment tools."""
    registry = SchemaRegistry.instance()
    registry.register(
        ToolSchema(
            name=f"list_{app_name}_comments",
            service=service,
            tier=ToolTier.COMPLETE,
            scopes=("drive_read",),
            read_only=True,
            description=f"List all comments from a Google {app_name.title()}.",
            tags=("comments",),
            service_type="drive",
        )
    )
    registry.register(
        ToolSchema(
            name=f"manage_{app_name}_comment",
            service=service,
            tier=ToolTier.COMPLETE,
            scopes=("drive_file",),
            read_only=False,
            description=f"Create, reply to, or resolve comments on a Google {app_name.title()}.",
            tags=("comments",),
            service_type="drive",
        )
    )
