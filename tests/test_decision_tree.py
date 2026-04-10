"""
Tests for services/decision_tree.py
"""

import pytest
from services.decision_tree import (
    load_tree,
    reload_tree,
    compute_weighted,
    apply_corrections,
    format_tree_for_prompt,
    record_tree_path,
)


def test_load_decision_tree():
    """Verify weights load correctly from decision_tree.json."""
    config = reload_tree()
    weights = config["weights"]
    assert weights["基本面"] == 0.10
    assert weights["预期差"] == 0.40
    assert weights["资金面"] == 0.30
    assert weights["技术面"] == 0.20
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_compute_weighted_score():
    """scores {基本面:60, 预期差:80, 资金面:70, 技术面:65} should give 72.0."""
    scores = {"基本面": 60, "预期差": 80, "资金面": 70, "技术面": 65}
    weights = {"基本面": 0.10, "预期差": 0.40, "资金面": 0.30, "技术面": 0.20}
    result = compute_weighted(scores, weights)
    # 60*0.10 + 80*0.40 + 70*0.30 + 65*0.20 = 6 + 32 + 21 + 13 = 72.0
    assert result == 72.0


def test_apply_corrections_resonance():
    """预期差>=75 and 资金面>=70 → +3 bonus."""
    scores = {"基本面": 60, "预期差": 80, "资金面": 70, "技术面": 65}
    result = apply_corrections(scores, {})
    assert "catalyst_capital_resonance" in result["_flags"]
    # composite = 60*0.10 + 80*0.40 + 70*0.30 + 65*0.20 = 6 + 32 + 21 + 13 = 72.0 + 3 = 75.0
    assert result["_final"] == pytest.approx(75.0, abs=0.1)


def test_apply_corrections_divergence():
    """预期差>=75 and 资金面<=45 → -5 penalty."""
    scores = {"基本面": 60, "预期差": 80, "资金面": 40, "技术面": 65}
    result = apply_corrections(scores, {})
    assert "catalyst_capital_divergence" in result["_flags"]
    # composite = 60*0.10 + 80*0.40 + 40*0.30 + 65*0.20 = 6 + 32 + 12 + 13 = 63.0 - 5 = 58.0
    assert result["_final"] == pytest.approx(58.0, abs=0.1)


def test_apply_corrections_bucket_cap():
    """Any dim <=30 → cap composite at 60."""
    scores = {"基本面": 60, "预期差": 80, "资金面": 30, "技术面": 65}
    result = apply_corrections(scores, {})
    assert "bucket_effect" in result["_flags"]
    assert result["_final"] <= 60


def test_apply_corrections_fundamental_breaker():
    """基本面<=25 → cap composite at 30 (overrides bucket)."""
    scores = {"基本面": 20, "预期差": 80, "资金面": 30, "技术面": 65}
    result = apply_corrections(scores, {})
    assert "fundamental_circuit_breaker" in result["_flags"]
    assert "bucket_effect" not in result["_flags"]
    assert result["_final"] <= 30


def test_apply_premortem_cap():
    """high_prob_fatal>=1 → cap composite at 70."""
    scores = {"基本面": 70, "预期差": 85, "资金面": 80, "技术面": 75}
    result = apply_corrections(scores, {}, high_prob_fatal_count=1)
    assert "premortem_cap" in result["_flags"]
    assert result["_final"] <= 70


def test_format_tree_for_prompt():
    """Output contains '预期差', 'Q1', '催化'."""
    config = reload_tree()
    output = format_tree_for_prompt(config["trees"])
    assert "预期差" in output
    assert "Q1" in output
    assert "催化" in output


def test_record_tree_path():
    """Verify formatting of traversal path."""
    path = record_tree_path("预期差", ["是", "A类", "30天内", "未定价", "单季超预期"], 75)
    assert path == "预期差: 是→A类→30天内→未定价→单季超预期→75分"
    assert "预期差" in path
    assert "75分" in path
