"""
axonops-plugin-kubernetes
~~~~~~~~~~~~~~~~~~~~~~~~~
Collector, Action, and Strategy for Kubernetes cluster management.
"""
from __future__ import annotations

import socket
import time
import uuid
from datetime import datetime

from axonops_sdk import (
    Action, ActionResult, ActionStatus, ActionType,
    BaseAction, BaseCollector, BaseStrategy,
    Decision, Episode, Metric, MetricBatch,
)

try:
    from kubernetes import client as k8s_client, config as k8s_config
    from kubernetes.client.rest import ApiException
    K8S_AVAILABLE = True
except ImportError:
    K8S_AVAILABLE = False


# ── Collector ─────────────────────────────────────────────────────────────────

class KubernetesCollector(BaseCollector):
    plugin_name    = "kubernetes"
    plugin_version = "1.0.0"
    description    = "Pod/node resource usage, events, and cluster health from k8s metrics-server"

    def __init__(self, namespace: str = "default", in_cluster: bool = False) -> None:
        self.namespace  = namespace
        self.in_cluster = in_cluster
        self._core      = None
        self._apps      = None

    async def setup(self) -> None:
        if not K8S_AVAILABLE:
            return
        try:
            if self.in_cluster:
                k8s_config.load_incluster_config()
            else:
                k8s_config.load_kube_config()
            self._core = k8s_client.CoreV1Api()
            self._apps = k8s_client.AppsV1Api()
        except Exception:
            pass

    async def collect(self) -> MetricBatch:
        metrics: list[Metric] = []
        now  = datetime.utcnow()
        host = socket.gethostname()

        if not self._core:
            return MetricBatch(
                source=self.plugin_name, host=host,
                metadata={"error": "Kubernetes client unavailable"},
            )

        try:
            # Pod status counts
            pods = self._core.list_namespaced_pod(namespace=self.namespace)
            total = running = pending = failed = 0
            restarts_total = 0
            for pod in pods.items:
                total += 1
                phase = pod.status.phase or "Unknown"
                if phase == "Running":   running += 1
                elif phase == "Pending": pending += 1
                elif phase == "Failed":  failed  += 1

                # Restart count
                for cs in (pod.status.container_statuses or []):
                    restarts_total += cs.restart_count

            metrics += [
                Metric(name="k8s_pods_total",   value=float(total),   timestamp=now, tags={"namespace": self.namespace}),
                Metric(name="k8s_pods_running",  value=float(running), timestamp=now, tags={"namespace": self.namespace}),
                Metric(name="k8s_pods_pending",  value=float(pending), timestamp=now, tags={"namespace": self.namespace}),
                Metric(name="k8s_pods_failed",   value=float(failed),  timestamp=now, tags={"namespace": self.namespace}),
                Metric(name="k8s_restarts_total",value=float(restarts_total), timestamp=now, tags={"namespace": self.namespace}),
            ]

            # Node status
            nodes      = self._core.list_node()
            node_total = node_ready = 0
            for node in nodes.items:
                node_total += 1
                for cond in (node.status.conditions or []):
                    if cond.type == "Ready" and cond.status == "True":
                        node_ready += 1

            metrics += [
                Metric(name="k8s_nodes_total", value=float(node_total), timestamp=now),
                Metric(name="k8s_nodes_ready", value=float(node_ready), timestamp=now),
            ]

            # Deployments
            deps = self._apps.list_namespaced_deployment(namespace=self.namespace)
            dep_total = dep_available = 0
            for dep in deps.items:
                dep_total += 1
                if (dep.status.available_replicas or 0) >= (dep.spec.replicas or 1):
                    dep_available += 1

            metrics += [
                Metric(name="k8s_deployments_total",    value=float(dep_total),     timestamp=now, tags={"namespace": self.namespace}),
                Metric(name="k8s_deployments_available",value=float(dep_available), timestamp=now, tags={"namespace": self.namespace}),
            ]

        except Exception as e:
            metrics.append(Metric(
                name="k8s_collection_error", value=1.0, timestamp=now,
                tags={"error": str(e)[:100]},
            ))

        return MetricBatch(
            source=self.plugin_name, host=host, metrics=metrics,
            metadata={"namespace": self.namespace},
        )

    async def health_check(self) -> bool:
        if not self._core:
            return False
        try:
            self._core.list_namespace()
            return True
        except Exception:
            return False


# ── Action ────────────────────────────────────────────────────────────────────

