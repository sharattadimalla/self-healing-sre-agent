"""Runtime configuration, sourced from the environment."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # Storage
    database_url: str = "postgresql+asyncpg://orders:orders@db:5432/orders"
    db_pool_size: int = 5

    # Admin control plane
    admin_token: str = "dev-admin-token"

    # Shared fault/remediation state (a file so it survives replica churn)
    state_path: str = "/state/state.json"

    # Telemetry (best-effort: unset endpoint disables tracing export)
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "api"

    # Remediation tunables
    rate_limit_rps: float = 20.0
    rate_limit_burst: int = 40
    cache_ttl_seconds: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
