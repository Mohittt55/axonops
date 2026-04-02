"""
axonops-sdk
~~~~~~~~~~~
The official SDK for building AxonOps plugins.

Install: pip install axonops-sdk

Quick start
-----------
from axonops_sdk import BaseCollector, MetricBatch, Metric

class MyCollector(BaseCollector):
    plugin_name = "my-service"
    plugin_version = "1.0.0"

    async def collect(self) -> MetricBatch:
        return MetricBatch(
            source=self.plugin_name,
            host="localhost",
            metrics=[Metric(name="cpu_percent", value=42.5, unit="%")]
        )

    async def health_check(self) -> bool:
        return True
"""

from .base import BaseAction, BaseCollector, BaseStrategy
from .models import (
    Action,
    ActionResult,
    ActionStatus,
    ActionType,
    ConfidenceTier,
    Decision,
    Episode,
    Metric,
    MetricBatch,
    Severity,
)
from .registry import load_actions, load_all, load_collectors, load_strategies

__version__ = "1.0.0"
__all__ = [
    # Base classes
    "BaseCollector",
    "BaseAction",
    "BaseStrategy",
    # Models
    "Metric",
    "MetricBatch",
    "Action",
    "ActionResult",
    "ActionType",
    "ActionStatus",
    "Decision",
    "Episode",
    "Severity",
    "ConfidenceTier",
    # Registry
    "load_collectors",
    "load_actions",
    "load_strategies",
    "load_all",
]
