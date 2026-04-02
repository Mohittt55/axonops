"""
axonops_core.api.app
~~~~~~~~~~~~~~~~~~~~
FastAPI application — REST endpoints + WebSocket live feed.
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

from ..config import get_settings
from ..database import create_tables
from ..engine import AxonOpsEngine

logger   = logging.getLogger("axonops.api")
settings = get_settings()

# ── Global engine instance ────────────────────────────────────────────────────
engine = AxonOpsEngine(settings)

# WebSocket connection pool
_ws_clients: list[WebSocket] = []

def _broadcast(event: dict) -> None:
    """Push events from engine to all connected WebSocket clients."""
    dead = []
    for ws in _ws_clients:
        try:
            asyncio.get_event_loop().create_task(ws.send_json(event))
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await create_tables()
    await engine.setup()
    engine.on_event(_broadcast)
    loop_task = asyncio.create_task(engine.start())
    logger.info("AxonOps API ready")
    yield
    # Shutdown
    await engine.stop()
    loop_task.cancel()


app = FastAPI(
    title="AxonOps",
    description="Neural Infrastructure Engine — REST + WebSocket API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── REST endpoints ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "engine": engine.status}


@app.get("/api/status")
async def status():
    return engine.status


@app.get("/api/metrics/latest")
async def latest_metrics():
    if not engine._latest_metrics:
        return {"metrics": []}
    batch = engine._latest_metrics
    return {
        "source":   batch.source,
        "host":     batch.host,
        "collected_at": batch.collected_at.isoformat(),
        "metrics": [
            {"name": m.name, "value": m.value, "unit": m.unit, "tags": m.tags}
            for m in batch.metrics
        ],
    }


@app.get("/api/decisions")
async def list_decisions(limit: int = 50, environment: str = "default"):
    from sqlalchemy import select, desc
    from ..database import AsyncSessionLocal, DecisionRecord
    async with AsyncSessionLocal() as session:
        stmt = (
            select(DecisionRecord)
            .where(DecisionRecord.environment == environment)
            .order_by(desc(DecisionRecord.time))
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "decision_id":    r.decision_id,
            "time":           r.time.isoformat(),
            "action_type":    r.action_type,
            "target":         r.target,
            "confidence":     r.confidence,
            "tier":           r.confidence_tier,
            "anomaly_score":  r.anomaly_score,
            "reasoning":      r.reasoning,
            "status":         r.status,
            "strategy":       r.strategy_name,
        }
        for r in rows
    ]


@app.get("/api/episodes")
async def list_episodes(limit: int = 50):
    from sqlalchemy import select, desc
    from ..database import AsyncSessionLocal, EpisodeRecord
    async with AsyncSessionLocal() as session:
        stmt = (
            select(EpisodeRecord)
            .order_by(desc(EpisodeRecord.time))
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "episode_id":      r.episode_id,
            "time":            r.time.isoformat(),
            "action_type":     r.action_type,
            "target":          r.target,
            "confidence":      r.confidence,
            "outcome_status":  r.outcome_status,
            "outcome_message": r.outcome_message,
            "duration_ms":     r.duration_ms,
            "was_successful":  r.was_successful,
        }
        for r in rows
    ]


@app.get("/api/approvals")
async def list_approvals():
    return {"pending": engine.pending_approvals}


class ApprovalAction(BaseModel):
    action: str  # "approve" | "reject"


@app.post("/api/approvals/{action_id}")
async def handle_approval(action_id: str, body: ApprovalAction):
    if body.action == "approve":
        result = await engine.approve(action_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Action not found")
        return {"status": result.status.value, "message": result.message}
    elif body.action == "reject":
        ok = engine.reject(action_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Action not found")
        return {"status": "rejected"}
    else:
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")


# ── WebSocket live feed ────────────────────────────────────────────────────────

@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.append(websocket)
    logger.info(f"WebSocket client connected ({len(_ws_clients)} total)")
    try:
        # Send current status immediately on connect
        await websocket.send_json({"type": "connected", "status": engine.status})
        while True:
            # Keep connection alive — client messages are ignored
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)
        logger.info(f"WebSocket client disconnected ({len(_ws_clients)} remaining)")
