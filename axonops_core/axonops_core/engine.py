"""
axonops_core.engine
~~~~~~~~~~~~~~~~~~~
The main decision loop. Every tick:
  1. Collect metrics from all active collectors
  2. Run the active strategy → Decision | None
  3. Pass decision through the confidence gate
  4. Execute or queue for human approval
  5. Write episode to memory
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Callable

from axonops_sdk import (
    Action, ActionResult, ActionStatus, ActionType,
    BaseAction, BaseCollector, BaseStrategy,
    ConfidenceTier, Decision, Episode, MetricBatch,
    load_actions, load_collectors, load_strategies,
)

from .config import Settings, get_settings
from .database import AsyncSessionLocal, DecisionRecord, EpisodeRecord, MetricRecord

logger = logging.getLogger("axonops.engine")


class AxonOpsEngine:
    """
    The brain. Instantiate once, call start() to begin the loop.
    Subscribers can register via on_event() to receive real-time updates.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings    = settings or get_settings()
        self.collectors: list[BaseCollector] = []
        self.actions:    dict[str, BaseAction] = {}
        self.strategy:   BaseStrategy | None   = None
        self._running    = False
        self._subscribers: list[Callable[[dict], None]] = []
        self._pending_approvals: dict[str, Decision] = {}
        self._latest_metrics: MetricBatch | None = None

    # ── Setup ──────────────────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Load plugins, set up clients, warm up models."""
        logger.info("AxonOps engine starting up...")

        all_collectors = load_collectors()
        all_actions    = load_actions()
        all_strategies = load_strategies()

        # Instantiate active collectors
        for name in self.settings.active_collectors:
            cls = all_collectors.get(name)
            if not cls:
                logger.warning(f"Collector plugin {name!r} not found — is it installed?")
                continue
            instance = self._instantiate_collector(name, cls)
            await instance.setup()
            if await instance.health_check():
                self.collectors.append(instance)
                logger.info(f"Collector ready: {name}")
            else:
                logger.warning(f"Collector {name!r} failed health check — skipping")

        # Instantiate active actions
        for name in self.settings.active_actions:
            cls = all_actions.get(name)
            if not cls:
                logger.warning(f"Action plugin {name!r} not found")
                continue
            instance = self._instantiate_action(name, cls)
            await instance.setup()
            self.actions[name] = instance
            logger.info(f"Action ready: {name}")

        # Instantiate strategy
        strategy_name = self.settings.active_strategy
        cls = all_strategies.get(strategy_name)
        if cls:
            self.strategy = cls()
            self.strategy.setup()
            logger.info(f"Strategy ready: {strategy_name}")
        else:
            logger.warning(f"Strategy {strategy_name!r} not found — engine will collect but not act")

        logger.info(
            f"Engine ready — {len(self.collectors)} collectors, "
            f"{len(self.actions)} actions, strategy={strategy_name}"
        )

    def _instantiate_collector(self, name: str, cls: type) -> BaseCollector:
        if name == "prometheus" and self.settings.prometheus_targets:
            return cls(targets=self.settings.prometheus_targets)
        if name == "kubernetes":
            return cls(namespace=self.settings.k8s_namespace,
                       in_cluster=self.settings.k8s_in_cluster)
        if name == "aws":
            return cls(region=self.settings.aws_region,
                       asg_names=self.settings.aws_asg_names,
                       rds_instances=self.settings.aws_rds_instances)
        return cls()

    def _instantiate_action(self, name: str, cls: type) -> BaseAction:
        if name == "kubernetes":
            return cls(namespace=self.settings.k8s_namespace,
                       in_cluster=self.settings.k8s_in_cluster)
        if name == "aws":
            return cls(region=self.settings.aws_region)
        return cls()

    # ── Main loop ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Run the decision loop indefinitely."""
        self._running = True
        logger.info(f"Decision loop started (tick every {self.settings.tick_interval}s)")
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.exception(f"Tick error: {e}")
            await asyncio.sleep(self.settings.tick_interval)

    async def stop(self) -> None:
        self._running = False
        for c in self.collectors:
            await c.teardown()
        for a in self.actions.values():
            await a.teardown()

    async def _tick(self) -> None:
        # 1. Collect
        batch = await self._collect_all()
        if not batch.metrics:
            return
        self._latest_metrics = batch
        await self._persist_metrics(batch)
        self._emit("metrics", self._metrics_summary(batch))

        # 2. Strategy
        if not self.strategy:
            return
        context = await self._fetch_episode_context()
        decision = self.strategy.evaluate(batch, context)
        if not decision:
            return

        decision.action.action_id = str(uuid.uuid4())
        await self._persist_decision(decision)
        self._emit("decision", self._decision_summary(decision))
        logger.info(
            f"Decision: {decision.action.action_type.value} → {decision.action.target} "
            f"(confidence={decision.confidence:.2f}, tier={decision.confidence_tier.value})"
        )

        # 3. Confidence gate
        await self._gate(decision, batch)

    async def _collect_all(self) -> MetricBatch:
        """Run all collectors concurrently and merge results."""
        tasks = [c.collect() for c in self.collectors]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        from axonops_sdk import Metric
        import socket
        merged = MetricBatch(source="merged", host=socket.gethostname())
        for r in results:
            if isinstance(r, MetricBatch):
                merged.metrics.extend(r.metrics)
                merged.metadata.update(r.metadata)
        return merged

    async def _gate(self, decision: Decision, batch: MetricBatch) -> None:
        tier = decision.confidence_tier

        if tier == ConfidenceTier.AUTO:
            logger.info(f"AUTO executing: {decision.action.action_type.value}")
            result = await self._execute(decision)
            if self.settings.notify_on_auto:
                self._emit("action_executed", self._result_summary(decision, result))
            else:
                self._emit("action_executed", self._result_summary(decision, result))
            await self._persist_episode(decision, result, batch)

        elif tier == ConfidenceTier.NOTIFY:
            logger.info(f"NOTIFY executing: {decision.action.action_type.value}")
            result = await self._execute(decision)
            self._emit("action_executed", self._result_summary(decision, result))
            await self._notify(decision)
            await self._persist_episode(decision, result, batch)

        elif tier == ConfidenceTier.HUMAN:
            logger.info(f"HUMAN approval required: {decision.action.action_type.value}")
            self._pending_approvals[decision.action.action_id] = decision
            self._emit("approval_required", self._decision_summary(decision))

    async def _execute(self, decision: Decision) -> ActionResult:
        """Find the right action plugin and execute."""
        action_name = decision.action.action_type.value
        # Try matching action plugin by target type hint in params
        source = decision.action.params.get("source", list(self.actions.keys())[0] if self.actions else None)
        executor = self.actions.get(source) or (list(self.actions.values())[0] if self.actions else None)

        if not executor:
            return ActionResult(
                action_id=decision.action.action_id,
                status=ActionStatus.FAILED,
                message="No action plugin available",
            )

        if not await executor.validate(decision.action):
            return ActionResult(
                action_id=decision.action.action_id,
                status=ActionStatus.SKIPPED,
                message="Action validation failed",
            )

        return await executor.execute(decision.action)

    async def approve(self, action_id: str) -> ActionResult | None:
        """Approve a pending human-gate decision."""
        decision = self._pending_approvals.pop(action_id, None)
        if not decision:
            return None
        result = await self._execute(decision)
        self._emit("action_executed", self._result_summary(decision, result))
        await self._persist_episode(decision, result, self._latest_metrics or MetricBatch(source="unknown", host="unknown"))
        return result

    def reject(self, action_id: str) -> bool:
        """Reject a pending decision."""
        decision = self._pending_approvals.pop(action_id, None)
        if decision:
            self._emit("action_rejected", {"action_id": action_id, "reason": decision.action.reason})
            return True
        return False

    # ── Persistence ────────────────────────────────────────────────────────────

    async def _persist_metrics(self, batch: MetricBatch) -> None:
        try:
            async with AsyncSessionLocal() as session:
                records = [
                    MetricRecord(
                        time=m.timestamp, source=batch.source, host=batch.host,
                        name=m.name, value=m.value, unit=m.unit,
                        tags_json=json.dumps(m.tags),
                    )
                    for m in batch.metrics
                ]
                session.add_all(records)
                await session.commit()
        except Exception as e:
            logger.debug(f"Metric persist skipped: {e}")

    async def _persist_decision(self, decision: Decision) -> None:
        try:
            async with AsyncSessionLocal() as session:
                record = DecisionRecord(
                    decision_id    = decision.action.action_id,
                    action_type    = decision.action.action_type.value,
                    target         = decision.action.target,
                    confidence     = decision.confidence,
                    confidence_tier= decision.confidence_tier.value,
                    anomaly_score  = decision.anomaly_score,
                    reasoning      = decision.reasoning,
                    strategy_name  = decision.strategy_name,
                    params_json    = json.dumps(decision.action.params),
                    status         = "pending",
                    environment    = self.settings.environment,
                )
                session.add(record)
                await session.commit()
        except Exception as e:
            logger.debug(f"Decision persist skipped: {e}")

    async def _persist_episode(
        self, decision: Decision, result: ActionResult, batch: MetricBatch,
    ) -> None:
        try:
            state = {m.name: m.value for m in batch.metrics[:50]}
            async with AsyncSessionLocal() as session:
                record = EpisodeRecord(
                    episode_id     = str(uuid.uuid4()),
                    decision_id    = decision.action.action_id,
                    action_type    = decision.action.action_type.value,
                    target         = decision.action.target,
                    confidence     = decision.confidence,
                    anomaly_score  = decision.anomaly_score,
                    outcome_status = result.status.value,
                    outcome_message= result.message,
                    duration_ms    = result.duration_ms,
                    state_json     = json.dumps(state),
                    environment    = self.settings.environment,
                    was_successful = result.succeeded,
                )
                session.add(record)
                # Update decision status
                from sqlalchemy import select
                stmt = select(DecisionRecord).where(
                    DecisionRecord.decision_id == decision.action.action_id
                )
                dr = (await session.execute(stmt)).scalar_one_or_none()
                if dr:
                    dr.status = "executed"
                    dr.approved_by = "auto" if decision.confidence_tier != ConfidenceTier.HUMAN else "human"
                await session.commit()
        except Exception as e:
            logger.debug(f"Episode persist skipped: {e}")

    async def _fetch_episode_context(self, limit: int = 10) -> list:
        """Fetch recent successful episodes for context."""
        try:
            from sqlalchemy import select
            async with AsyncSessionLocal() as session:
                stmt = (
                    select(EpisodeRecord)
                    .where(EpisodeRecord.was_successful == True)
                    .order_by(EpisodeRecord.time.desc())
                    .limit(limit)
                )
                rows = (await session.execute(stmt)).scalars().all()
                return rows  # raw ORM objects — strategy receives them as context
        except Exception:
            return []

    async def _notify(self, decision: Decision) -> None:
        webhook = self.settings.slack_webhook_url
        if not webhook:
            return
        try:
            import httpx
            payload = {
                "text": (
                    f"*[AxonOps]* Action executed (notify tier)\n"
                    f"• *Type:* `{decision.action.action_type.value}`\n"
                    f"• *Target:* `{decision.action.target}`\n"
                    f"• *Reason:* {decision.action.reason}\n"
                    f"• *Confidence:* {decision.confidence:.0%}"
                )
            }
            async with httpx.AsyncClient() as client:
                await client.post(webhook, json=payload, timeout=5)
        except Exception as e:
            logger.warning(f"Slack notify failed: {e}")

    # ── Event bus ──────────────────────────────────────────────────────────────

    def on_event(self, callback: Callable[[dict], None]) -> None:
        self._subscribers.append(callback)

    def _emit(self, event_type: str, payload: dict) -> None:
        event = {"type": event_type, "ts": datetime.utcnow().isoformat(), **payload}
        for cb in self._subscribers:
            try:
                cb(event)
            except Exception:
                pass

    # ── Summaries (for WebSocket / API) ───────────────────────────────────────

    def _metrics_summary(self, batch: MetricBatch) -> dict:
        return {
            "source": batch.source,
            "host":   batch.host,
            "count":  len(batch.metrics),
            "snapshot": {m.name: m.value for m in batch.metrics[:20]},
        }

    def _decision_summary(self, decision: Decision) -> dict:
        return {
            "action_id":      decision.action.action_id,
            "action_type":    decision.action.action_type.value,
            "target":         decision.action.target,
            "confidence":     decision.confidence,
            "tier":           decision.confidence_tier.value,
            "anomaly_score":  decision.anomaly_score,
            "reasoning":      decision.reasoning,
            "reason":         decision.action.reason,
            "strategy":       decision.strategy_name,
        }

    def _result_summary(self, decision: Decision, result: ActionResult) -> dict:
        return {
            **self._decision_summary(decision),
            "status":      result.status.value,
            "message":     result.message,
            "duration_ms": result.duration_ms,
            "succeeded":   result.succeeded,
        }

    @property
    def pending_approvals(self) -> list[dict]:
        return [self._decision_summary(d) for d in self._pending_approvals.values()]

    @property
    def status(self) -> dict:
        return {
            "running":     self._running,
            "collectors":  [c.plugin_name for c in self.collectors],
            "actions":     list(self.actions.keys()),
            "strategy":    self.strategy.strategy_name if self.strategy else None,
            "pending":     len(self._pending_approvals),
        }
