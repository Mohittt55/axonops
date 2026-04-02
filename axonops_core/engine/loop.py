"""
axonops_core.engine
~~~~~~~~~~~~~~~~~~~
The main AxonOps decision loop.
Loads plugins → collects metrics → runs strategy → gates confidence → executes actions.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any

from axonops_sdk import (
    ActionResult, ActionStatus, ActionType,
    ConfidenceTier, Decision, Episode, MetricBatch,
    load_actions, load_collectors, load_strategies,
)
from axonops_sdk.base import BaseAction, BaseCollector, BaseStrategy

logger = logging.getLogger("axonops.engine")


class EngineConfig:
    tick_interval:    int   = 10      # seconds between collection ticks
    confidence_auto:  float = 0.85    # >= this → execute silently
    confidence_notify: float = 0.60   # >= this → execute + notify
    environment:      str   = "default"
    max_episode_context: int = 10     # episodes to fetch for context


class AxonOpsEngine:
    """
    The core reasoning loop.

    Usage
    -----
    engine = AxonOpsEngine(config=EngineConfig(), memory=memory_store)
    await engine.setup()
    await engine.run()          # blocks — runs forever
    """

    def __init__(
        self,
        config: EngineConfig | None = None,
        memory: Any = None,          # MemoryStore instance (optional in dev mode)
        notification_fn: Any = None, # async callable(message: str) for NOTIFY tier
    ) -> None:
        self.config          = config or EngineConfig()
        self.memory          = memory
        self.notification_fn = notification_fn
        self._running        = False

        # Plugin instances — populated in setup()
        self.collectors: list[BaseCollector]  = []
        self.actions:    dict[str, BaseAction] = {}
        self.strategies: list[BaseStrategy]   = []

        # Live state for the API / UI
        self.live_metrics:   MetricBatch | None = None
        self.decision_feed:  list[dict]          = []   # last 100 decisions
        self.approval_queue: list[dict]          = []   # pending human approvals

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Load all installed plugins and run their setup hooks."""
        logger.info("AxonOps engine starting — loading plugins")

        collector_classes = load_collectors()
        action_classes    = load_actions()
        strategy_classes  = load_strategies()

        for name, cls in collector_classes.items():
            try:
                inst = cls()
                await inst.setup()
                self.collectors.append(inst)
                logger.info(f"  collector loaded: {name}")
            except Exception as e:
                logger.error(f"  collector {name!r} setup failed: {e}")

        for name, cls in action_classes.items():
            try:
                inst = cls()
                await inst.setup()
                self.actions[name] = inst
                logger.info(f"  action loaded: {name}")
            except Exception as e:
                logger.error(f"  action {name!r} setup failed: {e}")

        for name, cls in strategy_classes.items():
            try:
                inst = cls()
                inst.setup()
                self.strategies.append(inst)
                logger.info(f"  strategy loaded: {name}")
            except Exception as e:
                logger.error(f"  strategy {name!r} setup failed: {e}")

        logger.info(
            f"Engine ready — {len(self.collectors)} collectors, "
            f"{len(self.actions)} actions, {len(self.strategies)} strategies"
        )

    async def run(self) -> None:
        """Main loop. Runs until stop() is called."""
        self._running = True
        logger.info(f"Engine running (tick={self.config.tick_interval}s)")
        while self._running:
            tick_start = time.monotonic()
            try:
                await self._tick()
            except Exception as e:
                logger.exception(f"Unhandled error in tick: {e}")
            elapsed = time.monotonic() - tick_start
            sleep   = max(0.0, self.config.tick_interval - elapsed)
            await asyncio.sleep(sleep)

    async def stop(self) -> None:
        self._running = False
        for c in self.collectors:
            await c.teardown()
        for a in self.actions.values():
            await a.teardown()

    # ── Core tick ──────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        # 1. Collect metrics from all active collectors
        batches = await self._collect_all()
        if not batches:
            return

        # Merge all batches into one (latest wins on duplicate names)
        merged = self._merge_batches(batches)
        self.live_metrics = merged

        # 2. Get episode context from memory
        context: list[Episode] = []
        if self.memory:
            try:
                state_vec = {m.name: m.value for m in merged.metrics}
                context   = await self.memory.find_similar(
                    state_vec, limit=self.config.max_episode_context
                )
            except Exception as e:
                logger.warning(f"Memory query failed: {e}")

        # 3. Run each strategy — first non-None decision wins
        decision: Decision | None = None
        for strategy in self.strategies:
            try:
                decision = strategy.evaluate(merged, context)
                if decision:
                    logger.info(
                        f"Decision from {strategy.strategy_name!r}: "
                        f"{decision.action.action_type.value} on {decision.action.target!r} "
                        f"(confidence={decision.confidence:.2f})"
                    )
                    break
            except Exception as e:
                logger.error(f"Strategy {strategy.strategy_name!r} error: {e}")

        if not decision:
            return

        # 4. Route through confidence gate
        await self._gate(decision, merged)

    async def _collect_all(self) -> list[MetricBatch]:
        tasks   = [c.collect() for c in self.collectors]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        batches = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"Collector error: {r}")
            else:
                batches.append(r)
        return batches

    def _merge_batches(self, batches: list[MetricBatch]) -> MetricBatch:
        from axonops_sdk import Metric
        seen:    dict[str, Metric] = {}
        sources: list[str]          = []
        for batch in batches:
            sources.append(batch.source)
            for m in batch.metrics:
                seen[f"{batch.source}:{m.name}"] = m
        return MetricBatch(
            source=",".join(sources),
            host=batches[0].host if batches else "unknown",
            metrics=list(seen.values()),
            collected_at=datetime.utcnow(),
        )

    # ── Confidence gate ────────────────────────────────────────────────────────

    async def _gate(self, decision: Decision, metrics: MetricBatch) -> None:
        tier  = decision.confidence_tier
        entry = {
            "id":          decision.action.action_id,
            "timestamp":   datetime.utcnow().isoformat(),
            "action_type": decision.action.action_type.value,
            "target":      decision.action.target,
            "reason":      decision.action.reason,
            "confidence":  decision.confidence,
            "tier":        tier.value,
            "severity":    decision.severity.value,
            "reasoning":   decision.reasoning,
            "status":      "pending",
        }

        if tier == ConfidenceTier.AUTO:
            result = await self._execute(decision)
            entry["status"] = result.status.value
            entry["result"] = result.message
            self._append_feed(entry)
            await self._save_episode(decision, result, metrics)

        elif tier == ConfidenceTier.NOTIFY:
            result = await self._execute(decision)
            entry["status"] = result.status.value
            entry["result"] = result.message
            self._append_feed(entry)
            await self._save_episode(decision, result, metrics)
            if self.notification_fn:
                try:
                    await self.notification_fn(
                        f"[AxonOps] {decision.action.action_type.value} on "
                        f"{decision.action.target!r} — {decision.action.reason}"
                    )
                except Exception as e:
                    logger.warning(f"Notification failed: {e}")

        else:  # HUMAN
            entry["status"] = "awaiting_approval"
            self.approval_queue.append(entry)
            self._append_feed(entry)
            if self.notification_fn:
                try:
                    await self.notification_fn(
                        f"[AxonOps] Approval needed: {decision.action.action_type.value} "
                        f"on {decision.action.target!r} (confidence={decision.confidence:.0%})"
                    )
                except Exception:
                    pass

    async def _execute(self, decision: Decision) -> ActionResult:
        action      = decision.action
        action_name = action.action_type.value

        # Find matching action plugin
        actor = self.actions.get(action_name) or self.actions.get(
            next((k for k in self.actions if action_name in k), "")
        )

        if not actor:
            # Fallback — find any plugin that supports this action
            for a in self.actions.values():
                if action_name in (a.supported_actions or []):
                    actor = a
                    break

        if not actor:
            logger.warning(f"No action plugin for {action_name!r} — logging only")
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SKIPPED,
                message=f"No plugin registered for action type: {action_name}",
            )

        valid = await actor.validate(action)
        if not valid:
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.SKIPPED,
                message="Pre-flight validation failed",
            )

        return await actor.execute(action)

    async def _save_episode(
        self,
        decision: Decision,
        result: ActionResult,
        metrics: MetricBatch,
    ) -> None:
        if not self.memory:
            return
        episode = Episode(
            episode_id      = str(uuid.uuid4()),
            state_snapshot  = {m.name: m.value for m in metrics.metrics},
            decision        = decision,
            result          = result,
            environment     = self.config.environment,
            timestamp       = datetime.utcnow(),
        )
        try:
            await self.memory.save(episode)
        except Exception as e:
            logger.warning(f"Failed to save episode: {e}")

    # ── Approval API ───────────────────────────────────────────────────────────

    async def approve(self, action_id: str) -> dict:
        """Called by UI/API to approve a pending decision."""
        item = next((x for x in self.approval_queue if x["id"] == action_id), None)
        if not item:
            return {"error": "not_found"}
        self.approval_queue.remove(item)
        item["status"] = "approved"

        # Re-construct the decision from queue entry and execute
        from axonops_sdk import Action, ActionType
        action = Action(
            action_id   = action_id,
            action_type = ActionType(item["action_type"]),
            target      = item["target"],
            reason      = item["reason"],
        )
        # Build a minimal Decision for episode storage
        from axonops_sdk import Decision as D
        decision = D(action=action, confidence=item["confidence"],
                     reasoning=item["reasoning"])
        result = await self._execute(decision)
        item["result"] = result.message
        item["status"] = result.status.value
        self._append_feed(item)
        return item

    async def reject(self, action_id: str) -> dict:
        """Called by UI/API to reject a pending decision."""
        item = next((x for x in self.approval_queue if x["id"] == action_id), None)
        if not item:
            return {"error": "not_found"}
        self.approval_queue.remove(item)
        item["status"] = "rejected"
        self._append_feed(item)
        return item

    def _append_feed(self, entry: dict) -> None:
        self.decision_feed.insert(0, entry)
        if len(self.decision_feed) > 100:
            self.decision_feed.pop()
