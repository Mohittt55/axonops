"""
axonops_sdk.registry
~~~~~~~~~~~~~~~~~~~~
Plugin discovery and registration via Python entry points.

Any installed package that declares axonops.collectors / axonops.actions /
axonops.strategies entry points is automatically discovered at startup.
No configuration file, no manual registration.
"""

from __future__ import annotations

import importlib.metadata
import logging
from typing import TypeVar

from .base import BaseAction, BaseCollector, BaseStrategy

logger = logging.getLogger(__name__)

T = TypeVar("T")

COLLECTOR_GROUP = "axonops.collectors"
ACTION_GROUP    = "axonops.actions"
STRATEGY_GROUP  = "axonops.strategies"


def _load_group(group: str) -> dict[str, type]:
    """Load all entry points for a given group."""
    plugins: dict[str, type] = {}
    try:
        eps = importlib.metadata.entry_points(group=group)
    except Exception as e:
        logger.warning(f"Failed to enumerate entry points for {group!r}: {e}")
        return plugins

    for ep in eps:
        try:
            cls = ep.load()
            plugins[ep.name] = cls
            logger.info(f"Loaded plugin: [{group}] {ep.name} → {cls.__name__}")
        except Exception as e:
            logger.error(f"Failed to load plugin {ep.name!r} from {group!r}: {e}")

    return plugins


def load_collectors() -> dict[str, type[BaseCollector]]:
    """Return all installed Collector plugins keyed by name."""
    return _load_group(COLLECTOR_GROUP)  # type: ignore[return-value]


def load_actions() -> dict[str, type[BaseAction]]:
    """Return all installed Action plugins keyed by name."""
    return _load_group(ACTION_GROUP)  # type: ignore[return-value]


def load_strategies() -> dict[str, type[BaseStrategy]]:
    """Return all installed Strategy plugins keyed by name."""
    return _load_group(STRATEGY_GROUP)  # type: ignore[return-value]


def load_all() -> dict[str, dict[str, type]]:
    """Load all three plugin types at once."""
    return {
        "collectors": load_collectors(),
        "actions":    load_actions(),
        "strategies": load_strategies(),
    }
