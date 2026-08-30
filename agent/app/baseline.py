"""Rolling baseline for the RED signals, held in agent memory.

An EWMA per signal, updated only while the system is ``HEALTHY`` so a fault does
not poison the reference the next anomaly is compared against.
"""
from __future__ import annotations

import threading


class BaselineTracker:
    def __init__(self, alpha: float = 0.2) -> None:
        self._alpha = alpha
        self._lock = threading.Lock()
        self._values: dict[str, float] = {}
        self._samples = 0

    @property
    def samples(self) -> int:
        return self._samples

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self._values)

    def ready(self) -> bool:
        return self._samples >= 3

    def update(self, signals: dict[str, float]) -> dict[str, float]:
        with self._lock:
            for key, value in signals.items():
                if value is None:
                    continue
                if key not in self._values:
                    self._values[key] = value
                else:
                    self._values[key] = (
                        self._alpha * value + (1 - self._alpha) * self._values[key]
                    )
            self._samples += 1
            return dict(self._values)

    def seed(self, signals: dict[str, float]) -> None:
        with self._lock:
            self._values = {k: v for k, v in signals.items() if v is not None}
            self._samples = max(self._samples, 3)
