"""
axonops-plugin-aws
~~~~~~~~~~~~~~~~~~
Collector, Action, and Strategy for AWS infrastructure via boto3.
Covers EC2 Auto Scaling, RDS, ElastiCache, and CloudWatch.
"""
from __future__ import annotations

import socket
import time
import uuid
from datetime import datetime, timedelta

from axonops_sdk import (
    Action, ActionResult, ActionStatus, ActionType,
    BaseAction, BaseCollector, BaseStrategy,
    Decision, Episode, Metric, MetricBatch,
)

try:
    import boto3
    
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


# ── Collector ─────────────────────────────────────────────────────────────────

class AWSCollector(BaseCollector):
    plugin_name    = "aws"
    plugin_version = "1.0.0"
    description    = "CloudWatch metrics: EC2, RDS, ElastiCache, ALB"

    def __init__(
        self,
        region: str = "us-east-1",
        asg_names: list[str] | None = None,
        rds_instances: list[str] | None = None,
    ) -> None:
        self.region        = region
        self.asg_names     = asg_names or []
        self.rds_instances = rds_instances or []
        self._cw           = None
        self._asg          = None

    async def setup(self) -> None:
        if not BOTO3_AVAILABLE:
            return
        try:
            session      = boto3.Session(region_name=self.region)
            self._cw     = session.client("cloudwatch")
            self._asg    = session.client("autoscaling")
            self._ec2    = session.client("ec2")
        except Exception:
            pass

    def _cw_metric(
        self,
        namespace: str,
        metric_name: str,
        dimensions: list[dict],
        stat: str = "Average",
        period: int = 300,
    ) -> float:
        """Fetch the latest CloudWatch metric value."""
        end   = datetime.utcnow()
        start = end - timedelta(seconds=period * 2)
        resp  = self._cw.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=start,
            EndTime=end,
            Period=period,
            Statistics=[stat],
        )
        points = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
        return points[-1][stat] if points else 0.0

    async def collect(self) -> MetricBatch:
        metrics: list[Metric] = []
        now  = datetime.utcnow()
        host = socket.gethostname()

        if not self._cw:
            return MetricBatch(
                source=self.plugin_name, host=host,
                metadata={"error": "boto3 client unavailable — check AWS credentials"},
            )

        # EC2 / ASG metrics
        for asg_name in self.asg_names:
            try:
                dims = [{"Name": "AutoScalingGroupName", "Value": asg_name}]
                cpu = self._cw_metric("AWS/EC2", "CPUUtilization", dims)
                metrics += [
                    Metric(name="aws_asg_cpu_percent", value=round(cpu, 2), unit="%",
                           timestamp=now, tags={"asg": asg_name, "region": self.region}),
                ]
                # ASG capacity
                resp = self._asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
                for grp in resp.get("AutoScalingGroups", []):
                    metrics += [
                        Metric(name="aws_asg_desired",  value=float(grp["DesiredCapacity"]),  timestamp=now, tags={"asg": asg_name}),
                        Metric(name="aws_asg_min",       value=float(grp["MinSize"]),           timestamp=now, tags={"asg": asg_name}),
                        Metric(name="aws_asg_max",       value=float(grp["MaxSize"]),           timestamp=now, tags={"asg": asg_name}),
                        Metric(name="aws_asg_instances", value=float(len(grp["Instances"])),    timestamp=now, tags={"asg": asg_name}),
                    ]
            except Exception as e:
                metrics.append(Metric(name="aws_collection_error", value=1.0, timestamp=now,
                                      tags={"resource": asg_name, "error": str(e)[:80]}))

        # RDS metrics
        for db_id in self.rds_instances:
            try:
                dims = [{"Name": "DBInstanceIdentifier", "Value": db_id}]
                connections = self._cw_metric("AWS/RDS", "DatabaseConnections", dims)
                cpu         = self._cw_metric("AWS/RDS", "CPUUtilization", dims)
                read_iops   = self._cw_metric("AWS/RDS", "ReadIOPS", dims)
                write_iops  = self._cw_metric("AWS/RDS", "WriteIOPS", dims)
                metrics += [
                    Metric(name="aws_rds_connections",  value=round(connections, 0), timestamp=now, tags={"db": db_id}),
                    Metric(name="aws_rds_cpu_percent",  value=round(cpu, 2),         unit="%", timestamp=now, tags={"db": db_id}),
                    Metric(name="aws_rds_read_iops",    value=round(read_iops, 0),   timestamp=now, tags={"db": db_id}),
                    Metric(name="aws_rds_write_iops",   value=round(write_iops, 0),  timestamp=now, tags={"db": db_id}),
                ]
            except Exception as e:
                metrics.append(Metric(name="aws_collection_error", value=1.0, timestamp=now,
                                      tags={"resource": db_id, "error": str(e)[:80]}))

        return MetricBatch(
            source=self.plugin_name, host=host, metrics=metrics,
            metadata={"region": self.region, "asgs": len(self.asg_names), "rds": len(self.rds_instances)},
        )

    async def health_check(self) -> bool:
        if not self._cw:
            return False
        try:
            self._cw.list_metrics(MaxRecords=1)
            return True
        except Exception:
            return False


# ── Action ────────────────────────────────────────────────────────────────────

