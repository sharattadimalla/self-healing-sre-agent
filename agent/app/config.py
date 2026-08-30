"""Runtime configuration for the agent, sourced from the environment."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")

    # --- upstream services ---
    prometheus_url: str = "http://prometheus:9090"
    jaeger_url: str = "http://jaeger:16686"
    # Admin calls go through Traefik so they fan out across replicas.
    api_base_url: str = "http://traefik:80"
    admin_token: str = "dev-admin-token"

    # --- docker compose remediation ---
    compose_project: str = "sre-demo"
    compose_file: str = "/workspace/docker-compose.yml"
    api_service: str = "api"
    min_replicas: int = 2
    max_replicas: int = 6
    scale_step: int = 2

    # --- control loop ---
    poll_interval_seconds: float = 10.0
    # A scale-out needs replica warmup + Prometheus re-discovery + the 1m rate()
    # window to settle, so give verify a generous cooldown.
    verify_cooldown_seconds: float = 55.0
    verify_max_retries: int = 1
    baseline_alpha: float = 0.25  # EWMA weight for new healthy samples

    # --- detection thresholds ---
    throughput_ratio_high: float = 2.0   # vs. baseline
    p95_ratio_high: float = 1.5          # vs. baseline
    error_rate_abs: float = 0.05         # absolute 5xx fraction
    p95_abs_floor_seconds: float = 0.25  # ignore ratio noise below this p95
    min_throughput_rps: float = 5.0      # don't alarm on an idle system
    db_span_dominant_fraction: float = 0.5  # DB share of trace time -> "DB dominant"

    # --- LLM (optional) ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"

    # --- notifications (optional) ---
    slack_webhook_url: str = ""

    # --- server ---
    server_host: str = "0.0.0.0"
    server_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
