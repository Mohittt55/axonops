"""
axonops_sdk.base
~~~~~~~~~~~~~~~~
Abstract base classes for the three AxonOps plugin types.

Every plugin implements one or more of these:
  - BaseCollector   → collect() → MetricBatch
  - BaseAction      → execute(action) → ActionResult
  - BaseStrategy    → evaluate(metrics, context) → Decision

These interfaces are stable from SDK v1.0.0 onwards.
Breaking changes will only land in major versions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from .models import Action, ActionResult, Decision, Episode, MetricBatch


class BaseCollector(ABC):
    """
    Implement this to add a new data source to AxonOps.

    Example
    -------
    class MyCollector(BaseCollector):
        plugin_name = "my-service"
        plugin_version = "1.0.0"

        async def collect(self) -> MetricBatch:
            # poll your data source
            return MetricBatch(source=self.plugin_name, host="localhost", metrics=[...])

        async def health_check(self) -> bool:
            return True  # can we reach the data source?
    """

    plugin_name: ClassVar[str] = "base"
    plugin_version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = ""

    @abstractmethod
    async def collect(self) -> MetricBatch:
        """
        Poll the data source and return a normalized MetricBatch.
        Must be non-blocking. Called every tick (default: 10s).
        Should never raise — catch exceptions internally and return
        an empty MetricBatch with metadata describing the error.
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Return True if the data source is reachable.
        Called on startup and periodically to detect degraded collectors.
        """

    async def setup(self) -> None:
        """Optional: called once on startup before the first collect()."""

    async def teardown(self) -> None:
        """Optional: called on shutdown for cleanup."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(plugin={self.plugin_name} v{self.plugin_version})"


class BaseAction(ABC):
    """
    Implement this to give AxonOps the ability to act on a system.

    Example
    -------
    class MyAction(BaseAction):
        plugin_name = "my-service"
        supported_actions = ["restart", "scale_up"]

        async def execute(self, action: Action) -> ActionResult:
            # perform the action
            return ActionResult(action_id=action.action_id, status=ActionStatus.SUCCESS)

        async def validate(self, action: Action) -> bool:
            return action.action_type.value in self.supported_actions
    """

    plugin_name: ClassVar[str] = "base"
    plugin_version: ClassVar[str] = "1.0.0"
    supported_actions: ClassVar[list[str]] = []

    @abstractmethod
    async def execute(self, action: Action) -> ActionResult:
        """
        Execute the action against the target system.
        Should be idempotent where possible.
        Never raise — return ActionResult with status=FAILED on errors.
        """

    @abstractmethod
    async def validate(self, action: Action) -> bool:
        """
        Pre-flight check before execution.
        Return False to abort the action without marking it as failed.
        """

    async def setup(self) -> None:
        """Optional: called once on startup."""

    async def teardown(self) -> None:
        """Optional: called on shutdown."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(plugin={self.plugin_name}, supports={self.supported_actions})"


class BaseStrategy(ABC):
    """
    Implement this to replace or extend the default decision logic.

    Example
    -------
    class MyStrategy(BaseStrategy):
        strategy_name = "my-strategy"

        def evaluate(self, metrics: MetricBatch, context: list[Episode]) -> Decision | None:
            # analyze metrics, return a Decision or None if no action needed
            ...
    """

    strategy_name: ClassVar[str] = "base"
    strategy_version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = ""

    @abstractmethod
    def evaluate(
        self,
        metrics: MetricBatch,
        context: list[Episode],
    ) -> Decision | None:
        """
        Analyze current metrics and past episodes.
        Return a Decision if action is needed, None if everything is normal.

        Parameters
        ----------
        metrics : MetricBatch
            Current state from all active collectors.
        context : list[Episode]
            Most similar past episodes from the memory system.
            Use these to bias your action selection toward what worked before.
        """

    def setup(self) -> None:
        """Optional: called once on startup to initialize models."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(strategy={self.strategy_name} v{self.strategy_version})"
