"""
Tool Tier Loader Module

This module provides functionality to load and resolve tool tiers from the YAML configuration.
It integrates with the existing tool enablement workflow to support tiered tool loading.
"""

import logging
from pathlib import Path
from typing import Dict, List, Set, Literal, Optional

import yaml

logger = logging.getLogger(__name__)

TierLevel = Literal["core", "extended", "complete"]


class ToolTierLoader:
    """Loads and manages tool tiers from configuration."""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the tool tier loader.

        Args:
            config_path: Path to the tool_tiers.yaml file. If None, uses default location.
        """
        if config_path is None:
            # Default to core/tool_tiers.yaml relative to this file
            config_path = Path(__file__).parent / "tool_tiers.yaml"

        self.config_path = Path(config_path)
        self._tiers_config: Optional[Dict] = None

    def _load_config(self) -> Dict:
        """Load the tool tiers configuration from YAML file."""
        if self._tiers_config is not None:
            return self._tiers_config

        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Tool tiers configuration not found: {self.config_path}"
            )

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._tiers_config = yaml.safe_load(f)
            logger.info(f"Loaded tool tiers configuration from {self.config_path}")
            return self._tiers_config
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in tool tiers configuration: {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to load tool tiers configuration: {e}")

    def get_available_services(self) -> List[str]:
        """Get list of all available services defined in the configuration."""
        config = self._load_config()
        return list(config.keys())

    def get_tools_for_tier(
        self, tier: TierLevel, services: Optional[List[str]] = None
    ) -> List[str]:
        """
        Get all tools for a specific tier level.

        Args:
            tier: The tier level (core, extended, complete)
            services: Optional list of services to filter by. If None, includes all services.

        Returns:
            List of tool names for the specified tier level
        """
        config = self._load_config()
        tools = []

        # If no services specified, use all available services
        if services is None:
            services = self.get_available_services()

        for service in services:
            if service not in config:
                logger.warning(
                    f"Service '{service}' not found in tool tiers configuration"
                )
                continue

            service_config = config[service]
            if tier not in service_config:
                logger.debug(f"Tier '{tier}' not defined for service '{service}'")
                continue

            tier_tools = service_config[tier]
            if tier_tools:  # Handle empty lists
                tools.extend(tier_tools)

        return tools

    def get_tools_up_to_tier(
        self, tier: TierLevel, services: Optional[List[str]] = None
    ) -> List[str]:
        """
        Get all tools up to and including the specified tier level.

        Args:
            tier: The maximum tier level to include
            services: Optional list of services to filter by. If None, includes all services.

        Returns:
            List of tool names up to the specified tier level
        """
        tier_order = ["core", "extended", "complete"]
        max_tier_index = tier_order.index(tier)

        tools = []
        for i in range(max_tier_index + 1):
            current_tier = tier_order[i]
            tools.extend(self.get_tools_for_tier(current_tier, services))

        # Remove duplicates while preserving order
        seen = set()
        unique_tools = []
        for tool in tools:
            if tool not in seen:
                seen.add(tool)
                unique_tools.append(tool)

        return unique_tools

    def get_services_for_tools(self, tool_names: List[str]) -> Set[str]:
        """
        Get the service names that provide the specified tools.

        Args:
            tool_names: List of tool names to lookup

        Returns:
            Set of service names that provide any of the specified tools
        """
        config = self._load_config()
        services = set()

        for service, service_config in config.items():
            for tier_name, tier_tools in service_config.items():
                if tier_tools and any(tool in tier_tools for tool in tool_names):
                    services.add(service)
                    break

        return services


def get_tools_for_tier(
    tier: TierLevel, services: Optional[List[str]] = None
) -> List[str]:
    """
    Convenience function to get tools for a specific tier.

    Args:
        tier: The tier level (core, extended, complete)
        services: Optional list of services to filter by

    Returns:
        List of tool names for the specified tier level
    """
    loader = ToolTierLoader()
    return loader.get_tools_up_to_tier(tier, services)


def resolve_tools_from_tier(
    tier: TierLevel, services: Optional[List[str]] = None
) -> tuple[List[str], List[str]]:
    """
    Resolve tool names and service names for the specified tier.

    Args:
        tier: The tier level (core, extended, complete)
        services: Optional list of services to filter by

    Returns:
        Tuple of (tool_names, service_names) where:
        - tool_names: List of specific tool names for the tier
        - service_names: List of service names that should be imported
    """
    loader = ToolTierLoader()

    # Get all tools for the tier
    tools = loader.get_tools_up_to_tier(tier, services)

    # Map back to service names
    service_names = loader.get_services_for_tools(tools)

    logger.info(
        f"Tier '{tier}' resolved to {len(tools)} tools across {len(service_names)} services: {sorted(service_names)}"
    )

    return tools, sorted(service_names)
