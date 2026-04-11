# tests/test_learning_optimizer.py
import json
import pytest
from knowledge.learning_optimizer import apply_proposal, check_adoption_criteria


def test_apply_weight_proposal():
    tree = {
        "weights": {"预期差": 0.30, "技术面": 0.40, "基本面": 0.20, "资金面": 0.10},
        "correction_rules": {},
        "trees": {},
    }
    proposal = {
        "id": "P1", "type": "weight",
        "target": "技术面", "proposed_value": "0.35",
    }
    new_tree, errors = apply_proposal(tree, proposal)
    assert not errors
    assert new_tree["weights"]["技术面"] == 0.35


def test_apply_weight_violates_bounds():
    tree = {
        "weights": {"预期差": 0.30, "技术面": 0.40, "基本面": 0.20, "资金面": 0.10},
        "correction_rules": {},
        "trees": {},
    }
    proposal = {
        "id": "P1", "type": "weight",
        "target": "技术面", "proposed_value": "0.60",
    }
    new_tree, errors = apply_proposal(tree, proposal)
    assert errors  # should have safety violation


def test_apply_rule_proposal():
    tree = {
        "weights": {},
        "correction_rules": {
            "fundamental_circuit_breaker": {"condition": {"基本面": {"<=": 25}}, "action": {"cap": 30}},
        },
        "trees": {},
    }
    proposal = {
        "id": "P2", "type": "rule",
        "target": "fundamental_breaker",
        "proposed_value": "20",
    }
    new_tree, errors = apply_proposal(tree, proposal)
    assert not errors


def test_check_adoption_criteria_pass():
    result = check_adoption_criteria(
        old_hit_rate=55.0, new_hit_rate=60.0,
        old_by_category={"big_rise": {"hit_rate": 70}},
        new_by_category={"big_rise": {"hit_rate": 65}},
        old_calibration=5.0, new_calibration=8.0,
    )
    assert result["adopted"]


def test_check_adoption_criteria_insufficient_improvement():
    result = check_adoption_criteria(
        old_hit_rate=55.0, new_hit_rate=56.0,
        old_by_category={}, new_by_category={},
        old_calibration=5.0, new_calibration=6.0,
    )
    assert not result["adopted"]


def test_check_adoption_criteria_cliff_drop():
    result = check_adoption_criteria(
        old_hit_rate=55.0, new_hit_rate=62.0,
        old_by_category={"big_rise": {"hit_rate": 70}},
        new_by_category={"big_rise": {"hit_rate": 50}},  # -20% drop
        old_calibration=5.0, new_calibration=8.0,
    )
    assert not result["adopted"]
    assert "断崖" in result["reason"]
