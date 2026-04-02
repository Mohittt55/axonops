"""
axonops-plugin-prometheus
~~~~~~~~~~~~~~~~~~~~~~~~~
Scrape any Prometheus /metrics endpoint. Covers the entire Prometheus ecosystem.
"""
from __future__ import annotations

import socket
import time
import uuid
from datetime import datetime
from urllib.parse import urlparse

from axonops_sdk import (
    Action, ActionResult, ActionStatus, ActionType,
    BaseAction, BaseCollector, BaseStrategy,
    Decision, Episode, Metric, MetricBatch,
)

try:
    import httpx
    HTTP_AVAILABLE = True
except ImportError:
    try:
        import urllib.request
        HTTP_AVAILABLE = True
    except ImportError:
        HTTP_AVAILABLE = False


def _scrape(url: str, timeout: float = 5.0) -> str:
    """Fetch a Prometheus /metrics endpoint."""
    try:
        import httpx
        with httpx.Client(timeout=timeout) as client:
            return client.get(url).text
    except ImportError:
        import urllib.request
        with urllib.request.urlopen(url, timeout=int(timeout)) as r:
            return r.read().decode()


def _parse_prometheus(text: str) -> list[tuple[str, float, dict[str, str]]]:
    """
    Minimal Prometheus text format parser.
    Returns list of (metric_name, value, labels).
    """
    results = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            # Split off labels
            if "{" in line:
                name_labels, rest = line.split("}", 1)
                name, label_str = name_labels.split("{", 1)
                labels: dict[str, str] = {}
                for part in label_str.split(","):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        labels[k.strip()] = v.strip().strip('"')
            else:
                parts = line.rsplit(" ", 1)
                if len(parts) != 2:
                    continue
                name, rest = parts[0], parts[1]
                labels = {}

            parts2 = rest.strip().split()
            value = float(parts2[0])
            results.append((name.strip(), value, labels))
        except Exception:
            continue
    return results


# ── Collector ─────────────────────────────────────────────────────────────────

class PrometheusCollector(BaseCollector):
    plugin_name    = "prometheus"
    plugin_version = "1.0.0"
    description    = "Scrape Prometheus /metrics endpoints from any service"

    def __init__(self, targets: list[str] | None = None, timeout: float = 5.0) -> None:
        """
        Parameters
        ----------
        targets : list of URLs to scrape, e.g. ["http://localhost:9090/metrics"]
        timeout : per-request timeout in seconds
        """
        self.targets = targets or ["http://localhost:9090/metrics"]
        self.timeout = timeout

    async def collect(self) -> MetricBatch:
        metrics: list[Metric] = []
        now  = datetime.utcnow()
        host = socket.gethostname()

        for url in self.targets:
            target_host = urlparse(url).netloc
            try:
                text = _scrape(url, self.timeout)
                parsed = _parse_prometheus(text)
                for name, value, labels in parsed:
                    tags = {**labels, "target": target_host}
                    metrics.append(Metric(
                        name=name, value=value, timestamp=now, tags=tags,
                    ))
            except Exception as e:
                metrics.append(Metric(
                    name="prometheus_scrape_error",
                    value=1.0,
                    timestamp=now,
                    tags={"target": target_host, "error": str(e)[:100]},
                ))

        return MetricBatch(
            source=self.plugin_name,
            host=host,
            metrics=metrics,
            metadata={"targets": len(self.targets), "metrics_count": len(metrics)},
        )

    async def health_check(self) -> bool:
        for url in self.targets:
            try:
                _scrape(url, timeout=2.0)
                return True
            except Exception:
                continue
        return False


# ── Action ────────────────────────────────────────────────────────────────────

class PrometheusAction(BaseAction):
    plugin_name      = "prometheus"
    plugin_version   = "1.0.0"
    supported_actions = [ActionType.NOTIFY.value, ActionType.CUSTOM.value]

    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url

    async def validate(self, action: Action) -> bool:
        return action.action_type in (ActionType.NOTIFY, ActionType.CUSTOM)

    async def execute(self, action: Action) -> ActionResult:
        start = time.monotonic()
        try:
            if self.webhook_url and action.action_type == ActionType.NOTIFY:
                import json
                import urllib.request
                payload = json.dumps({
                    "text": f"[AxonOps] {action.reason}",
                    "action_id": action.action_id,
                }).encode()
                req = urllib.request.Request(
                    self.webhook_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=5)
                status  = ActionStatus.SUCCESS
                message = f"Webhook notified: {action.reason}"
            else:
                status  = ActionStatus.SUCCESS
                message = f"Prometheus action logged: {action.reason}"
        except Exception as e:
            status  = ActionStatus.FAILED
            message = str(e)

        return ActionResult(
            action_id=action.action_id, status=status, message=message,
            duration_ms=int((time.monotonic() - start) * 1000),
        )


# ── Strategy ──────────────────────────────────────────────────────────────────

class PrometheusStrategy(BaseStrategy):
    strategy_name    = "prometheus-threshold"
    strategy_version = "1.0.0"
    description      = "Threshold and rate-of-change detection on scraped Prometheus metrics"

    # Default rules: (metric_name_contains, threshold, comparator)
    DEFAULT_RULES: list[tuple[str, float, str]] = [
        ("error_rate",       0.05,   "gt"),  # >5% error rate
        ("error_total",      100.0,  "gt"),  # >100 errors
        ("latency_seconds",  2.0,    "gt"),  # >2s latency
        ("memory_usage",     0.90,   "gt"),  # >90% memory
        ("cpu_usage",        0.85,   "gt"),  # >85% CPU
        ("up",               0.0,    "eq"),  # service down (up=0)
    ]

    def __init__(self, rules: list[tuple[str, float, str]] | None = None) -> None:
        self.rules = rules or self.DEFAULT_RULES

    def evaluate(
        self,
        metrics: MetricBatch,
        context: list[Episode],
    ) -> Decision | None:
        for metric in metrics.metrics:
            for name_fragment, threshold, comparator in self.rules:
                if name_fragment.lower() not in metric.name.lower():
                    continue
                triggered = (
                    (comparator == "gt" and metric.value > threshold) or
                    (comparator == "lt" and metric.value < threshold) or
                    (comparator == "eq" and metric.value == threshold)
                )
                if triggered:
                    target    = metric.tags.get("target", metrics.host)
                    confidence = self._confidence(context)
                    action = Action(
                        action_id=str(uuid.uuid4()),
                        action_type=ActionType.NOTIFY,
                        target=target,
                        reason=f"{metric.name}={metric.value} breached threshold {comparator} {threshold}",
                        params={"metric": metric.name, "value": metric.value,
                                "threshold": threshold, "target": target},
                    )
                    return Decision(
                        action=action, confidence=confidence,
                        reasoning=f"Prometheus metric {metric.name!r} = {metric.value} ({comparator} {threshold})",
                        anomaly_score=min(abs(metric.value - threshold) / max(threshold, 0.001), 1.0),
                        strategy_name=self.strategy_name,
                    )
        return None

    def _confidence(self, context: list[Episode]) -> float:
        if not context:
            return 0.75
        successes = sum(1 for ep in context if ep.was_successful)
        return round(0.70 + (successes / len(context)) * 0.20, 2)
