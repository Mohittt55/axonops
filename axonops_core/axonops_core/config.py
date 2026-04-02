"""
axonops_core.config
~~~~~~~~~~~~~~~~~~~
All configuration via environment variables or .env file.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Engine
    app_name: str            = "AxonOps"
    environment: str         = "development"
    tick_interval: int       = 10          # seconds between collection cycles
    log_level: str           = "INFO"

    # API server
    api_host: str            = "0.0.0.0"
    api_port: int            = 8000
    api_cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Database (TimescaleDB / Postgres)
    database_url: str        = "postgresql+asyncpg://axonops:axonops@localhost:5432/axonops"
    database_url_sync: str   = "postgresql+psycopg2://axonops:axonops@localhost:5432/axonops"

    # Confidence gate thresholds
    confidence_auto: float   = 0.85   # >= this → execute silently
    confidence_notify: float = 0.60   # >= this → execute + notify

    # Anomaly detection
    anomaly_contamination: float = 0.05   # expected anomaly fraction for IsolationForest
    anomaly_window: int          = 100    # samples kept in rolling window

    # Notifications
    slack_webhook_url: str   = ""
    notify_on_auto: bool     = False   # also notify on auto-executed actions

    # Active plugins (comma-separated, loaded by name from entry points)
    active_collectors: list[str] = ["psutil"]
    active_actions:    list[str] = ["psutil"]
    active_strategy:   str       = "psutil-zscore"

    # AWS (optional)
    aws_region: str          = "us-east-1"
    aws_asg_names: list[str] = []
    aws_rds_instances: list[str] = []

    # Kubernetes (optional)
    k8s_namespace: str       = "default"
    k8s_in_cluster: bool     = False

    # Prometheus (optional)
    prometheus_targets: list[str] = []


def get_settings() -> Settings:
    return Settings()
