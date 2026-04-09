"""集成测试 — 跨模块端到端链路"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest


# ══════════════════════════════════════════════════════════════════
# TestOutcomeReflectionCaseChain
# ══════════════════════════════════════════════════════════════════

class TestOutcomeReflectionCaseChain:
    """outcome → reflection → case_memory 全链路。"""

    def test_full_chain(self, func_db_manager, reports_db_with_markdown,
                        kline_bull, mock_ai_reflection_batch,
                        reset_outcome_tracker):
        """
        Step 1: evaluate_pending → outcomes
        Step 2: process_pending_reflections → cases with lessons
        Step 3: retrieve_similar_cases → finds stored cases
        """
        from knowledge.outcome_tracker import evaluate_pending, load_outcomes
        from knowledge.reflection import process_pending_reflections
        from knowledge.case_memory import retrieve_similar_cases, get_case_count

        db_path, md_dir = reports_db_with_markdown
        mgr, tmp_path = func_db_manager

        # Step 1: evaluate_pending
        with patch("knowledge.outcome_tracker.DB_PATH", db_path), \
             patch("knowledge.outcome_tracker.get_manager", return_value=mgr), \
             patch("data.tushare_client.get_price_df", return_value=(kline_bull, None)), \
             patch("knowledge.outcome_tracker._get_benchmark_returns", return_value=None), \
             patch("knowledge.outcome_tracker.OUTCOMES_FILE", tmp_path / "nonexistent.jsonl"):
            eval_count = evaluate_pending(min_days=8)
            assert eval_count > 0

        outcomes = None
        with patch("knowledge.outcome_tracker.get_manager", return_value=mgr):
            outcomes = load_outcomes()
            assert len(outcomes) > 0

        # Step 2: process_pending_reflections
        # 构建批量教训（匹配实际 report_id）
        batch_lessons = []
        for o in outcomes:
            batch_lessons.append({
                "id": o["report_id"],
                "lesson": f"我在技术面评分上高估了{o.get('stock_name', '')}的突破可靠性。",
            })
        batch_json = json.dumps(batch_lessons)

        mock_client = MagicMock()
        with patch("knowledge.outcome_tracker.get_manager", return_value=mgr), \
             patch("knowledge.kb_db.get_manager", return_value=mgr), \
             patch("knowledge.regime_detector.get_regime_history", return_value=[]), \
             patch("ai.client.get_ai_client",
                   return_value=(mock_client, {"model": "test"}, None)), \
             patch("ai.client.call_ai",
                   return_value=(batch_json, None)), \
             patch("knowledge.kb_utils.parse_ai_json",
                   return_value=json.loads(batch_json)), \
             patch("knowledge.case_memory.extract_sector_tags", return_value=["AI算力"]):
            reflect_count = process_pending_reflections(max_batch=20)
            assert reflect_count > 0

        # Step 3: retrieve_similar_cases
        with patch("knowledge.kb_db.get_manager", return_value=mgr):
            cases = retrieve_similar_cases(
                regime="bull", sector_tags=["AI算力"], top_k=3, max_days=365,
            )
            # 应该能找到刚才存入的 case
            assert get_case_count() > 0


# ══════════════════════════════════════════════════════════════════
# TestKlineDiaryCycle
# ══════════════════════════════════════════════════════════════════

class TestKlineDiaryCycle:
    """盘感日记：扫描 → 回测 → 统计重建。"""

    def test_scan_backtest_stats(self, func_db_manager, kline_bull):
        """
        Step 1: scan_and_observe (mock 形态检测)
        Step 2: 手动填 actual_return_5d
        Step 3: rebuild_pattern_stats
        Assert: stats 表有数据
        """
        from knowledge.kline_diary import scan_and_observe, rebuild_pattern_stats

        mgr, tmp_path = func_db_manager

        mock_pattern = MagicMock()
        mock_pattern.pattern_id = "hammer"
        mock_pattern.name = "锤子线"

        stock_list = [("600000.SH", "测试股A"), ("600001.SH", "测试股B")]

        # kline_diary.scan_and_observe 内部: df = get_price_df(ts_code, days=60)
        # 不解包 tuple，直接用 len(df)，所以 mock 须返回 DataFrame 本身
        with patch("knowledge.kb_db.get_manager", return_value=mgr), \
             patch("data.tushare_client.get_price_df", return_value=kline_bull), \
             patch("knowledge.kline_patterns.detect_all_patterns",
                   return_value=[mock_pattern]), \
             patch("knowledge.kline_patterns.classify_position", return_value="底部"), \
             patch("knowledge.kline_patterns.classify_volume_state", return_value="放量"), \
             patch("knowledge.regime_detector.get_current_regime",
                   return_value={"regime": "bull"}):
            observed = scan_and_observe(stock_list)
            assert observed >= 1

        # Step 2: 手动填 actual_return_5d
        with mgr.write("kline_diary") as conn:
            conn.execute(
                "UPDATE kline_observations SET actual_return_5d=3.5, hit=1"
            )

        # Step 3: rebuild_pattern_stats
        with patch("knowledge.kb_db.get_manager", return_value=mgr):
            stats_count = rebuild_pattern_stats()
            assert stats_count >= 1

        # 验证 stats 表有数据
        with mgr.read("kline_diary") as conn:
            rows = conn.execute("SELECT COUNT(*) FROM kline_pattern_stats").fetchone()
            assert rows[0] >= 1
