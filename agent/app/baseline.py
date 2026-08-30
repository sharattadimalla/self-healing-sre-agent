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

    def is_stable(
        self, signals: dict[str, float], tol: float = 1.4, warm_floor: float = 0.0
    ) -> bool:
        """True while ``signals`` sit close to the current baseline.

        Used to *stop feeding the baseline* once throughput / latency start to
        ramp, so a slow climb into an anomaly doesn't drag the reference with it.

        While the baseline throughput is still below ``warm_floor`` the system was
        idle when the baseline started, so keep taking every sample — otherwise
        the first burst of real traffic would freeze the baseline near zero.
        """
        with self._lock:
            # Keep taking every sample until the baseline is properly warm: ~8
            # polls covers Prometheus' 1m rate window filling after load starts
            # (early samples read low purely as a measurement artifact).
            if self._samples < 8:
                return True
            if self._values.get("throughput_rps", 0.0) < warm_floor:
                return True  # baseline still reflects an idle system
            for key in ("throughput_rps", "p95_seconds"):
                base = self._values.get(key)
                cur = signals.get(key)
                if not base or cur is None:
                    continue
                if cur > base * tol or cur < base / tol:
                    return False
            return True

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
