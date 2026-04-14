# tests/test_learning_backtester.py
import pytest
from knowledge.learning_backtester import (
    grade_result, split_holdout, categorize_return,
)


def test_grade_bullish_positive_alpha():
    assert grade_result("bullish", excess_return=5.0) == "hit"


def test_grade_bullish_negative_alpha():
    assert grade_result("bullish", excess_return=-3.0) == "miss"


def test_grade_bearish_negative_alpha():
    assert grade_result("bearish", excess_return=-4.0) == "hit"


def test_grade_bearish_positive_alpha():
    assert grade_result("bearish", excess_return=2.0) == "miss"


def test_grade_neutral_within_threshold():
    assert grade_result("neutral", excess_return=1.5) == "hit"


def test_grade_neutral_outside_threshold():
    assert grade_result("neutral", excess_return=5.0) == "miss"


def test_split_holdout_ratio():
    items = list(range(100))
    train, holdout = split_holdout(items, ratio=0.30)
    assert len(holdout) == 30
    assert len(train) == 70
    assert set(train + holdout) == set(items)


def test_split_holdout_small_list():
    items = list(range(3))
    train, holdout = split_holdout(items, ratio=0.30)
    assert len(holdout) >= 1
    assert len(train) + len(holdout) == 3


def test_categorize_return():
    assert categorize_return(15.0) == "big_rise"
    assert categorize_return(5.0) == "rise"
    assert categorize_return(0.5) == "flat"
    assert categorize_return(-5.0) == "fall"
    assert categorize_return(-12.0) == "big_fall"
