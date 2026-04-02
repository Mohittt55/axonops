"""
axonops-plugin-docker
~~~~~~~~~~~~~~~~~~~~~
Collector, Action, and Strategy for Docker container monitoring.
"""
from __future__ import annotations

import socket
import time
import uuid
from collections import deque
from datetime import datetime
from typing import Deque

from axonops_sdk import (
    Action, ActionResult, ActionStatus, ActionType,
    BaseAction, BaseCollector, BaseStrategy,
    Decision, Episode, Metric, MetricBatch,
)

try:
    import docker
    
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False


# ── Collector ─────────────────────────────────────────────────────────────────

class DockerCollector(BaseCollector):
    plugin_name    = "docker"
    plugin_version = "1.0.0"
    description    = "Container-level CPU, memory, network I/O, and health status"

    def __init__(self) -> None:
        self._client = None

    async def setup(self) -> None:
        if DOCKER_AVAILABLE:
            try:
                self._client = docker.from_env()
            except Exception:
                self._client = None

    async def collect(self) -> MetricBatch:
        if not self._client:
            return MetricBatch(
                source=self.plugin_name, host=socket.gethostname(),
                metadata={"error": "Docker client unavailable"},
            )

        metrics: list[Metric] = []
        now  = datetime.utcnow()
        host = socket.gethostname()

        try:
            containers = self._client.containers.list()
        except Exception as e:
            return MetricBatch(
                source=self.plugin_name, host=host,
                metadata={"error": str(e)},
            )

        metrics.append(Metric(
            name="container_count", value=float(len(containers)),
            timestamp=now, unit="containers",
        ))

        for c in containers:
            name = c.name
            try:
                stats = c.stats(stream=False)
                # CPU %
                cpu_delta   = (stats["cpu_stats"]["cpu_usage"]["total_usage"] -
                               stats["precpu_stats"]["cpu_usage"]["total_usage"])
                sys_delta   = (stats["cpu_stats"]["system_cpu_usage"] -
                               stats["precpu_stats"]["system_cpu_usage"])
                num_cpus    = stats["cpu_stats"].get("online_cpus", 1)
                cpu_pct     = (cpu_delta / sys_delta) * num_cpus * 100.0 if sys_delta else 0.0

                # Memory
                mem_usage   = stats["memory_stats"].get("usage", 0)
                mem_limit   = stats["memory_stats"].get("limit", 1)
                mem_pct     = (mem_usage / mem_limit) * 100.0

                # Network
                net_rx = net_tx = 0
                for iface in stats.get("networks", {}).values():
                    net_rx += iface.get("rx_bytes", 0)
                    net_tx += iface.get("tx_bytes", 0)

                tags = {"container": name, "image": c.image.tags[0] if c.image.tags else "unknown"}
                metrics += [
                    Metric(name="container_cpu_percent",   value=round(cpu_pct, 2),        unit="%",  timestamp=now, tags=tags),
                    Metric(name="container_mem_percent",   value=round(mem_pct, 2),         unit="%",  timestamp=now, tags=tags),
                    Metric(name="container_mem_mb",        value=round(mem_usage/1024**2, 1), unit="MB", timestamp=now, tags=tags),
                    Metric(name="container_net_rx_mb",     value=round(net_rx/1024**2, 2),  unit="MB", timestamp=now, tags=tags),
                    Metric(name="container_net_tx_mb",     value=round(net_tx/1024**2, 2),  unit="MB", timestamp=now, tags=tags),
                    Metric(name="container_restarts",      value=float(c.attrs.get("RestartCount", 0)), timestamp=now, tags=tags),
                ]
            except Exception:
                continue

        return MetricBatch(
            source=self.plugin_name, host=host, metrics=metrics,
            metadata={"container_count": len(containers)},
        )

    async def health_check(self) -> bool:
        if not self._client:
            return False
        try:
            self._client.ping()
            return True
        except Exception:
            return False


# ── Action ────────────────────────────────────────────────────────────────────

