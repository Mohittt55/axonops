"""
axonops_sdk.models
~~~~~~~~~~~~~~~~~~
Core data structures shared across all AxonOps plugins and the core engine.
These are the contracts that keep the entire system interoperable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ── Enums ─────────────────────────────────────────────────────────────────────

class ActionType(str, Enum):
    RESTART         = "restart"
    SCALE_UP        = "scale_up"
    SCALE_DOWN      = "scale_down"
    KILL_PROCESS    = "kill_process"
    ROLLBACK        = "rollback"
    NOTIFY          = "notify"
    DRAIN_NODE      = "drain_node"
    CUSTOM          = "custom"


class ActionStatus(str, Enum):
    SUCCESS  = "success"
    FAILED   = "failed"
    SKIPPED  = "skipped"
    PENDING  = "pending"


class Severity(str, Enum):
    INFO     = "info"
    WARNING  = "warning"
    CRITICAL = "critical"


class ConfidenceTier(str, Enum):
    AUTO    = "auto"      # >= 0.85 — execute silently
    NOTIFY  = "notify"    # 0.60–0.85 — execute + notify
    HUMAN   = "human"     # < 0.60 — wait for approval


# ── Metric ────────────────────────────────────────────────────────────────────

@dataclass
class Metric:
    """A single named measurement at a point in time."""
    name: str
    value: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    unit: str = ""
    tags: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"Metric({self.name}={self.value}{self.unit} @ {self.timestamp.isoformat()})"


@dataclass
class MetricBatch:
    """
    Normalized output of any Collector plugin.
    All collectors return this regardless of their underlying data source.
    """
    source: str                          # plugin name, e.g. "psutil", "kubernetes"
    host: str                            # hostname or node identifier
    metrics: list[Metric] = field(default_factory=list)
    collected_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get(self, name: str) -> Metric | None:
        """Retrieve a metric by name."""
        for m in self.metrics:
            if m.name == name:
                return m
        return None

    def get_value(self, name: str, default: float = 0.0) -> float:
        m = self.get(name)
        return m.value if m else default

    def __len__(self) -> int:
        return len(self.metrics)


# ── Action ────────────────────────────────────────────────────────────────────

@dataclass
class Action:
    """
    A decision to perform on infrastructure.
    Produced by the Strategy plugin, consumed by the Action plugin.
    """
    action_type: ActionType
    target: str                          # container name, pod name, ASG name, etc.
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    action_id: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ActionResult:
    """
    Result of executing an Action.
    Written back to the memory system as part of an Episode.
    """
    action_id: str
    status: ActionStatus
    message: str = ""
    side_effects: dict[str, Any] = field(default_factory=dict)
    executed_at: datetime = field(default_factory=datetime.utcnow)
    duration_ms: int = 0

    @property
    def succeeded(self) -> bool:
        return self.status == ActionStatus.SUCCESS


# ── Decision ──────────────────────────────────────────────────────────────────

@dataclass
class Decision:
    """
    Output of a Strategy plugin's evaluate() call.
    Carries the recommended action and confidence score.
    """
    action: Action
    confidence: float                    # 0.0 – 1.0
    reasoning: str = ""
    anomaly_score: float = 0.0
    strategy_name: str = ""

    @property
    def confidence_tier(self) -> ConfidenceTier:
        if self.confidence >= 0.85:
            return ConfidenceTier.AUTO
        elif self.confidence >= 0.60:
            return ConfidenceTier.NOTIFY
        else:
            return ConfidenceTier.HUMAN

    @property
    def severity(self) -> Severity:
        if self.anomaly_score >= 0.8:
            return Severity.CRITICAL
        elif self.anomaly_score >= 0.5:
            return Severity.WARNING
        return Severity.INFO


# ── Episode ───────────────────────────────────────────────────────────────────

@dataclass
class Episode:
    """
    A complete (state → decision → outcome) tuple stored in the memory system.
    This is the unit of learning in AxonOps.
    """
    episode_id: str
    state_snapshot: dict[str, float]    # metric name → value at decision time
    decision: Decision
    result: ActionResult
    environment: str = "default"
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def was_successful(self) -> bool:
        return self.result.succeeded
