"""Thin clients for the systems the agent observes and acts on.

- ``PrometheusClient``  — instant PromQL queries for the RED signals.
- ``JaegerClient``      — recent ``service=api`` traces + a span-duration breakdown.
- ``ApiAdminClient``    — app-level remediation knobs via Traefik.
- ``DockerOpsClient``   — wraps ``docker compose`` scale / restart / ps.

All network/subprocess work is async. ``DockerOpsClient`` takes an injectable
``runner`` so the unit tests can assert on the exact compose argv.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shlex
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import httpx

logger = logging.getLogger("app.clients")


class PrometheusClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def instant(self, query: str) -> Optional[float]:
        """Run an instant query; return the first scalar value or ``None``."""
        url = f"{self._base_url}/api/v1/query"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, params={"query": query})
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:  # network / bad JSON
            logger.warning("prometheus query failed (%s): %s", query, exc)
            return None

        if payload.get("status") != "success":
            logger.warning("prometheus query not successful: %s", payload)
            return None
        result = payload.get("data", {}).get("result", [])
        if not result:
            return None
        try:
            value = float(result[0]["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        if value != value:  # NaN
            return None
        return value


class JaegerClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def recent_traces(
        self, service: str = "api", lookback: str = "5m", limit: int = 20
    ) -> list[dict]:
        url = f"{self._base_url}/api/traces"
        params = {"service": service, "lookback": lookback, "limit": str(limit)}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                return resp.json().get("data", []) or []
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("jaeger query failed: %s", exc)
            return []

    async def span_breakdown(
        self, service: str = "api", lookback: str = "5m", limit: int = 20
    ) -> dict:
        """Aggregate span self-time by a coarse category (db vs. app).

        Returns ``{"trace_count", "db_fraction", "by_operation": {...}, "avg_trace_ms"}``.
        DB spans are identified by an ``db.system`` tag or a ``SELECT``/``INSERT``/… name.
        """
        traces = await self.recent_traces(service, lookback, limit)
        return summarize_spans(traces)


_DB_KEYWORDS = ("select", "insert", "update", "delete", "commit", "connect")


def _is_db_span(span: dict) -> bool:
    tags = {t.get("key"): t.get("value") for t in span.get("tags", [])}
    if "db.system" in tags or "db.statement" in tags:
        return True
    name = (span.get("operationName") or "").strip().lower()
    return any(name.startswith(k) or f" {k} " in f" {name} " for k in _DB_KEYWORDS)


def summarize_spans(traces: list[dict]) -> dict:
    total_db_us = 0.0
    total_us = 0.0
    by_operation: dict[str, float] = {}
    trace_durations: list[float] = []

    for trace in traces:
        spans = trace.get("spans", [])
        trace_total = 0.0
        for span in spans:
            dur = float(span.get("duration", 0) or 0)  # microseconds
            trace_total += dur
            op = span.get("operationName", "unknown")
            by_operation[op] = by_operation.get(op, 0.0) + dur
            if _is_db_span(span):
                total_db_us += dur
            total_us += dur
        if spans:
            # Trace wall-clock ≈ max(end) - min(start).
            starts = [float(s.get("startTime", 0) or 0) for s in spans]
            ends = [
                float(s.get("startTime", 0) or 0) + float(s.get("duration", 0) or 0)
                for s in spans
            ]
            trace_durations.append(max(ends) - min(starts))

    db_fraction = (total_db_us / total_us) if total_us > 0 else 0.0
    top_ops = dict(
        sorted(by_operation.items(), key=lambda kv: kv[1], reverse=True)[:5]
    )
    top_ops_ms = {k: round(v / 1000.0, 2) for k, v in top_ops.items()}
    avg_trace_ms = (
        round(sum(trace_durations) / len(trace_durations) / 1000.0, 2)
        if trace_durations
        else 0.0
    )
    return {
        "trace_count": len(traces),
        "db_fraction": round(db_fraction, 3),
        "by_operation_ms": top_ops_ms,
        "avg_trace_ms": avg_trace_ms,
    }


class ApiAdminClient:
    def __init__(self, base_url: str, admin_token: str, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Admin-Token": admin_token, "Content-Type": "application/json"}
        self._timeout = timeout

    async def set_remediation(self, action: str, enabled: bool = True) -> dict:
        url = f"{self._base_url}/admin/remediation"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                url, headers=self._headers, json={"action": action, "enabled": enabled}
            )
            resp.raise_for_status()
            return resp.json()

    async def set_fault(self, payload: dict) -> dict:
        url = f"{self._base_url}/admin/fault"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, headers=self._headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def get_state(self) -> dict:
        url = f"{self._base_url}/admin/state"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.json()


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[list[str]], Awaitable[RunResult]]


async def _subprocess_runner(argv: list[str]) -> RunResult:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return RunResult(proc.returncode or 0, out.decode(), err.decode())


class DockerOpsError(RuntimeError):
    pass


class DockerOpsClient:
    """Scale / restart the ``api`` service through the compose CLI."""

    def __init__(
        self,
        project: str,
        compose_file: str,
        service: str = "api",
        runner: CommandRunner | None = None,
    ) -> None:
        self._project = project
        self._compose_file = compose_file
        self._service = service
        self._run = runner or _subprocess_runner

    def _base_argv(self) -> list[str]:
        return ["docker", "compose", "-p", self._project, "-f", self._compose_file]

    async def _exec(self, *args: str) -> RunResult:
        argv = self._base_argv() + list(args)
        logger.info("docker: %s", " ".join(shlex.quote(a) for a in argv))
        result = await self._run(argv)
        if result.returncode != 0:
            raise DockerOpsError(
                f"`{' '.join(args)}` exited {result.returncode}: {result.stderr.strip()}"
            )
        return result

    async def replica_count(self) -> int:
        result = await self._exec("ps", "--format", "json", self._service)
        count = 0
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            # `docker compose ps --format json` may emit one object per line or a
            # single JSON array — handle both.
            rows = row if isinstance(row, list) else [row]
            for r in rows:
                state = str(r.get("State", "")).lower()
                if r.get("Service") == self._service and (
                    "run" in state or "up" in state or not state
                ):
                    count += 1
        return count

    async def scale(self, replicas: int) -> RunResult:
        # --no-build: the image is always pre-built by `make up`; the agent must
        # never trigger a build against the read-only repo mount.
        return await self._exec(
            "up", "-d", "--no-recreate", "--no-build",
            "--scale", f"{self._service}={replicas}", self._service,
        )

    async def restart(self) -> RunResult:
        return await self._exec("restart", self._service)