class DockerAction(BaseAction):
    plugin_name      = "docker"
    plugin_version   = "1.0.0"
    supported_actions = [
        ActionType.RESTART.value,
        ActionType.SCALE_UP.value,
        ActionType.KILL_PROCESS.value,
        ActionType.NOTIFY.value,
    ]

    def __init__(self) -> None:
        self._client = None

    async def setup(self) -> None:
        if DOCKER_AVAILABLE:
            try:
                self._client = docker.from_env()
            except Exception:
                self._client = None

    async def validate(self, action: Action) -> bool:
        if not self._client:
            return False
        if action.action_type in (ActionType.RESTART, ActionType.KILL_PROCESS):
            try:
                self._client.containers.get(action.target)
                return True
            except Exception:
                return False
        return True

    async def execute(self, action: Action) -> ActionResult:
        start = time.monotonic()
        try:
            if action.action_type == ActionType.RESTART:
                result = await self._restart(action)
            elif action.action_type == ActionType.KILL_PROCESS:
                result = await self._stop(action)
            elif action.action_type == ActionType.SCALE_UP:
                result = await self._scale(action)
            else:
                result = ActionResult(action_id=action.action_id,
                                      status=ActionStatus.SKIPPED,
                                      message=f"No handler for {action.action_type}")
        except Exception as e:
            result = ActionResult(action_id=action.action_id,
                                  status=ActionStatus.FAILED, message=str(e))

        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    async def _restart(self, action: Action) -> ActionResult:
        container = self._client.containers.get(action.target)
        container.restart(timeout=action.params.get("timeout", 10))
        return ActionResult(
            action_id=action.action_id, status=ActionStatus.SUCCESS,
            message=f"Restarted container: {action.target}",
            side_effects={"container": action.target},
        )

    async def _stop(self, action: Action) -> ActionResult:
        container = self._client.containers.get(action.target)
        container.stop()
        return ActionResult(
            action_id=action.action_id, status=ActionStatus.SUCCESS,
            message=f"Stopped container: {action.target}",
        )

    async def _scale(self, action: Action) -> ActionResult:
        replicas = action.params.get("replicas", 1)
        return ActionResult(
            action_id=action.action_id, status=ActionStatus.SUCCESS,
            message=f"Scale action noted for {action.target} → {replicas} replicas (Swarm required)",
            side_effects={"target": action.target, "replicas": replicas},
        )


# ── Strategy ──────────────────────────────────────────────────────────────────

class DockerStrategy(BaseStrategy):
    strategy_name    = "docker-restart-loop"
    strategy_version = "1.0.0"
    description      = "Detects container restart loops and memory leaks"

    RESTART_THRESHOLD = 3     # restarts in window = problem
    MEM_THRESHOLD     = 90.0  # % container memory usage

    def __init__(self) -> None:
        self._restart_history: dict[str, Deque[float]] = {}

    def evaluate(
        self,
        metrics: MetricBatch,
        context: list[Episode],
    ) -> Decision | None:
        restart_metrics = [m for m in metrics.metrics if m.name == "container_restarts"]
        mem_metrics     = [m for m in metrics.metrics if m.name == "container_mem_percent"]

        for m in restart_metrics:
            container = m.tags.get("container", "unknown")
            if container not in self._restart_history:
                self._restart_history[container] = deque(maxlen=10)
            self._restart_history[container].append(m.value)

            history = self._restart_history[container]
            if len(history) >= 2 and history[-1] > history[-2] + self.RESTART_THRESHOLD:
                confidence = self._confidence(context)
                action = Action(
                    action_id=str(uuid.uuid4()),
                    action_type=ActionType.RESTART,
                    target=container,
                    reason=f"Container {container!r} is in a restart loop ({m.value:.0f} restarts)",
                    params={"container": container, "restart_count": m.value},
                )
                return Decision(
                    action=action, confidence=confidence,
                    reasoning=f"{container!r} restarted {m.value:.0f} times — restart loop detected",
                    anomaly_score=min(m.value / 10.0, 1.0),
                    strategy_name=self.strategy_name,
                )

        for m in mem_metrics:
            if m.value >= self.MEM_THRESHOLD:
                container  = m.tags.get("container", "unknown")
                confidence = self._confidence(context)
                action = Action(
                    action_id=str(uuid.uuid4()),
                    action_type=ActionType.RESTART,
                    target=container,
                    reason=f"Container {container!r} memory at {m.value:.1f}%",
                    params={"container": container, "mem_percent": m.value},
                )
                return Decision(
                    action=action, confidence=confidence,
                    reasoning=f"Memory pressure: {container!r} at {m.value:.1f}%",
                    anomaly_score=(m.value - self.MEM_THRESHOLD) / (100 - self.MEM_THRESHOLD),
                    strategy_name=self.strategy_name,
                )

        return None

    def _confidence(self, context: list[Episode]) -> float:
        if not context:
            return 0.70
        successes = sum(1 for ep in context if ep.was_successful)
        return round(0.65 + (successes / len(context)) * 0.25, 2)
