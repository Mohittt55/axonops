"""psutil strategy — Z-score anomaly detection for local machine metrics."""
from __future__ import annotations

import uuid
from collections import deque
from statistics import mean, stdev
from typing import Deque

from axonops_sdk import (
    Action, ActionType, BaseStrategy, Decision, Episode, MetricBatch,
)


class PsutilStrategy(BaseStrategy):
    strategy_name    = "psutil-zscore"
    strategy_version = "1.0.0"
    description      = "Z-score based anomaly detection on CPU and memory"

    # Thresholds
    CPU_WARN    = 85.0    # % — warn above this
    MEM_WARN    = 90.0    # % — warn above this
    ZSCORE_CRIT = 3.0     # standard deviations — anomaly above this
    WINDOW      = 30      # rolling window size

    def __init__(self) -> None:
        self._cpu_history: Deque[float] = deque(maxlen=self.WINDOW)
        self._mem_history: Deque[float] = deque(maxlen=self.WINDOW)

    def evaluate(
        self,
        metrics: MetricBatch,
        context: list[Episode],
    ) -> Decision | None:
        cpu = metrics.get_value("cpu_percent")
        mem = metrics.get_value("mem_percent")

        self._cpu_history.append(cpu)
        self._mem_history.append(mem)

        # Need at least 5 samples before making decisions
        if len(self._cpu_history) < 5:
            return None

        cpu_z = self._zscore(cpu, self._cpu_history)
        mem_z = self._zscore(mem, self._mem_history)

        # Determine worst offender
        if cpu_z >= self.ZSCORE_CRIT and cpu >= self.CPU_WARN:
            anomaly_score = min(cpu_z / 5.0, 1.0)
            confidence    = self._confidence_from_context(context, "cpu_percent", cpu)
            action = Action(
                action_id   = str(uuid.uuid4()),
                action_type = ActionType.NOTIFY,
                target      = metrics.host,
                reason      = f"CPU spike: {cpu:.1f}% (z={cpu_z:.2f})",
                params      = {"metric": "cpu_percent", "value": cpu, "zscore": cpu_z},
            )
            return Decision(
                action        = action,
                confidence    = confidence,
                reasoning     = f"CPU at {cpu:.1f}% is {cpu_z:.1f} std devs above recent baseline",
                anomaly_score = anomaly_score,
                strategy_name = self.strategy_name,
            )

        if mem_z >= self.ZSCORE_CRIT and mem >= self.MEM_WARN:
            anomaly_score = min(mem_z / 5.0, 1.0)
            confidence    = self._confidence_from_context(context, "mem_percent", mem)
            action = Action(
                action_id   = str(uuid.uuid4()),
                action_type = ActionType.NOTIFY,
                target      = metrics.host,
                reason      = f"Memory pressure: {mem:.1f}% (z={mem_z:.2f})",
                params      = {"metric": "mem_percent", "value": mem, "zscore": mem_z},
            )
            return Decision(
                action        = action,
                confidence    = confidence,
                reasoning     = f"Memory at {mem:.1f}% is {mem_z:.1f} std devs above baseline",
                anomaly_score = anomaly_score,
                strategy_name = self.strategy_name,
            )

        return None

    def _zscore(self, value: float, history: Deque[float]) -> float:
        if len(history) < 2:
            return 0.0
        s = stdev(history)
        if s == 0:
            return 0.0
        return abs(value - mean(history)) / s

    def _confidence_from_context(
        self,
        context: list[Episode],
        metric: str,
        value: float,
    ) -> float:
        """Boost confidence if similar past episodes resolved successfully."""
        if not context:
            return 0.65  # no history → medium confidence
        successes = sum(1 for ep in context if ep.was_successful)
        base = 0.60 + (successes / len(context)) * 0.30
        return round(min(base, 0.95), 2)
