"""
axonops_core.api
~~~~~~~~~~~~~~~~
FastAPI application exposing the engine state to the UI.
Endpoints: REST for feed/approvals, WebSocket for live streaming.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from ..engine.loop import AxonOpsEngine, EngineConfig

logger = logging.getLogger("axonops.api")

# Global engine instance — initialised in lifespan
_engine: AxonOpsEngine | None = None


def create_app(config: EngineConfig | None = None) -> FastAPI:
    cfg = config or EngineConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _engine
        _engine = AxonOpsEngine(config=cfg)
        await _engine.setup()
        # Run the engine loop in the background
        task = asyncio.create_task(_engine.run())
        yield
        await _engine.stop()
        task.cancel()

    app = FastAPI(
        title="AxonOps",
        description="Neural Infrastructure Engine — REST + WebSocket API",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── REST endpoints ─────────────────────────────────────────────────────────

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "engine_running": _engine._running if _engine else False}

    @app.get("/api/metrics")
    async def get_metrics():
        if not _engine or not _engine.live_metrics:
            return {"metrics": [], "source": None}
        batch = _engine.live_metrics
        return {
            "source":       batch.source,
            "host":         batch.host,
            "collected_at": batch.collected_at.isoformat(),
            "metrics": [
                {
                    "name":      m.name,
                    "value":     m.value,
                    "unit":      m.unit,
                    "tags":      m.tags,
                    "timestamp": m.timestamp.isoformat(),
                }
                for m in batch.metrics
            ],
        }

    @app.get("/api/feed")
    async def get_feed(limit: int = 50):
        if not _engine:
            return {"feed": []}
        return {"feed": _engine.decision_feed[:limit]}

    @app.get("/api/approvals")
    async def get_approvals():
        if not _engine:
            return {"approvals": []}
        return {"approvals": _engine.approval_queue}

    @app.post("/api/approvals/{action_id}/approve")
    async def approve(action_id: str):
        if not _engine:
            raise HTTPException(503, "Engine not running")
        result = await _engine.approve(action_id)
        if "error" in result:
            raise HTTPException(404, result["error"])
        return result

    @app.post("/api/approvals/{action_id}/reject")
    async def reject(action_id: str):
        if not _engine:
            raise HTTPException(503, "Engine not running")
        result = await _engine.reject(action_id)
        if "error" in result:
            raise HTTPException(404, result["error"])
        return result

    @app.get("/api/plugins")
    async def get_plugins():
        if not _engine:
            return {"collectors": [], "actions": [], "strategies": []}
        return {
            "collectors": [
                {"name": c.plugin_name, "version": c.plugin_version, "description": c.description}
                for c in _engine.collectors
            ],
            "actions": [
                {"name": a.plugin_name, "version": a.plugin_version, "supported": a.supported_actions}
                for a in _engine.actions.values()
            ],
            "strategies": [
                {"name": s.strategy_name, "version": s.strategy_version, "description": s.description}
                for s in _engine.strategies
            ],
        }

    # ── WebSocket — live feed ──────────────────────────────────────────────────

    class ConnectionManager:
        def __init__(self):
            self.active: list[WebSocket] = []

        async def connect(self, ws: WebSocket):
            await ws.accept()
            self.active.append(ws)

        def disconnect(self, ws: WebSocket):
            self.active.discard(ws) if hasattr(self.active, 'discard') else (
                self.active.remove(ws) if ws in self.active else None
            )

        async def broadcast(self, data: dict):
            dead = []
            for ws in self.active:
                try:
                    await ws.send_text(json.dumps(data))
                except Exception:
                    dead.append(ws)
            for ws in dead:
                if ws in self.active:
                    self.active.remove(ws)

    manager = ConnectionManager()

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await manager.connect(websocket)
        try:
            while True:
                if _engine:
                    payload: dict[str, Any] = {"type": "tick"}

                    # Live metrics snapshot
                    if _engine.live_metrics:
                        b = _engine.live_metrics
                        payload["metrics"] = {
                            m.name: {"value": m.value, "unit": m.unit, "tags": m.tags}
                            for m in b.metrics
                        }

                    # Latest decision
                    if _engine.decision_feed:
                        payload["latest_decision"] = _engine.decision_feed[0]

                    # Approval queue count
                    payload["pending_approvals"] = len(_engine.approval_queue)

                    await websocket.send_text(json.dumps(payload))

                await asyncio.sleep(2)  # push update every 2 seconds
        except WebSocketDisconnect:
            manager.disconnect(websocket)

    return app
