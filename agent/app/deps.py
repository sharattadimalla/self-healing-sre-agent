"""Dependency container passed to the graph node factories."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.baseline import BaselineTracker
from app.clients import ApiAdminClient, DockerOpsClient, JaegerClient, PrometheusClient
from app.config import Settings
from app.incidents import IncidentStore
from app.llm import LlmClient

logger = logging.getLogger("app.deps")


async def slack_notify(webhook_url: str, text: str) -> None:
    if not webhook_url:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(webhook_url, json={"text": text})
    except httpx.HTTPError as exc:  # pragma: no cover - best effort
        logger.warning("slack notify failed: %s", exc)


@dataclass
class Deps:
    settings: Settings
    prom: PrometheusClient
    jaeger: JaegerClient
    api: ApiAdminClient
    docker: DockerOpsClient
    baseline: BaselineTracker
    llm: LlmClient
    store: IncidentStore

    async def alert(self, text: str) -> None:
        await slack_notify(self.settings.slack_webhook_url, text)

    @classmethod
    def build(cls, settings: Settings, *, store: IncidentStore | None = None) -> "Deps":
        return cls(
            settings=settings,
            prom=PrometheusClient(settings.prometheus_url),
            jaeger=JaegerClient(settings.jaeger_url),
            api=ApiAdminClient(settings.api_base_url, settings.admin_token),
            docker=DockerOpsClient(
                settings.compose_project, settings.compose_file, settings.api_service
            ),
            baseline=BaselineTracker(settings.baseline_alpha),
            llm=LlmClient(settings.anthropic_api_key, settings.anthropic_model),
            store=store or IncidentStore(),
        )
