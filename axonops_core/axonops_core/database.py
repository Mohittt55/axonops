"""
axonops_core.database
~~~~~~~~~~~~~~~~~~~~~
SQLAlchemy async engine + ORM models for metrics, decisions, and episodes.
Uses TimescaleDB when available, falls back to standard Postgres.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import AsyncGenerator

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer,
    String, Text, text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


# ── ORM Models ────────────────────────────────────────────────────────────────

class MetricRecord(Base):
    __tablename__ = "metrics"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    time       = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    source     = Column(String(64),  nullable=False)
    host       = Column(String(128), nullable=False)
    name       = Column(String(128), nullable=False)
    value      = Column(Float,       nullable=False)
    unit       = Column(String(32),  default="")
    tags_json  = Column(Text,        default="{}")

    @property
    def tags(self) -> dict:
        return json.loads(self.tags_json or "{}")


class DecisionRecord(Base):
    __tablename__ = "decisions"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    decision_id    = Column(String(64), nullable=False, unique=True, index=True)
    time           = Column(DateTime,   default=datetime.utcnow, nullable=False, index=True)
    action_type    = Column(String(32), nullable=False)
    target         = Column(String(256),nullable=False)
    confidence     = Column(Float,      nullable=False)
    confidence_tier= Column(String(16), nullable=False)
    anomaly_score  = Column(Float,      default=0.0)
    reasoning      = Column(Text,       default="")
    strategy_name  = Column(String(64), default="")
    params_json    = Column(Text,       default="{}")
    status         = Column(String(16), default="pending")   # pending | approved | rejected | executed
    approved_by    = Column(String(64), default="auto")
    environment    = Column(String(64), default="default")

    @property
    def params(self) -> dict:
        return json.loads(self.params_json or "{}")


class EpisodeRecord(Base):
    __tablename__ = "episodes"

    id              = Column(Integer,    primary_key=True, autoincrement=True)
    episode_id      = Column(String(64), nullable=False, unique=True, index=True)
    time            = Column(DateTime,   default=datetime.utcnow, nullable=False, index=True)
    decision_id     = Column(String(64), nullable=False, index=True)
    action_type     = Column(String(32), nullable=False)
    target          = Column(String(256),nullable=False)
    confidence      = Column(Float,      nullable=False)
    anomaly_score   = Column(Float,      default=0.0)
    outcome_status  = Column(String(16), nullable=False)
    outcome_message = Column(Text,       default="")
    duration_ms     = Column(Integer,    default=0)
    state_json      = Column(Text,       default="{}")
    environment     = Column(String(64), default="default")
    was_successful  = Column(Boolean,    default=False)

    @property
    def state(self) -> dict:
        return json.loads(self.state_json or "{}")


# ── Helpers ───────────────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def create_tables() -> None:
    """Create all tables. For production, use Alembic migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Enable TimescaleDB hypertable if available
        for table in ("metrics", "decisions", "episodes"):
            try:
                await conn.execute(text(
                    f"SELECT create_hypertable('{table}', 'time', if_not_exists => TRUE)"
                ))
            except Exception:
                pass  # TimescaleDB not available — standard Postgres is fine
