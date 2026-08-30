from __future__ import annotations

from app.nodes.detect import (
    ERROR_SPIKE,
    HEALTHY,
    LATENCY_SPIKE,
    SATURATION,
    classify,
)

BASELINE = {"throughput_rps": 20.0, "error_rate": 0.0, "p95_seconds": 0.05}


def test_healthy_when_signals_track_baseline(settings):
    signals = {"throughput_rps": 21.0, "error_rate": 0.001, "p95_seconds": 0.055}
    anomaly, _ = classify(signals, BASELINE, settings)
    assert anomaly == HEALTHY


def test_error_spike_when_only_errors_elevated(settings):
    signals = {"throughput_rps": 20.0, "error_rate": 0.30, "p95_seconds": 0.05}
    anomaly, notes = classify(signals, BASELINE, settings)
    assert anomaly == ERROR_SPIKE
    assert any("err=0.300" in n for n in notes)


def test_latency_spike_when_only_p95_elevated(settings):
    signals = {"throughput_rps": 20.0, "error_rate": 0.0, "p95_seconds": 0.9}
    anomaly, _ = classify(signals, BASELINE, settings)
    assert anomaly == LATENCY_SPIKE


def test_saturation_when_throughput_p95_and_errors_all_up(settings):
    signals = {"throughput_rps": 120.0, "error_rate": 0.12, "p95_seconds": 0.6}
    anomaly, _ = classify(signals, BASELINE, settings)
    assert anomaly == SATURATION


def test_idle_system_is_healthy_even_with_ratio_noise(settings):
    # throughput below min_throughput_rps -> treated as idle
    signals = {"throughput_rps": 0.2, "error_rate": 0.0, "p95_seconds": 2.0}
    anomaly, _ = classify(signals, BASELINE, settings)
    assert anomaly == HEALTHY


def test_no_baseline_only_error_threshold_can_fire(settings):
    anomaly, _ = classify(
        {"throughput_rps": 10.0, "error_rate": 0.0, "p95_seconds": 5.0}, {}, settings
    )
    assert anomaly == HEALTHY
    anomaly2, _ = classify(
        {"throughput_rps": 10.0, "error_rate": 0.5, "p95_seconds": 0.05}, {}, settings
    )
    assert anomaly2 == ERROR_SPIKE
