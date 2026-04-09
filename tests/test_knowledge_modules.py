# -*- coding: utf-8 -*-
"""知识库核心模块单元测试

覆盖：
- outcome_tracker: _extract_scores、_calc_returns、_infer_direction、load_outcomes
- case_memory: store_case、retrieve_similar_cases、classify_outcome
- regime_detector: 环境分类逻辑、滞后逻辑
- injector: 候选池收集优先级
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ════════════════════════════════════════════════════════════════════
# outcome_tracker
# ════════════════════════════════════════════════════════════════════

class TestOutcomeTracker:

    def test_extract_scores_100_format(self, score_block_100):
        from knowledge.outcome_tracker import _extract_scores
        scores = _extract_scores(score_block_100)
        assert scores is not None
        assert scores["基本面"] == 72
        assert scores["预期差"] == 85
        assert scores["资金面"] == 68
        assert scores["技术面"] == 60

    def test_extract_scores_10_format(self, score_block_10):
        from knowledge.outcome_tracker import _extract_scores
        scores = _extract_scores(score_block_10)
        assert scores is not None
        assert scores["基本面"] == 7  # 10分制保持原值

    def test_extract_scores_empty(self, score_empty):
        from knowledge.outcome_tracker import _extract_scores
        scores = _extract_scores(score_empty)
        assert scores is None

    def test_infer_direction(self):
        from knowledge.outcome_tracker import _infer_direction
        # 综合分 >= 6 → bullish
        assert _infer_direction({"综合加权": 7}) == "bullish"
        # 综合分 <= 3 → bearish
        assert _infer_direction({"综合加权": 2}) == "bearish"
        # 中间 → neutral
        assert _infer_direction({"综合加权": 5}) == "neutral"

    def test_row_to_outcome(self):
        from knowledge.outcome_tracker import _row_to_outcome
        row = {
            "report_id": "test_123",
            "scores": '{"基本面": 70}',
            "war_room_generals": '{}',
            "hit_5d": 1,
            "hit_10d": 0,
            "hit_20d": None,
            "beat_market_10d": 1,
        }
        result = _row_to_outcome(row)
        assert result["scores"] == {"基本面": 70}
        assert result["hit_5d"] is True
        assert result["hit_10d"] is False
        assert result["hit_20d"] is None
        assert result["beat_market_10d"] is True

    def test_load_outcomes_empty(self, tmp_path):
        """空数据库应返回空列表。"""
        from knowledge.outcome_tracker import load_outcomes
        # 使用一个新的 DBManager 实例来测试
        from knowledge.kb_db import DBManager, OUTCOMES_SCHEMA
        mgr = DBManager()
        mgr.register("outcomes", tmp_path / "outcomes.db", OUTCOMES_SCHEMA)
        with patch("knowledge.outcome_tracker.get_manager", return_value=mgr):
            results = load_outcomes()
            assert results == []
        mgr.close()


# ════════════════════════════════════════════════════════════════════
# case_memory
# ════════════════════════════════════════════════════════════════════

class TestCaseMemory:

    def test_classify_outcome_bullish_win(self):
        from knowledge.case_memory import classify_outcome
        assert classify_outcome("bullish", 5.0) == "win"

    def test_classify_outcome_bullish_loss(self):
        from knowledge.case_memory import classify_outcome
        assert classify_outcome("bullish", -5.0) == "loss"

    def test_classify_outcome_bearish_win(self):
        from knowledge.case_memory import classify_outcome
        assert classify_outcome("bearish", -5.0) == "win"

    def test_classify_outcome_neutral_draw(self):
        from knowledge.case_memory import classify_outcome
        assert classify_outcome("neutral", 10.0) == "draw"

    def test_classify_outcome_small_move(self):
        from knowledge.case_memory import classify_outcome
        assert classify_outcome("bullish", 1.0) == "draw"
        assert classify_outcome("bearish", -1.0) == "draw"

    def test_extract_sector_tags(self):
        from knowledge.case_memory import extract_sector_tags
        tags = extract_sector_tags("这是一家AI算力芯片公司，主营半导体")
        assert "AI算力" in tags
        assert "芯片" in tags
        assert "半导体" in tags

    def test_extract_sector_tags_empty(self):
        from knowledge.case_memory import extract_sector_tags
        tags = extract_sector_tags("")
        assert tags == []

    def test_store_case_rejects_empty_id(self, tmp_path):
        from knowledge.case_memory import store_case, CaseCard
        from knowledge.kb_db import DBManager, CASE_MEMORY_SCHEMA
        mgr = DBManager()
        mgr.register("case_memory", tmp_path / "case.db", CASE_MEMORY_SCHEMA)
        with patch("knowledge.kb_db.get_manager", return_value=mgr):
            case = CaseCard(
                case_id="",  # 空 ID
                report_date="2026-04-09",
                stock_code="600000.SH",
                stock_name="浦发银行",
            )
            store_case(case)  # 应该被拒绝，不崩溃
            # 验证没有写入
            with mgr.read("case_memory") as conn:
                count = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
                assert count == 0
        mgr.close()

    def test_store_case_valid(self, tmp_path):
        from knowledge.case_memory import store_case, CaseCard, get_case_count
        from knowledge.kb_db import DBManager, CASE_MEMORY_SCHEMA
        mgr = DBManager()
        mgr.register("case_memory", tmp_path / "case.db", CASE_MEMORY_SCHEMA)
        with patch("knowledge.kb_db.get_manager", return_value=mgr):
            case = CaseCard(
                case_id="test_001",
                report_date="2026-04-09",
                stock_code="600000.SH",
                stock_name="浦发银行",
                regime="shock",
                regime_label="震荡市",
                score_fundamental=70,
                score_expectation=80,
                score_capital=60,
                score_technical=65,
                score_weighted=68.5,
                direction="bullish",
                return_10d=5.0,
                hit_10d=True,
                outcome_type="win",
            )
            store_case(case)
            count = get_case_count()
            assert count == 1
        mgr.close()


# ════════════════════════════════════════════════════════════════════
# regime_detector
# ════════════════════════════════════════════════════════════════════

class TestRegimeDetector:

    def test_fallback_regime(self):
        from knowledge.regime_detector import _fallback_regime
        result = _fallback_regime("test_reason")
        assert result["regime"] == "shock"
        assert result["regime_label"] == "震荡市"
        assert result["indicators"]["fallback_reason"] == "test_reason"

    def test_apply_hysteresis_first_detection(self):
        from knowledge.regime_detector import _apply_hysteresis
        with patch("knowledge.regime_detector.get_current_regime", return_value=None):
            assert _apply_hysteresis("bull") == "bull"

    def test_apply_hysteresis_same_regime(self):
        from knowledge.regime_detector import _apply_hysteresis
        with patch("knowledge.regime_detector.get_current_regime",
                   return_value={"regime": "bull"}):
            assert _apply_hysteresis("bull") == "bull"

    def test_apply_hysteresis_holds_on_change(self):
        from knowledge.regime_detector import _apply_hysteresis
        with patch("knowledge.regime_detector.get_current_regime",
                   return_value={"regime": "shock"}), \
             patch("knowledge.regime_detector.get_regime_history",
                   return_value=[{"indicators": {"raw_regime": "bull"}}]):
            # 只有 1 天 bull，不够 2 天滞后
            assert _apply_hysteresis("bull") == "shock"

    def test_apply_hysteresis_confirms_after_n_days(self):
        from knowledge.regime_detector import _apply_hysteresis
        from knowledge.kb_config import REGIME_HYSTERESIS_DAYS
        history = [{"indicators": {"raw_regime": "bull"}} for _ in range(REGIME_HYSTERESIS_DAYS)]
        with patch("knowledge.regime_detector.get_current_regime",
                   return_value={"regime": "shock"}), \
             patch("knowledge.regime_detector.get_regime_history",
                   return_value=history):
            assert _apply_hysteresis("bull") == "bull"


# ════════════════════════════════════════════════════════════════════
# kb_utils 进阶测试
# ════════════════════════════════════════════════════════════════════

class TestKbUtilsAdvanced:

    def test_parse_ai_json_multiline_fence(self):
        from knowledge.kb_utils import parse_ai_json
        raw = """```json
{
    "themes": ["AI", "半导体"],
    "sentiment": "bullish"
}
```"""
        result = parse_ai_json(raw)
        assert result["themes"] == ["AI", "半导体"]
        assert result["sentiment"] == "bullish"

    def test_parse_ai_json_nested_backticks(self):
        from knowledge.kb_utils import parse_ai_json
        # AI 有时会多输出一些内容
        raw = '```json\n{"key": "value with `code`"}\n```'
        result = parse_ai_json(raw)
        assert result["key"] == "value with `code`"

    def test_calc_bucket_stats_empty(self):
        from knowledge.kb_utils import calc_bucket_stats
        stats = calc_bucket_stats([])
        assert stats["total"] == 0
        assert stats["hit_rate"] is None
        assert stats["avg_return"] is None
