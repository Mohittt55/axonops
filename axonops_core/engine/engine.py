"""
axonops_core.engine
~~~~~~~~~~~~~~~~~~~
The main reasoning loop. Loads plugins, collects metrics,
runs the strategy, applies the confidence gate, and executes actions.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Callable

from axonops_sdk import (
    ActionResult, ActionStatus, ConfidenceTier,
    Decision, Episode, MetricBatch,
    load_actions, load_collectors, load_strategies,
)
from axonops_sdk.base import BaseAction, BaseCollector, BaseStrategy

logger = logging.getLogger("axonops.engine")


class AxonOpsEngine:
    """
    The core reasoning loop.

    Usage
    -----
    engine = AxonOpsEngine(tick_interval=10)
    await engine.start()
    """

    def __init__(
        self,
        tick_interval: int = 10,
        confidence_auto: float = 0.85,
        confidence_notify: float = 0.60,
        environment: str = "default",
        on_decision: Callable[[Decision], None] | None = None,
        on_action: Callable[[ActionResult], None] | None = None,
    ) -> None:
        self.tick_interval    = tick_interval
        self.confidence_auto  = confidence_auto
        self.confidence_notify = confidence_notify
        self.environment      = environment
        self.on_decision      = on_decision
        self.on_action        = on_action

        self._collectors: list[BaseCollector] = []
        self._actions: dict[str, BaseAction]  = {}
        self._strategies: list[BaseStrategy]  = []
        self._episodes: list[Episode]          = []
        self._pending: list[Decision]          = []
        self._running = False
        self._feed: list[dict] = []   # live feed for the UI

    # ── Setup ─────────────────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Discover and initialise all installed plugins."""
        collector_classes  = load_collectors()
        action_classes     = load_actions()
        strategy_classes   = load_strategies()

        logger.info(f"Discovered {len(collector_classes)} collectors, "
                    f"{len(action_classes)} actions, "
                    f"{len(strategy_classes)} strategies")

        for name, cls in collector_classes.items():
            c = cls()
            await c.setup()
            self._collectors.append(c)
            logger.info(f"  collector ready: {name}")

        for name, cls in action_classes.items():
            a = cls()
            await a.setup()
            self._actions[name] = a
            logger.info(f"  action ready: {name}")

        for name, cls in strategy_classes.items():
            s = cls()
            s.setup()
            self._strategies.append(s)
            logger.info(f"  strategy ready: {name}")

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        logger.info(f"AxonOps engine started — tick every {self.tick_interval}s")
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"Tick error: {e}", exc_info=True)
            await asyncio.sleep(self.tick_interval)

    async def stop(self) -> None:
        self._running = False
        logger.info("AxonOps engine stopped")

    # ── Tick ──────────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        # 1. Collect metrics from all active collectors
        batches = await asyncio.gather(
            *[c.collect() for c in self._collectors],
            return_exceptions=True,
        )
        valid_batches: list[MetricBatch] = [
            b for b in batches if isinstance(b, MetricBatch)
        ]
        if not valid_batches:
            return

        # Merge into a single batch for strategy evaluation
        merged = self._merge_batches(valid_batches)

        # 2. Run all strategies, take the first non-None decision
        context  = self._get_episode_context(merged)
        decision = None
        for strategy in self._strategies:
            try:
                decision = strategy.evaluate(merged, context)
                if decision is not None:
                    break
            except Exception as e:
                logger.warning(f"Strategy {strategy.strategy_name} error: {e}")

        if decision is None:
            self._log_feed("ok", "All systems nominal", merged)
            return

        logger.info(
            f"Decision: {decision.action.action_type.value} on {decision.action.target!r} "
            f"[confidence={decision.confidence:.2f}, tier={decision.confidence_tier.value}]"
        )

        if self.on_decision:
            self.on_decision(decision)

        # 3. Confidence gate
        tier = decision.confidence_tier
        if tier == ConfidenceTier.AUTO:
            await self._execute_decision(decision, merged)
        elif tier == ConfidenceTier.NOTIFY:
            self._log_feed("notify", f"Executing with notification: {decision.action.reason}", merged)
            await self._execute_decision(decision, merged)
        else:
            # HUMAN — queue for approval
            self._pending.append(decision)
            self._log_feed(
                "pending",
                f"Awaiting approval: {decision.action.reason} "
                f"(confidence={decision.confidence:.2f})",
                merged,
            )

    async def _execute_decision(self, decision: Decision, metrics: MetricBatch) -> None:
        """Find the right action plugin and execute."""
        action_type = decision.action.action_type.value

        # Find action plugin that supports this action type
        handler: BaseAction | None = None
        for plugin in self._actions.values():
            if action_type in plugin.supported_actions:
                handler = plugin
                break

        if not handler:
            logger.warning(f"No action plugin found for {action_type!r}")
            return

        try:
            valid = await handler.validate(decision.action)
            if not valid:
                logger.info(f"Action validation failed for {action_type!r} on {decision.action.target!r}")
                return

            result = await handler.execute(decision.action)
        except Exception as e:
            result = ActionResult(
                action_id=decision.action.action_id,
                status=ActionStatus.FAILED,
                message=str(e),
            )

        if self.on_action:
            self.on_action(result)

        # 4. Write episode to memory
        state_snapshot = {m.name: m.value for m in metrics.metrics[:50]}
        episode = Episode(
            episode_id     = str(uuid.uuid4()),
            state_snapshot = state_snapshot,
            decision       = decision,
            result         = result,
            environment    = self.environment,
            timestamp      = datetime.utcnow(),
        )
        self._episodes.append(episode)

        status = "success" if result.succeeded else "failed"
        self._log_feed(
            status,
            f"{decision.action.action_type.value} on {decision.action.target!r} → {result.status.value}: {result.message}",
            metrics,
        )
        logger.info(f"Action result: {result.status.value} — {result.message}")

    # ── Approval gate ─────────────────────────────────────────────────────────

    async def approve(self, action_id: str) -> bool:
        """Approve a pending decision by action_id."""
        for decision in list(self._pending):
            if decision.action.action_id == action_id:
                self._pending.remove(decision)
                await self._execute_decision(decision, MetricBatch(source="manual", host=""))
                return True
        return False

    async def reject(self, action_id: str) -> bool:
        """Reject and discard a pending decision."""
        for decision in list(self._pending):
            if decision.action.action_id == action_id:
                self._pending.remove(decision)
                self._log_feed("rejected", f"Rejected: {decision.action.reason}", MetricBatch(source="manual", host=""))
                return True
        return False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _merge_batches(self, batches: list[MetricBatch]) -> MetricBatch:
        merged_metrics = []
        for b in batches:
            merged_metrics.extend(b.metrics)
        return MetricBatch(
            source   = ",".join(b.source for b in batches),
            host     = batches[0].host if batches else "",
            metrics  = merged_metrics,
            metadata = {b.source: b.metadata for b in batches},
        )

    def _get_episode_context(self, metrics: MetricBatch, top_k: int = 5) -> list[Episode]:
        """Simple recency-based context. Replace with pgvector similarity in production."""
        return self._episodes[-top_k:]

    def _log_feed(self, status: str, message: str, metrics: MetricBatch) -> None:
        entry = {
            "id":        str(uuid.uuid4()),
            "timestamp": datetime.utcnow().isoformat(),
            "status":    status,
            "message":   message,
            "metrics": {
                m.name: m.value
                for m in metrics.metrics[:8]  # top 8 for the feed
            },
        }
        self._feed.append(entry)
        if len(self._feed) > 500:
            self._feed = self._feed[-500:]

    # ── State for API ─────────────────────────────────────────────────────────

    @property
    def feed(self) -> list[dict]:
        return list(reversed(self._feed[-50:]))

    @property
    def pending_decisions(self) -> list[dict]:
        return [
            {
                "action_id":    d.action.action_id,
                "action_type":  d.action.action_type.value,
                "target":       d.action.target,
                "reason":       d.action.reason,
                "confidence":   d.confidence,
                "reasoning":    d.reasoning,
                "anomaly_score":d.anomaly_score,
                "severity":     d.severity.value,
                "created_at":   d.action.created_at.isoformat(),
            }
            for d in self._pending
        ]

    @property
    def episode_count(self) -> int:
        return len(self._episodes)

    @property
    def collectors_status(self) -> list[dict]:
        return [
            {"name": c.plugin_name, "version": c.plugin_version}
            for c in self._collectors
        ]
