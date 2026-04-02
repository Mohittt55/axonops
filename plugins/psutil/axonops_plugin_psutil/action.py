"""psutil action — process-level actions on the local machine."""
from __future__ import annotations

import os
import signal
import time

import psutil

from axonops_sdk import Action, ActionResult, ActionStatus, ActionType, BaseAction


class PsutilAction(BaseAction):
    plugin_name      = "psutil"
    plugin_version   = "1.0.0"
    supported_actions = [
        ActionType.KILL_PROCESS.value,
        ActionType.RESTART.value,
        ActionType.NOTIFY.value,
    ]

    async def validate(self, action: Action) -> bool:
        if action.action_type == ActionType.KILL_PROCESS:
            pid = action.params.get("pid")
            if not pid:
                return False
            return psutil.pid_exists(int(pid))
        return True

    async def execute(self, action: Action) -> ActionResult:
        start = time.monotonic()

        try:
            if action.action_type == ActionType.KILL_PROCESS:
                result = await self._kill_process(action)
            elif action.action_type == ActionType.RESTART:
                result = await self._restart_process(action)
            elif action.action_type == ActionType.NOTIFY:
                result = ActionResult(
                    action_id=action.action_id,
                    status=ActionStatus.SUCCESS,
                    message=f"Notification: {action.reason}",
                )
            else:
                result = ActionResult(
                    action_id=action.action_id,
                    status=ActionStatus.SKIPPED,
                    message=f"Unsupported action: {action.action_type}",
                )
        except Exception as e:
            result = ActionResult(
                action_id=action.action_id,
                status=ActionStatus.FAILED,
                message=str(e),
            )

        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    async def _kill_process(self, action: Action) -> ActionResult:
        pid  = int(action.params["pid"])
        proc = psutil.Process(pid)
        name = proc.name()
        proc.send_signal(signal.SIGTERM)
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message=f"Sent SIGTERM to {name} (pid={pid})",
            side_effects={"pid": pid, "process_name": name},
        )

    async def _restart_process(self, action: Action) -> ActionResult:
        cmd = action.params.get("command")
        if not cmd:
            return ActionResult(
                action_id=action.action_id,
                status=ActionStatus.FAILED,
                message="restart action requires params.command",
            )
        os.system(cmd)
        return ActionResult(
            action_id=action.action_id,
            status=ActionStatus.SUCCESS,
            message=f"Executed restart command: {cmd}",
            side_effects={"command": cmd},
        )
