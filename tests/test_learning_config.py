# tests/test_learning_config.py
import pytest
from knowledge.learning_config import (
    validate_weights, validate_rule_thresholds, validate_tree_structure,
    validate_prompt_patch, SAFETY_BOUNDS, STAGING_DIR, LEARNING_LOG_DIR,
)


def test_validate_weights_valid():
    w = {"预期差": 0.35, "技术面": 0.35, "基本面": 0.20, "资金面": 0.10}
    assert validate_weights(w) == []


def test_validate_weights_exceeds_max():
    w = {"预期差": 0.55, "技术面": 0.25, "基本面": 0.15, "资金面": 0.05}
    errors = validate_weights(w)
    assert any("50%" in e for e in errors)


def test_validate_weights_below_min():
    w = {"预期差": 0.50, "技术面": 0.45, "基本面": 0.04, "资金面": 0.01}
    errors = validate_weights(w)
    assert any("5%" in e for e in errors)


def test_validate_weights_sum_not_100():
    w = {"预期差": 0.30, "技术面": 0.30, "基本面": 0.20, "资金面": 0.10}
    errors = validate_weights(w)
    assert any("100%" in e or "总和" in e for e in errors)


def test_validate_rule_thresholds_valid():
    rules = {"fundamental_breaker": 25, "bucket_cap": 30, "premortem_cap": 70}
    assert validate_rule_thresholds(rules) == []


def test_validate_rule_thresholds_out_of_range():
    rules = {"fundamental_breaker": 5, "bucket_cap": 50, "premortem_cap": 85}
    errors = validate_rule_thresholds(rules)
    assert len(errors) == 3


def test_validate_tree_structure_too_many_questions():
    tree = {"预期差": {"questions": [f"Q{i}" for i in range(10)]}}
    errors = validate_tree_structure(tree)
    assert any("8" in e for e in errors)


def test_validate_prompt_patch_too_large():
    original = "x" * 1000
    patch = "y" * 300  # 30% > 20% limit
    errors = validate_prompt_patch(patch, original)
    assert any("20%" in e for e in errors)


def test_staging_dir_constant():
    assert "staging" in str(STAGING_DIR)


def test_learning_log_dir_constant():
    assert "learning_log" in str(LEARNING_LOG_DIR)
