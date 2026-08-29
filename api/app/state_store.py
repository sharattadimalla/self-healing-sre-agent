"""Single source of truth for runtime-mutable behavior (faults + remediation).

State is one JSON document on disk (``STATE_PATH``). Every replica reads it, so a
fault set on one ``api`` container is observed fleet-wide. Writes are atomic
(temp file + ``os.replace``); last write wins, which is acceptable for a demo
control plane.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Literal

from pydantic import BaseModel, Field


class FaultState(BaseModel):
    error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    latency_ms: int = Field(default=0, ge=0)
    latency_target: Literal["api", "db"] = "api"


class RemediationState(BaseModel):
    feature_flag_enabled: bool = True
    cache_enabled: bool = False
    rate_limit_enabled: bool = False
    pool_generation: int = 0


class AppState(BaseModel):
    fault: FaultState = Field(default_factory=FaultState)
    remediation: RemediationState = Field(default_factory=RemediationState)


class StateStore:
    """Read/modify the shared state file with a tiny mtime-based cache."""

    def __init__(self, path: str, cache_seconds: float = 0.5) -> None:
        self._path = path
        self._cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._cached: AppState | None = None
        self._checked_at = 0.0
        self._mtime = 0.0

    # --- reads -----------------------------------------------------------------
    def read(self) -> AppState:
        now = time.monotonic()
        with self._lock:
            if self._cached is not None and (now - self._checked_at) < self._cache_seconds:
                return self._cached.model_copy(deep=True)
            self._checked_at = now
            state = self._load_locked()
            self._cached = state
            return state.model_copy(deep=True)

    def _load_locked(self) -> AppState:
        try:
            mtime = os.path.getmtime(self._path)
            if self._cached is not None and mtime == self._mtime:
                return self._cached
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            state = AppState.model_validate(raw)
            self._mtime = mtime
            return state
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            # Missing or corrupt -> safe defaults, and rewrite so it self-heals.
            state = AppState()
            self._write_locked(state)
            return state

    # --- writes --------------------------------------------------------------
    def _write_locked(self, state: AppState) -> None:
        directory = os.path.dirname(self._path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state.model_dump(), fh)
            os.replace(tmp, self._path)
            self._mtime = os.path.getmtime(self._path)
            self._cached = state
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def update_fault(self, **changes) -> FaultState:
        with self._lock:
            state = self._load_locked()
            state.fault = state.fault.model_copy(update=changes)
            self._write_locked(state)
            self._checked_at = time.monotonic()
            return state.fault.model_copy()

    def replace_fault(self, fault: FaultState) -> FaultState:
        with self._lock:
            state = self._load_locked()
            state.fault = fault.model_copy()
            self._write_locked(state)
            self._checked_at = time.monotonic()
            return state.fault.model_copy()

    def update_remediation(self, **changes) -> RemediationState:
        with self._lock:
            state = self._load_locked()
            state.remediation = state.remediation.model_copy(update=changes)
            self._write_locked(state)
            self._checked_at = time.monotonic()
            return state.remediation.model_copy()
