"""
axonops_core.api.app
~~~~~~~~~~~~~~~~~~~~
FastAPI application — REST endpoints + WebSocket live feed for the UI.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from axonops_core.engine.engine import AxonOpsEngine

logger = logging.getLogger("axonops.api")

# ── Engine singleton ──────────────────────────────────────────────────────────
engine = AxonOpsEngine(tick_interval=10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await engine.setup()
    task = asyncio.create_task(engine.start())
    yield
    await engine.stop()
    task.cancel()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AxonOps",
    description="Neural Infrastructure Engine — REST API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── WebSocket connection manager ──────────────────────────────────────────────
class ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.remove(ws)

    async def broadcast(self, data: Any) -> None:
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.remove(ws)


manager = ConnectionManager()


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health() -> dict:
    return {
        "status":        "ok",
        "running":       engine._running,
        "episode_count": engine.episode_count,
        "collectors":    engine.collectors_status,
    }


@app.get("/api/feed")
async def get_feed() -> dict:
    return {"feed": engine.feed}


@app.get("/api/pending")
async def get_pending() -> dict:
    return {"pending": engine.pending_decisions}


@app.get("/api/episodes")
async def get_episodes(limit: int = 50) -> dict:
    episodes = engine._episodes[-limit:]
    return {
        "episodes": [
            {
                "episode_id":    ep.episode_id,
                "timestamp":     ep.timestamp.isoformat(),
                "environment":   ep.environment,
                "action_type":   ep.decision.action.action_type.value,
                "target":        ep.decision.action.target,
                "confidence":    ep.decision.confidence,
                "reasoning":     ep.decision.reasoning,
                "anomaly_score": ep.decision.anomaly_score,
                "outcome":       ep.result.status.value,
                "message":       ep.result.message,
                "duration_ms":   ep.result.duration_ms,
            }
            for ep in reversed(episodes)
        ]
    }


@app.get("/api/stats")
async def get_stats() -> dict:
    episodes = engine._episodes
    total    = len(episodes)
    success  = sum(1 for ep in episodes if ep.was_successful)
    return {
        "total_episodes": total,
        "success_rate":   round(success / total, 3) if total else 0,
        "pending_count":  len(engine._pending),
        "collector_count":len(engine._collectors),
        "strategy_count": len(engine._strategies),
    }


class ApprovalBody(BaseModel):
    action_id: str


@app.post("/api/approve")
async def approve(body: ApprovalBody) -> dict:
    ok = await engine.approve(body.action_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Decision not found in pending queue")
    return {"status": "approved", "action_id": body.action_id}


@app.post("/api/reject")
async def reject(body: ApprovalBody) -> dict:
    ok = await engine.reject(body.action_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Decision not found in pending queue")
    return {"status": "rejected", "action_id": body.action_id}


# ── WebSocket live feed ───────────────────────────────────────────────────────

@app.websocket("/ws/feed")
async def websocket_feed(ws: WebSocket) -> None:
    await manager.connect(ws)
    # Send current feed on connect
    await ws.send_json({"type": "init", "feed": engine.feed, "pending": engine.pending_decisions})

    last_len  = len(engine._feed)
    last_pend = len(engine._pending)

    try:
        while True:
            await asyncio.sleep(1)
            new_len  = len(engine._feed)
            new_pend = len(engine._pending)

            if new_len != last_len or new_pend != last_pend:
                await ws.send_json({
                    "type":    "update",
                    "feed":    engine.feed[:10],
                    "pending": engine.pending_decisions,
                    "stats":   {
                        "total_episodes": engine.episode_count,
                        "pending_count":  len(engine._pending),
                    },
                })
                last_len  = new_len
                last_pend = new_pend
    except WebSocketDisconnect:
        manager.disconnect(ws)