class AWSAction(BaseAction):
    plugin_name      = "aws"
    plugin_version   = "1.0.0"
    supported_actions = [
        ActionType.SCALE_UP.value,
        ActionType.SCALE_DOWN.value,
        ActionType.NOTIFY.value,
        ActionType.RESTART.value,
    ]

    def __init__(self, region: str = "us-east-1") -> None:
        self.region = region
        self._asg   = None

    async def setup(self) -> None:
        if BOTO3_AVAILABLE:
            try:
                self._asg = boto3.client("autoscaling", region_name=self.region)
            except Exception:
                pass

    async def validate(self, action: Action) -> bool:
        return self._asg is not None

    async def execute(self, action: Action) -> ActionResult:
        start = time.monotonic()
        try:
            if action.action_type in (ActionType.SCALE_UP, ActionType.SCALE_DOWN):
                result = await self._set_asg_capacity(action)
            elif action.action_type == ActionType.NOTIFY:
                result = ActionResult(
                    action_id=action.action_id, status=ActionStatus.SUCCESS,
                    message=f"[AWS] {action.reason}",
                )
            else:
                result = ActionResult(
                    action_id=action.action_id, status=ActionStatus.SKIPPED,
                    message=f"No AWS handler for {action.action_type}",
                )
        except Exception as e:
            result = ActionResult(action_id=action.action_id,
                                  status=ActionStatus.FAILED, message=str(e))

        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    async def _set_asg_capacity(self, action: Action) -> ActionResult:
        desired  = action.params.get("desired_capacity")
        asg_name = action.target
        if desired is None:
            return ActionResult(action_id=action.action_id,
                                status=ActionStatus.FAILED,
                                message="params.desired_capacity is required")
        self._asg.set_desired_capacity(
            AutoScalingGroupName=asg_name,
            DesiredCapacity=int(desired),
            HonorCooldown=True,
        )
        return ActionResult(
            action_id=action.action_id, status=ActionStatus.SUCCESS,
            message=f"ASG {asg_name!r} desired capacity set to {desired}",
            side_effects={"asg": asg_name, "desired_capacity": desired},
        )


# ── Strategy ──────────────────────────────────────────────────────────────────

class AWSStrategy(BaseStrategy):
    strategy_name    = "aws-autoscaling"
    strategy_version = "1.0.0"
    description      = "Cost-aware ASG scaling based on CPU and instance utilization"

    CPU_SCALE_UP_THRESHOLD   = 70.0  # scale up above this
    CPU_SCALE_DOWN_THRESHOLD = 25.0  # scale down below this
    MAX_SCALE_STEP           = 2     # max instances to add per decision

    def evaluate(
        self,
        metrics: MetricBatch,
        context: list[Episode],
    ) -> Decision | None:
        cpu_metrics = [m for m in metrics.metrics if m.name == "aws_asg_cpu_percent"]

        for m in cpu_metrics:
            asg     = m.tags.get("asg", "unknown")
            desired = metrics.get_value("aws_asg_desired")
            max_cap = metrics.get_value("aws_asg_max")
            min_cap = metrics.get_value("aws_asg_min")

            if m.value > self.CPU_SCALE_UP_THRESHOLD and desired < max_cap:
                new_desired = min(int(desired) + self.MAX_SCALE_STEP, int(max_cap))
                confidence  = self._confidence(context, "scale_up")
                action = Action(
                    action_id=str(uuid.uuid4()),
                    action_type=ActionType.SCALE_UP,
                    target=asg,
                    reason=f"ASG {asg!r} CPU at {m.value:.1f}% — scaling up to {new_desired}",
                    params={"desired_capacity": new_desired, "asg": asg},
                )
                return Decision(
                    action=action, confidence=confidence,
                    reasoning=f"CPU {m.value:.1f}% > {self.CPU_SCALE_UP_THRESHOLD}% threshold, adding capacity",
                    anomaly_score=(m.value - self.CPU_SCALE_UP_THRESHOLD) / (100 - self.CPU_SCALE_UP_THRESHOLD),
                    strategy_name=self.strategy_name,
                )

            if m.value < self.CPU_SCALE_DOWN_THRESHOLD and desired > min_cap:
                new_desired = max(int(desired) - 1, int(min_cap))
                confidence  = self._confidence(context, "scale_down") * 0.85  # more conservative
                action = Action(
                    action_id=str(uuid.uuid4()),
                    action_type=ActionType.SCALE_DOWN,
                    target=asg,
                    reason=f"ASG {asg!r} CPU at {m.value:.1f}% — scaling down to {new_desired}",
                    params={"desired_capacity": new_desired, "asg": asg},
                )
                return Decision(
                    action=action, confidence=confidence,
                    reasoning=f"CPU {m.value:.1f}% < {self.CPU_SCALE_DOWN_THRESHOLD}% — reducing cost",
                    anomaly_score=0.2,
                    strategy_name=self.strategy_name,
                )

        return None

    def _confidence(self, context: list[Episode], action_type: str) -> float:
        relevant = [ep for ep in context
                    if ep.decision.action.action_type.value == action_type]
        if not relevant:
            return 0.68
        successes = sum(1 for ep in relevant if ep.was_successful)
        return round(0.65 + (successes / len(relevant)) * 0.25, 2)
