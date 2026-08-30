"""Anthropic client wrapper for the analyze / recommend nodes.

``claude-opus-5`` with adaptive thinking and a JSON-schema structured output.
Every method returns ``None`` when no API key is configured or the call fails, so
the callers fall back to the deterministic rule table and the demo always runs.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger("app.llm")

_DIAGNOSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "one-sentence root-cause hypothesis"},
        "root_cause": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "root_cause", "confidence", "evidence"],
    "additionalProperties": False,
}

_RECOMMENDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "action_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "ordered subset of the provided candidate action ids",
        },
        "rationale": {"type": "string"},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "expected_effect": {"type": "string"},
    },
    "required": ["action_ids", "rationale", "risk", "expected_effect"],
    "additionalProperties": False,
}


class LlmClient:
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key or ""
        self._model = model
        self._client = None
        if self._api_key:
            try:
                import anthropic

                self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
            except Exception:  # pragma: no cover - import/config guard
                logger.exception("failed to construct Anthropic client; using fallback")
                self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def _structured(self, system: str, user: str, schema: dict) -> Optional[dict]:
        if self._client is None:
            return None
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=1500,
                thinking={"type": "adaptive"},
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
            text = next(b.text for b in resp.content if b.type == "text")
            return json.loads(text)
        except Exception:  # pragma: no cover - any SDK/network failure -> fallback
            logger.exception("LLM structured call failed; falling back to rule table")
            return None

    async def diagnose(
        self,
        *,
        anomaly: str,
        signals: dict[str, Any],
        baseline: dict[str, Any],
        trace_summary: dict[str, Any],
    ) -> Optional[dict]:
        system = (
            "You are an SRE incident analyst. Given RED signals, a rolling "
            "baseline, and a Jaeger span breakdown, produce a concise root-cause "
            "hypothesis. Be specific and cite the numbers you used."
        )
        user = json.dumps(
            {
                "anomaly_class": anomaly,
                "signals": signals,
                "baseline": baseline,
                "trace_summary": trace_summary,
            },
            indent=2,
        )
        return await self._structured(system, user, _DIAGNOSIS_SCHEMA)

    async def recommend(
        self,
        *,
        anomaly: str,
        diagnosis: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> Optional[dict]:
        system = (
            "You are an SRE remediation planner. Choose an ordered subset of the "
            "provided candidate actions (by id) that best remediates the diagnosis. "
            "Prefer the smallest safe intervention. Only use ids from the candidate list."
        )
        user = json.dumps(
            {"anomaly_class": anomaly, "diagnosis": diagnosis, "candidates": candidates},
            indent=2,
        )
        return await self._structured(system, user, _RECOMMENDATION_SCHEMA)
