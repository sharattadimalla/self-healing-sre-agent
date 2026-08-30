"""In-memory incident store shared by the poll loop and the approval API."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

OPEN = "OPEN"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
RESOLVED = "RESOLVED"
ESCALATED = "ESCALATED"

_OPEN_STATES = {OPEN}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IncidentStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_id: dict[str, dict] = {}
        self._by_key: dict[str, str] = {}
        self._seq = 0

    def get_or_create(self, key: str, **fields) -> dict:
        """Idempotent create keyed by ``key`` (e.g. the graph thread id).

        The ``approve`` node re-runs from its top after an ``interrupt()`` resume,
        so incident creation must not fire twice for the same run.
        """
        with self._lock:
            existing_id = self._by_key.get(key)
        if existing_id is not None:
            return self.get(existing_id)
        created = self.create(**fields)
        with self._lock:
            self._by_key[key] = created["id"]
        return created

    def create(self, *, anomaly: str, signals: dict, baseline: dict,
               trace_summary: dict, diagnosis: dict, recommendation: dict) -> dict:
        with self._lock:
            self._seq += 1
            incident_id = f"INC-{self._seq:04d}"
            incident = {
                "id": incident_id,
                "created_at": _now(),
                "status": OPEN,
                "anomaly": anomaly,
                "signals": signals,
                "baseline": baseline,
                "trace_summary": trace_summary,
                "diagnosis": diagnosis,
                "diagnosis_source": None,
                "recommendation": recommendation,
                "recommendation_source": None,
                "decision": None,          # None | True (approve) | False (reject)
                "decided_at": None,
                "decided_by": None,
                "action_result": None,
                "verify_result": None,
                "closed_at": None,
            }
            self._by_id[incident_id] = incident
            return dict(incident)

    def get(self, incident_id: str) -> Optional[dict]:
        with self._lock:
            found = self._by_id.get(incident_id)
            return dict(found) if found else None

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(v) for v in sorted(
                self._by_id.values(), key=lambda i: i["id"], reverse=True
            )]

    def open_incidents(self) -> list[dict]:
        with self._lock:
            return [dict(v) for v in self._by_id.values() if v["status"] in _OPEN_STATES]

    def record_decision(self, incident_id: str, approved: bool,
                        by: str = "web-ui") -> Optional[dict]:
        with self._lock:
            incident = self._by_id.get(incident_id)
            if incident is None or incident["status"] != OPEN:
                return None
            incident["decision"] = approved
            incident["decided_at"] = _now()
            incident["decided_by"] = by
            incident["status"] = APPROVED if approved else REJECTED
            return dict(incident)

    def update(self, incident_id: str, **fields) -> Optional[dict]:
        with self._lock:
            incident = self._by_id.get(incident_id)
            if incident is None:
                return None
            incident.update(fields)
            if fields.get("status") in (RESOLVED, REJECTED, ESCALATED):
                incident["closed_at"] = _now()
            return dict(incident)
