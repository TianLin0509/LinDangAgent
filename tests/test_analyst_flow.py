"""Integration tests for the new 2-round analyst flow.

These tests verify the wiring between components without making real API calls.
"""
import pytest


def test_decision_tree_loads_and_formats():
    """Verify decision tree loads from JSON and formats for prompt."""
    from services.decision_tree import load_tree, format_tree_for_prompt
    tree = load_tree()
    text = format_tree_for_prompt(tree["trees"])
    assert "预期差" in text
    assert "资金面" in text
    assert "技术面" in text
    assert "基本面" in text
    assert "催化" in text
    assert "主力资金" in text


def test_round1_prompt_includes_tree():
    """Verify Round 1 system prompt includes decision tree."""
    from ai.prompts_analyst import build_round1_system
    from services.decision_tree import load_tree, format_tree_for_prompt
    tree = load_tree()
    tree_text = format_tree_for_prompt(tree["trees"])
    prompt = build_round1_system(tree_text)
    assert "决策树" in prompt
    assert "价值投机" in prompt
    assert "<<<SCORES>>>" in prompt


def test_round1_prompt_includes_lessons():
    """Verify lessons are injected when provided."""
    from ai.prompts_analyst import build_round1_system
    prompt = build_round1_system("fake tree", "⚠️ 本股历史：曾经翻车")
    assert "历史经验教训" in prompt
    assert "曾经翻车" in prompt


def test_round2_prompt_structure():
    """Verify Round 2 prompt has required sections."""
    from ai.prompts_analyst import ROUND2_SYSTEM, build_round2_user
    assert "致命理由" in ROUND2_SYSTEM
    assert "Pre-mortem" in ROUND2_SYSTEM
    assert "<<<SCORE_CORRECTIONS>>>" in ROUND2_SYSTEM
    user = build_round2_user("fake round 1 output")
    assert "fake round 1 output" in user


def test_score_corrections_parsing():
    """Verify Round 2 score corrections are parsed correctly."""
    from services.war_room import _apply_round2_corrections
    from services.decision_tree import load_tree

    round1_scores = {"基本面": 60, "预期差": 80, "资金面": 70, "技术面": 65, "综合加权": 73.0}
    round2_text = """
Some analysis...
<<<SCORE_CORRECTIONS>>>
基本面: +0分 | 理由：无硬伤确认
预期差: -5分 | 理由：催化可能已部分定价
资金面: -3分 | 理由：北向可能是被动调仓
技术面: +0分 | 理由：技术面判断合理
<<<END_SCORE_CORRECTIONS>>>
"""
    tree = load_tree()
    result = _apply_round2_corrections(round1_scores, round2_text, tree)
    assert result["预期差"] == 75  # 80 - 5
    assert result["资金面"] == 67  # 70 - 3
    assert result["基本面"] == 60  # unchanged
    assert result["技术面"] == 65  # unchanged


def test_fatal_count_extraction():
    """Verify HIGH_PROB_FATAL_COUNT extraction."""
    from services.war_room import _extract_fatal_count

    text1 = "blah\n<<<HIGH_PROB_FATAL_COUNT>>>\n2\n<<<END_HIGH_PROB_FATAL_COUNT>>>"
    assert _extract_fatal_count(text1) == 2

    text2 = "Pre-mortem\n路径1：xxx 概率：高\n路径2：xxx 概率：低"
    assert _extract_fatal_count(text2) == 1


def test_corrections_clamp_to_10():
    """Verify corrections are clamped to ±10."""
    from services.war_room import _apply_round2_corrections
    from services.decision_tree import load_tree

    round1_scores = {"基本面": 60, "预期差": 80, "资金面": 70, "技术面": 65, "综合加权": 73.0}
    round2_text = """
<<<SCORE_CORRECTIONS>>>
基本面: +0分 | ok
预期差: -20分 | trying to over-correct
资金面: +0分 | ok
技术面: +0分 | ok
<<<END_SCORE_CORRECTIONS>>>
"""
    tree = load_tree()
    result = _apply_round2_corrections(round1_scores, round2_text, tree)
    assert result["预期差"] == 70  # Clamped: 80 - 10, not 80 - 20


def test_new_presets_exist():
    """Verify new preset structure."""
    from services.war_room import WAR_ROOM_PRESETS
    assert "opus" in WAR_ROOM_PRESETS
    assert "analyst" in WAR_ROOM_PRESETS["opus"]
    assert "balanced" in WAR_ROOM_PRESETS
    assert WAR_ROOM_PRESETS["balanced"].get("_legacy") is True


def test_experience_roundtrip(tmp_path):
    """Verify experience add -> retrieve roundtrip."""
    from knowledge.experience_db import add_experience, retrieve_lessons

    db_path = tmp_path / "exp.json"
    db_path.write_text("[]", encoding="utf-8")

    add_experience({
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "industry": "白酒",
        "catalyst_type": ["财报超预期"],
        "pattern_tags": ["放量突破"],
        "prediction": {"score": 75, "direction": "做多"},
        "actual": {"return_20d": -3.5},
        "lesson": "白酒板块整体走弱时不要逆势做多",
        "tags": ["板块走弱"],
    }, db_path=db_path)

    result = retrieve_lessons("600519", "贵州茅台", db_path=db_path)
    assert "白酒板块" in result


def test_weights_updated_in_analysis_service():
    """Verify analysis_service weights match new spec."""
    from services.analysis_service import SCORE_WEIGHTS
    assert SCORE_WEIGHTS["预期差"] == 0.30
    assert SCORE_WEIGHTS["技术面"] == 0.40
    assert SCORE_WEIGHTS["基本面"] == 0.20
    assert SCORE_WEIGHTS["资金面"] == 0.10
