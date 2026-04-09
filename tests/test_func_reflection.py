"""reflection 功能性测试 — 反思生成"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest


# ══════════════════════════════════════════════════════════════════
# TestGenerateReflection
# ══════════════════════════════════════════════════════════════════

class TestGenerateReflection:
    """generate_reflection 单条反思生成。"""

    def _make_outcome(self, direction="bullish", weighted_score=7.0,
                      return_10d=5.0, hit_10d=True):
        return {
            "report_id": "rpt_test",
            "report_date": "2026-03-01",
            "stock_code": "600000.SH",
            "stock_name": "测试股",
            "scores": {"基本面": 7, "预期差": 8, "资金面": 6, "技术面": 7},
            "weighted_score": weighted_score,
            "direction": direction,
            "return_5d": 3.0,
            "return_10d": return_10d,
            "return_20d": 8.0,
            "hit_10d": hit_10d,
        }

    def test_happy_path(self, mock_ai_reflection_single):
        """mock AI 返回有效教训 → (lesson, summary)，lesson 包含维度关键词"""
        from knowledge.reflection import generate_reflection

        outcome = self._make_outcome()
        regime_info = {"regime": "bull", "regime_label": "牛市"}

        mock_client = MagicMock()
        with patch("ai.client.get_ai_client",
                   return_value=(mock_client, {"model": "test"}, None)), \
             patch("ai.client.call_ai",
                   return_value=(mock_ai_reflection_single, None)), \
             patch("knowledge.case_memory.extract_sector_tags", return_value=[]):
            lesson, summary = generate_reflection(outcome, regime_info)
            assert len(lesson) > 10
            # 教训应包含至少一个维度关键词
            dimension_kw = ["基本面", "预期差", "资金面", "技术面", "催化", "题材", "估值", "资金"]
            assert any(kw in lesson for kw in dimension_kw)

    def test_ai_failure_returns_empty(self):
        """AI 不可用 → ("", "")"""
        from knowledge.reflection import generate_reflection

        outcome = self._make_outcome()
        with patch("ai.client.get_ai_client",
                   return_value=(None, {}, "unavailable")):
            lesson, summary = generate_reflection(outcome, None)
            assert lesson == ""
            assert summary == ""

    def test_quality_check_retry(self):
        """第一次回复缺少关键词 → 重试一次"""
        from knowledge.reflection import generate_reflection

        outcome = self._make_outcome()
        regime_info = {"regime": "bull", "regime_label": "牛市"}

        # 第一次返回无维度关键词，第二次返回有
        bad_response = "这次分析做得不太好，下次要改进。"
        good_response = "我在技术面评分上高估了突破信号的可靠性。"

        call_count = {"n": 0}

        def mock_call_ai(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return bad_response, None
            return good_response, None

        mock_client = MagicMock()
        with patch("ai.client.get_ai_client",
                   return_value=(mock_client, {"model": "test"}, None)), \
             patch("ai.client.call_ai", side_effect=mock_call_ai), \
             patch("knowledge.case_memory.extract_sector_tags", return_value=[]):
            lesson, summary = generate_reflection(outcome, regime_info)
            assert call_count["n"] == 2
            assert "技术面" in lesson


# ══════════════════════════════════════════════════════════════════
# TestBatchReflect
# ══════════════════════════════════════════════════════════════════

class TestBatchReflect:
    """_batch_reflect 批量反思。"""

    def test_batch_2_outcomes(self, mock_ai_reflection_batch):
        """mock AI 返回 JSON 数组 → 2 lessons in map"""
        from knowledge.reflection import _batch_reflect

        pending = [
            (
                {"report_id": "rpt_001", "stock_name": "股A", "stock_code": "600001.SH",
                 "report_date": "2026-03-01", "scores": {"基本面": 7}, "weighted_score": 7,
                 "direction": "bullish", "return_5d": 3, "return_10d": 5, "return_20d": 8,
                 "hit_10d": True},
                {"regime": "bull", "regime_label": "牛市"},
            ),
            (
                {"report_id": "rpt_002", "stock_name": "股B", "stock_code": "600002.SH",
                 "report_date": "2026-03-02", "scores": {"资金面": 6}, "weighted_score": 6,
                 "direction": "bullish", "return_5d": -2, "return_10d": -3, "return_20d": -5,
                 "hit_10d": False},
                {"regime": "shock", "regime_label": "震荡市"},
            ),
        ]

        mock_client = MagicMock()
        with patch("ai.client.get_ai_client",
                   return_value=(mock_client, {"model": "test"}, None)), \
             patch("ai.client.call_ai",
                   return_value=(mock_ai_reflection_batch, None)), \
             patch("knowledge.kb_utils.parse_ai_json",
                   return_value=json.loads(mock_ai_reflection_batch)):
            result = _batch_reflect(pending)
            assert len(result) == 2
            assert "rpt_001" in result
            assert "rpt_002" in result

    def test_batch_parse_failure(self):
        """mock AI 返回无效 JSON → 空 map"""
        from knowledge.reflection import _batch_reflect

        pending = [
            (
                {"report_id": "rpt_x", "stock_name": "X", "stock_code": "600000.SH",
                 "report_date": "2026-03-01", "scores": {}, "weighted_score": 5,
                 "direction": "neutral", "return_5d": 0, "return_10d": 0, "return_20d": 0,
                 "hit_10d": None},
                None,
            ),
            (
                {"report_id": "rpt_y", "stock_name": "Y", "stock_code": "600001.SH",
                 "report_date": "2026-03-02", "scores": {}, "weighted_score": 5,
                 "direction": "neutral", "return_5d": 0, "return_10d": 0, "return_20d": 0,
                 "hit_10d": None},
                None,
            ),
        ]

        mock_client = MagicMock()
        with patch("ai.client.get_ai_client",
                   return_value=(mock_client, {"model": "test"}, None)), \
             patch("ai.client.call_ai",
                   return_value=("这不是JSON", None)), \
             patch("knowledge.kb_utils.parse_ai_json", return_value=None):
            result = _batch_reflect(pending)
            assert result == {}


# ══════════════════════════════════════════════════════════════════
# TestProcessPendingReflections
# ══════════════════════════════════════════════════════════════════

class TestProcessPendingReflections:
    """process_pending_reflections 端到端。"""

    def test_creates_case_cards(self, func_db_manager, reset_outcome_tracker):
        """预插入 3 条 outcomes（无对应 case）→ 处理后 get_case_count > 0"""
        from knowledge.reflection import process_pending_reflections
        from knowledge.case_memory import get_case_count

        mgr, tmp_path = func_db_manager

        # 插入 3 条 outcomes
        with mgr.write("outcomes") as conn:
            for i in range(3):
                conn.execute("""
                    INSERT INTO outcomes (
                        report_id, report_date, stock_code, stock_name, source,
                        scores, weighted_score, direction, close_at_report,
                        return_5d, return_10d, return_20d,
                        hit_5d, hit_10d, hit_20d,
                        actual_trade_days, evaluated_at,
                        return_benchmark_10d, beat_market_10d,
                        war_room_divergence, war_room_generals
                    ) VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?, ?,?, ?,?)
                """, (
                    f"rpt_refl_{i}", "2026-03-01", f"60000{i}.SH",
                    f"反思测试{i}", "report",
                    json.dumps({"基本面": 7, "预期差": 6, "资金面": 5, "技术面": 8}),
                    7.0, "bullish", 15.0,
                    3.0, 5.0, 8.0,
                    1, 1, 1,
                    20, datetime.now().isoformat(),
                    None, None, None, "{}",
                ))

        # Mock AI 返回有效教训
        batch_response = json.dumps([
            {"id": f"rpt_refl_{i}", "lesson": f"我在技术面评分上高估了{i}号股的突破可靠性。"}
            for i in range(3)
        ])

        mock_client = MagicMock()
        with patch("knowledge.outcome_tracker.get_manager", return_value=mgr), \
             patch("knowledge.kb_db.get_manager", return_value=mgr), \
             patch("knowledge.regime_detector.get_regime_history", return_value=[]), \
             patch("ai.client.get_ai_client",
                   return_value=(mock_client, {"model": "test"}, None)), \
             patch("ai.client.call_ai",
                   return_value=(batch_response, None)), \
             patch("knowledge.kb_utils.parse_ai_json",
                   return_value=json.loads(batch_response)), \
             patch("knowledge.case_memory.extract_sector_tags", return_value=[]):
            processed = process_pending_reflections(max_batch=10)
            assert processed == 3

            count = get_case_count()
            assert count >= 3
