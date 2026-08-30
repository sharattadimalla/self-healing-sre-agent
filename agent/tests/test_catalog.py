from __future__ import annotations

import pytest

from app.catalog import CATALOG, candidates_for
from app.nodes.recommend import _fallback_recommendation


def test_every_catalog_action_has_a_valid_executor():
    for action in CATALOG.values():
        assert action.executor in {"docker", "api"}
        assert action.risk in {"low", "medium", "high"}


@pytest.mark.parametrize(
    "anomaly, expected_first",
    [
        ("SATURATION", "scale_api"),
        ("ERROR_SPIKE", "disable_feature_flag"),
        ("LATENCY_SPIKE", "enable_cache"),
    ],
)
def test_candidate_selection_per_anomaly(anomaly, expected_first):
    actions = candidates_for(anomaly)
    assert actions and actions[0].id == expected_first


def test_saturation_pairs_scale_with_rate_limit():
    ids = [a.id for a in candidates_for("SATURATION")]
    assert ids == ["scale_api", "enable_rate_limit"]


def test_latency_recommends_cache():
    ids = [a.id for a in candidates_for("LATENCY_SPIKE")]
    assert ids == ["enable_cache"]


def test_fallback_recommendation_shape():
    rec = _fallback_recommendation("ERROR_SPIKE")
    assert [a["id"] for a in rec["actions"]] == ["disable_feature_flag"]
    assert rec["risk"] in {"low", "medium", "high"}
    assert rec["expected_effect"]


def test_unknown_anomaly_has_no_candidates():
    assert candidates_for("MYSTERY") == []
