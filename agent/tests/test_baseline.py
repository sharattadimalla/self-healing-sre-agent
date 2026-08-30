from __future__ import annotations

from app.baseline import BaselineTracker


def _warm(bt: BaselineTracker, thru: float, p95: float, n: int = 5):
    for _ in range(n):
        bt.update({"throughput_rps": thru, "error_rate": 0.0, "p95_seconds": p95})


def test_ewma_converges_toward_repeated_samples():
    bt = BaselineTracker(alpha=0.5)
    _warm(bt, 100.0, 0.1, n=8)
    snap = bt.snapshot()
    assert abs(snap["throughput_rps"] - 100.0) < 1.0
    assert abs(snap["p95_seconds"] - 0.1) < 0.01


def test_is_stable_true_while_warming_up():
    bt = BaselineTracker()
    bt.update({"throughput_rps": 20.0, "p95_seconds": 0.05})
    assert bt.is_stable({"throughput_rps": 900.0, "p95_seconds": 9.0}) is True


def test_is_stable_keeps_taking_samples_until_warm():
    bt = BaselineTracker(alpha=0.5)
    _warm(bt, 30.0, 0.08, n=6)  # < 8 samples -> still warming
    assert bt.is_stable({"throughput_rps": 300.0, "p95_seconds": 9.0}) is True


def test_is_stable_flags_a_ramp_once_warm():
    bt = BaselineTracker(alpha=0.5)
    _warm(bt, 30.0, 0.08, n=10)
    assert bt.is_stable({"throughput_rps": 33.0, "p95_seconds": 0.085}) is True
    assert bt.is_stable({"throughput_rps": 300.0, "p95_seconds": 0.09}) is False
    assert bt.is_stable({"throughput_rps": 31.0, "p95_seconds": 0.4}) is False


def test_is_stable_keeps_warming_while_baseline_below_floor():
    bt = BaselineTracker(alpha=0.5)
    _warm(bt, 2.0, 0.05, n=10)  # warm, but baseline throughput still tiny
    assert bt.is_stable({"throughput_rps": 40.0, "p95_seconds": 0.05},
                        warm_floor=5.0) is True


def test_seed_marks_ready():
    bt = BaselineTracker()
    bt.seed({"throughput_rps": 50.0, "p95_seconds": 0.06})
    assert bt.ready() is True
    assert bt.snapshot()["throughput_rps"] == 50.0
