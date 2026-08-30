from __future__ import annotations

import pytest

from app.baseline import BaselineTracker
from app.clients import DockerOpsClient, RunResult
from app.config import Settings
from app.deps import Deps
from app.incidents import IncidentStore
from app.llm import LlmClient


class StubProm:
    def __init__(self, values: dict[str, float] | None = None):
        self.values = values or {}

    def set(self, **kw):
        self.values.update(kw)

    async def instant(self, query: str):
        if "status=~\"5..\"" in query or "status=~'5..'" in query:
            return self.values.get("error_rate", 0.0)
        if "histogram_quantile" in query:
            return self.values.get("p95_seconds", 0.0)
        if "http_requests_total" in query:
            return self.values.get("throughput_rps", 0.0)
        return None


class StubJaeger:
    def __init__(self, summary: dict | None = None):
        self.summary = summary or {
            "trace_count": 5, "db_fraction": 0.1,
            "by_operation_ms": {}, "avg_trace_ms": 10.0,
        }

    async def span_breakdown(self, service="api", lookback="5m", limit=20):
        return self.summary


class StubApi:
    def __init__(self):
        self.calls: list[tuple[str, bool]] = []
        self.state = {"fault": {}, "remediation": {}, "replica": "api-1"}

    async def set_remediation(self, action: str, enabled: bool = True):
        self.calls.append((action, enabled))
        return {"remediation": {action: enabled}}

    async def set_fault(self, payload: dict):
        return {"fault": payload}

    async def get_state(self):
        return self.state


class StubDocker:
    def __init__(self, replicas: int = 2):
        self.replicas = replicas
        self.calls: list[str] = []

    async def replica_count(self):
        return self.replicas

    async def scale(self, replicas: int):
        self.calls.append(f"scale={replicas}")
        self.replicas = replicas
        return RunResult(0, "", "")

    async def restart(self):
        self.calls.append("restart")
        return RunResult(0, "", "")


def make_settings(**overrides) -> Settings:
    base = dict(
        anthropic_api_key="",
        verify_cooldown_seconds=0.0,
        verify_max_retries=1,
        poll_interval_seconds=0.01,
        compose_file="/tmp/does-not-matter.yml",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def stub_deps(settings) -> Deps:
    return Deps(
        settings=settings,
        prom=StubProm(),
        jaeger=StubJaeger(),
        api=StubApi(),
        docker=StubDocker(),
        baseline=BaselineTracker(settings.baseline_alpha),
        llm=LlmClient("", settings.anthropic_model),
        store=IncidentStore(),
    )


@pytest.fixture
def fake_runner():
    """Records compose argv; returns a queue-driven RunResult."""
    calls: list[list[str]] = []
    queue: list[RunResult] = []

    async def _run(argv: list[str]) -> RunResult:
        calls.append(argv)
        return queue.pop(0) if queue else RunResult(0, "", "")

    _run.calls = calls
    _run.queue = queue
    return _run


@pytest.fixture
def docker_client(fake_runner):
    return DockerOpsClient(
        project="sre-demo",
        compose_file="/workspace/docker-compose.yml",
        service="api",
        runner=fake_runner,
    )