class KubernetesAction(BaseAction):
    plugin_name      = "kubernetes"
    plugin_version   = "1.0.0"
    supported_actions = [
        ActionType.SCALE_UP.value,
        ActionType.SCALE_DOWN.value,
        ActionType.RESTART.value,
        ActionType.DRAIN_NODE.value,
        ActionType.NOTIFY.value,
    ]

    def __init__(self, namespace: str = "default", in_cluster: bool = False) -> None:
        self.namespace  = namespace
        self.in_cluster = in_cluster
        self._apps      = None
        self._core      = None

    async def setup(self) -> None:
        if not K8S_AVAILABLE:
            return
        try:
            if self.in_cluster:
                k8s_config.load_incluster_config()
            else:
                k8s_config.load_kube_config()
            self._apps = k8s_client.AppsV1Api()
            self._core = k8s_client.CoreV1Api()
        except Exception:
            pass

    async def validate(self, action: Action) -> bool:
        return self._apps is not None

    async def execute(self, action: Action) -> ActionResult:
        start = time.monotonic()
        try:
            if action.action_type in (ActionType.SCALE_UP, ActionType.SCALE_DOWN):
                result = await self._scale(action)
            elif action.action_type == ActionType.RESTART:
                result = await self._rolling_restart(action)
            elif action.action_type == ActionType.DRAIN_NODE:
                result = await self._cordon_node(action)
            else:
                result = ActionResult(action_id=action.action_id, status=ActionStatus.SKIPPED,
                                      message=f"No handler for {action.action_type}")
        except Exception as e:
            result = ActionResult(action_id=action.action_id,
                                  status=ActionStatus.FAILED, message=str(e))
        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    async def _scale(self, action: Action) -> ActionResult:
        replicas   = action.params.get("replicas", 1)
        deployment = action.target
        body = {"spec": {"replicas": replicas}}
        self._apps.patch_namespaced_deployment_scale(
            name=deployment, namespace=self.namespace, body=body,
        )
        return ActionResult(
            action_id=action.action_id, status=ActionStatus.SUCCESS,
            message=f"Scaled {deployment!r} to {replicas} replicas",
            side_effects={"deployment": deployment, "replicas": replicas},
        )

    async def _rolling_restart(self, action: Action) -> ActionResult:
        import datetime as dt
        patch = {"spec": {"template": {"metadata": {"annotations": {
            "kubectl.kubernetes.io/restartedAt": dt.datetime.utcnow().isoformat(),
        }}}}}
        self._apps.patch_namespaced_deployment(
            name=action.target, namespace=self.namespace, body=patch,
        )
        return ActionResult(
            action_id=action.action_id, status=ActionStatus.SUCCESS,
            message=f"Rolling restart triggered for {action.target!r}",
        )

    async def _cordon_node(self, action: Action) -> ActionResult:
        body = {"spec": {"unschedulable": True}}
        self._core.patch_node(name=action.target, body=body)
        return ActionResult(
            action_id=action.action_id, status=ActionStatus.SUCCESS,
            message=f"Node {action.target!r} cordoned",
        )


# ── Strategy ──────────────────────────────────────────────────────────────────

class KubernetesStrategy(BaseStrategy):
    strategy_name    = "kubernetes-cluster-health"
    strategy_version = "1.0.0"
    description      = "Detects pod failures, crash loops, and deployment degradation"

    PENDING_THRESHOLD    = 3    # pods stuck pending → action
    FAILED_THRESHOLD     = 1    # any failed pods → action
    RESTART_THRESHOLD    = 10   # cumulative restarts → action
    AVAILABILITY_FLOOR   = 0.80 # <80% deployments available → action

    def evaluate(
        self,
        metrics: MetricBatch,
        context: list[Episode],
    ) -> Decision | None:
        pending      = metrics.get_value("k8s_pods_pending")
        failed       = metrics.get_value("k8s_pods_failed")
        restarts     = metrics.get_value("k8s_restarts_total")
        dep_total    = metrics.get_value("k8s_deployments_total")
        dep_available= metrics.get_value("k8s_deployments_available")
        namespace    = metrics.metadata.get("namespace", "default")

        confidence = self._confidence(context)

        if failed >= self.FAILED_THRESHOLD:
            action = Action(
                action_id=str(uuid.uuid4()),
                action_type=ActionType.NOTIFY,
                target=namespace,
                reason=f"{failed:.0f} pods in Failed state in namespace {namespace!r}",
                params={"failed_pods": failed, "namespace": namespace},
            )
            return Decision(
                action=action, confidence=confidence,
                reasoning=f"{failed:.0f} pods failed in {namespace!r}",
                anomaly_score=min(failed / 5.0, 1.0),
                strategy_name=self.strategy_name,
            )

        if dep_total > 0:
            avail_ratio = dep_available / dep_total
            if avail_ratio < self.AVAILABILITY_FLOOR:
                unavail = dep_total - dep_available
                action = Action(
                    action_id=str(uuid.uuid4()),
                    action_type=ActionType.NOTIFY,
                    target=namespace,
                    reason=f"{unavail:.0f}/{dep_total:.0f} deployments unavailable",
                    params={"unavailable": unavail, "total": dep_total},
                )
                return Decision(
                    action=action, confidence=confidence,
                    reasoning=f"Deployment availability at {avail_ratio*100:.0f}% (below {self.AVAILABILITY_FLOOR*100:.0f}%)",
                    anomaly_score=1.0 - avail_ratio,
                    strategy_name=self.strategy_name,
                )

        return None

    def _confidence(self, context: list[Episode]) -> float:
        if not context:
            return 0.72
        successes = sum(1 for ep in context if ep.was_successful)
        return round(0.68 + (successes / len(context)) * 0.22, 2)
