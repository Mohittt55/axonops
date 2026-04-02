"""
axonops_core.memory
~~~~~~~~~~~~~~~~~~~
Episode storage and retrieval.
Dev mode: SQLite (zero config).
Production: TimescaleDB via asyncpg (set DATABASE_URL env var).
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime

from axonops_sdk import Episode

logger = logging.getLogger("axonops.memory")


class MemoryStore:
    """
    SQLite-backed episode store for development.
    Stores (state_snapshot, action, outcome) tuples and supports
    cosine-similarity retrieval for episode context.
    """

    def __init__(self, db_path: str = "axonops_memory.db") -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def setup(self) -> None:
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                episode_id   TEXT PRIMARY KEY,
                timestamp    TEXT NOT NULL,
                environment  TEXT NOT NULL,
                state_json   TEXT NOT NULL,
                action_type  TEXT NOT NULL,
                action_target TEXT NOT NULL,
                action_reason TEXT,
                confidence   REAL,
                anomaly_score REAL,
                strategy_name TEXT,
                result_status TEXT,
                result_message TEXT,
                was_successful INTEGER
            )
        """)
        self._conn.commit()
        logger.info(f"Memory store ready: {self.db_path}")

    async def save(self, episode: Episode) -> None:
        if not self._conn:
            return
        try:
            self._conn.execute("""
                INSERT OR REPLACE INTO episodes VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                episode.episode_id,
                episode.timestamp.isoformat(),
                episode.environment,
                json.dumps(episode.state_snapshot),
                episode.decision.action.action_type.value,
                episode.decision.action.target,
                episode.decision.action.reason,
                episode.decision.confidence,
                episode.decision.anomaly_score,
                episode.decision.strategy_name,
                episode.result.status.value,
                episode.result.message,
                int(episode.was_successful),
            ))
            self._conn.commit()
        except Exception as e:
            logger.warning(f"Failed to save episode: {e}")

    async def find_similar(
        self,
        state_vec: dict[str, float],
        limit: int = 10,
    ) -> list[Episode]:
        """
        Find the most similar past episodes using cosine similarity
        on the state snapshot vectors.
        In production this would use pgvector — here we do it in Python.
        """
        if not self._conn:
            return []

        rows = self._conn.execute(
            "SELECT * FROM episodes ORDER BY timestamp DESC LIMIT 200"
        ).fetchall()

        scored: list[tuple[float, tuple]] = []
        for row in rows:
            try:
                past_state: dict[str, float] = json.loads(row[3])
                sim = _cosine_similarity(state_vec, past_state)
                scored.append((sim, row))
            except Exception:
                continue

        scored.sort(key=lambda x: x[0], reverse=True)
        return [_row_to_episode(r) for _, r in scored[:limit]]

    def get_stats(self) -> dict:
        if not self._conn:
            return {}
        total    = self._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        success  = self._conn.execute("SELECT COUNT(*) FROM episodes WHERE was_successful=1").fetchone()[0]
        by_type  = self._conn.execute(
            "SELECT action_type, COUNT(*) FROM episodes GROUP BY action_type"
        ).fetchall()
        return {
            "total_episodes": total,
            "successful": success,
            "success_rate": round(success / total, 3) if total else 0,
            "by_action_type": dict(by_type),
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    keys  = set(a) & set(b)
    if not keys:
        return 0.0
    dot   = sum(a[k] * b[k] for k in keys)
    mag_a = math.sqrt(sum(a[k] ** 2 for k in keys))
    mag_b = math.sqrt(sum(b[k] ** 2 for k in keys))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _row_to_episode(row: tuple) -> Episode:
    from axonops_sdk import Action, ActionResult, ActionStatus, ActionType, Decision
    action = Action(
        action_id   = row[0],
        action_type = ActionType(row[4]),
        target      = row[5],
        reason      = row[6] or "",
    )
    decision = Decision(
        action        = action,
        confidence    = row[7] or 0.0,
        anomaly_score = row[8] or 0.0,
        strategy_name = row[9] or "",
    )
    result = ActionResult(
        action_id = row[0],
        status    = ActionStatus(row[10]),
        message   = row[11] or "",
    )
    return Episode(
        episode_id     = row[0],
        timestamp      = datetime.fromisoformat(row[1]),
        environment    = row[2],
        state_snapshot = json.loads(row[3]),
        decision       = decision,
        result         = result,
    )
