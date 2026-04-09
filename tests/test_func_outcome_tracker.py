"""outcome_tracker 功能性测试 — evaluate_pending 完整流程"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

# fixtures 从 conftest_functional.py 自动引入


# ══════════════════════════════════════════════════════════════════
# TestCalcReturns
# ══════════════════════════════════════════════════════════════════

class TestCalcReturns:
    """_calc_returns 的精确数值验证。"""

    def test_calc_returns_known_values(self):
        """构造精确 25 行 DataFrame，验证 5/10/20 日收益率。"""
        from knowledge.outcome_tracker import _calc_returns

        base_close = 10.0
        rows = []
        dt = datetime(2026, 3, 1)
        for i in range(25):
            if i == 0:
                close = base_close
            elif i <= 5:
                # 均匀涨到 10.5
                close = base_close + 0.1 * i
            elif i <= 10:
                # 均匀涨到 11.0
                close = 10.5 + 0.1 * (i - 5)
            else:
                # 回落到 9.5
                close = 11.0 - 0.15 * (i - 10) if i <= 20 else 9.5 - 0.05 * (i - 20)

            date_str = dt.strftime("%Y%m%d")
            rows.append({
                "日期": date_str,
                "开盘": close,
                "最高": close + 0.1,
                "最低": close - 0.1,
                "收盘": close,
                "成交量": 100000,
                "涨跌幅": 0.0,
            })
            dt += timedelta(days=1)
            while dt.weekday() >= 5:
                dt += timedelta(days=1)

        df = pd.DataFrame(rows)
        report_date = datetime(2026, 3, 1)

        result = _calc_returns(df, report_date, None)
        assert result is not None
        assert result["close_at_report"] == base_close
        assert result["return_5d"] == pytest.approx(5.0, abs=0.5)
        assert result["return_10d"] == pytest.approx(10.0, abs=0.5)
        # day 20: close ~ 9.5 → return ~ -5%
        assert result["return_20d"] == pytest.approx(-5.0, abs=2.0)

    def test_calc_returns_insufficient_data(self):
        """只有 3 天数据 → reliable_10d=False, reliable_20d=False"""
        from knowledge.outcome_tracker import _calc_returns

        rows = []
        dt = datetime(2026, 3, 1)
        for i in range(3):
            rows.append({
                "日期": dt.strftime("%Y%m%d"),
                "开盘": 10.0, "最高": 10.5, "最低": 9.5,
                "收盘": 10.0 + i * 0.1,
                "成交量": 100000, "涨跌幅": 0.0,
            })
            dt += timedelta(days=1)

        df = pd.DataFrame(rows)
        result = _calc_returns(df, datetime(2026, 3, 1), None)
        assert result is not None
        assert result["reliable_10d"] is False
        assert result["reliable_20d"] is False

    def test_calc_returns_empty_df(self):
        """空 DataFrame → 返回 None"""
        from knowledge.outcome_tracker import _calc_returns

        df = pd.DataFrame(columns=["日期", "开盘", "最高", "最低", "收盘", "成交量", "涨跌幅"])
        result = _calc_returns(df, datetime(2026, 3, 1), None)
        assert result is None


# ══════════════════════════════════════════════════════════════════
# TestEvaluatePendingFull
# ══════════════════════════════════════════════════════════════════

class TestEvaluatePendingFull:
    """evaluate_pending 完整流程测试。"""

    def test_happy_path(self, reports_db_with_markdown, func_db_manager,
                        kline_bull, reset_outcome_tracker):
        """5 报告 → evaluate_pending(min_days=8) 返回 5，load_outcomes 返回 5"""
        from knowledge.outcome_tracker import evaluate_pending, load_outcomes

        db_path, md_dir = reports_db_with_markdown
        mgr, tmp_path = func_db_manager

        with patch("knowledge.outcome_tracker.DB_PATH", db_path), \
             patch("knowledge.outcome_tracker.get_manager", return_value=mgr), \
             patch("data.tushare_client.get_price_df", return_value=(kline_bull, None)), \
             patch("knowledge.outcome_tracker._get_benchmark_returns", return_value=None), \
             patch("knowledge.outcome_tracker.OUTCOMES_FILE", tmp_path / "nonexistent.jsonl"):
            count = evaluate_pending(min_days=8)
            assert count == 5

            outcomes = load_outcomes()
            assert len(outcomes) == 5

    def test_skip_already_evaluated(self, reports_db_with_markdown, func_db_manager,
                                     kline_bull, reset_outcome_tracker):
        """调两次 → 第二次返回 0"""
        from knowledge.outcome_tracker import evaluate_pending

        db_path, md_dir = reports_db_with_markdown
        mgr, tmp_path = func_db_manager

        with patch("knowledge.outcome_tracker.DB_PATH", db_path), \
             patch("knowledge.outcome_tracker.get_manager", return_value=mgr), \
             patch("data.tushare_client.get_price_df", return_value=(kline_bull, None)), \
             patch("knowledge.outcome_tracker._get_benchmark_returns", return_value=None), \
             patch("knowledge.outcome_tracker.OUTCOMES_FILE", tmp_path / "nonexistent.jsonl"):
            first = evaluate_pending(min_days=8)
            assert first == 5
            second = evaluate_pending(min_days=8)
            assert second == 0

    def test_kline_failure_skips(self, reports_db_with_markdown, func_db_manager,
                                  reset_outcome_tracker):
        """get_price_df 返回 (None, "error") → 返回 0"""
        from knowledge.outcome_tracker import evaluate_pending

        db_path, md_dir = reports_db_with_markdown
        mgr, tmp_path = func_db_manager

        with patch("knowledge.outcome_tracker.DB_PATH", db_path), \
             patch("knowledge.outcome_tracker.get_manager", return_value=mgr), \
             patch("data.tushare_client.get_price_df", return_value=(None, "network error")), \
             patch("knowledge.outcome_tracker._get_benchmark_returns", return_value=None), \
             patch("knowledge.outcome_tracker.OUTCOMES_FILE", tmp_path / "nonexistent.jsonl"):
            count = evaluate_pending(min_days=8)
            assert count == 0

    def test_no_scores_skips(self, func_db_manager, kline_bull, reset_outcome_tracker, tmp_path):
        """用无 SCORES 块的 markdown → 跳过"""
        from knowledge.outcome_tracker import evaluate_pending

        mgr, db_tmp = func_db_manager

        # 创建一个无 SCORES 的报告
        reports_dir = tmp_path / "storage2"
        reports_dir.mkdir(parents=True)
        md_dir = tmp_path / "reports2"
        md_dir.mkdir(parents=True)

        db_path = reports_dir / "reports.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                report_id TEXT PRIMARY KEY,
                stock_name TEXT, stock_code TEXT,
                summary TEXT, markdown_path TEXT, created_at TEXT
            )
        """)

        report_date = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S")
        md_path = md_dir / "no_score.md"
        md_path.write_text("# 报告\n没有评分块。", encoding="utf-8")
        conn.execute(
            "INSERT INTO reports VALUES (?,?,?,?,?,?)",
            ("rpt_noscore", "测试", "600000.SH", "无分数", str(md_path), report_date),
        )
        conn.commit()
        conn.close()

        with patch("knowledge.outcome_tracker.DB_PATH", db_path), \
             patch("knowledge.outcome_tracker.get_manager", return_value=mgr), \
             patch("data.tushare_client.get_price_df", return_value=(kline_bull, None)), \
             patch("knowledge.outcome_tracker._get_benchmark_returns", return_value=None), \
             patch("knowledge.outcome_tracker.OUTCOMES_FILE", tmp_path / "nonexistent.jsonl"):
            count = evaluate_pending(min_days=8)
            assert count == 0

    def test_direction_inference(self, reports_db_with_markdown, func_db_manager,
                                  kline_bull, reset_outcome_tracker):
        """高分报告 direction=bullish，低分=bearish"""
        from knowledge.outcome_tracker import evaluate_pending, load_outcomes

        db_path, md_dir = reports_db_with_markdown
        mgr, tmp_path = func_db_manager

        with patch("knowledge.outcome_tracker.DB_PATH", db_path), \
             patch("knowledge.outcome_tracker.get_manager", return_value=mgr), \
             patch("data.tushare_client.get_price_df", return_value=(kline_bull, None)), \
             patch("knowledge.outcome_tracker._get_benchmark_returns", return_value=None), \
             patch("knowledge.outcome_tracker.OUTCOMES_FILE", tmp_path / "nonexistent.jsonl"):
            evaluate_pending(min_days=8)
            outcomes = load_outcomes()

            by_id = {o["report_id"]: o for o in outcomes}
            # rpt_005 极高分(9,9,8,8) → weighted >= 6 → bullish
            assert by_id["rpt_005"]["direction"] == "bullish"
            # rpt_003 低分(3,3,4,3) → weighted ~3.35 → composite > 3 → neutral
            # _infer_direction: <= 3 才是 bearish
            assert by_id["rpt_003"]["direction"] in ("bearish", "neutral")
            # rpt_001 高分(8,9,7,7) → bullish
            assert by_id["rpt_001"]["direction"] == "bullish"


# ══════════════════════════════════════════════════════════════════
# TestGetAccuracySummary
# ══════════════════════════════════════════════════════════════════

class TestGetAccuracySummary:
    """get_accuracy_summary 的统计验证。"""

    def _insert_outcomes(self, mgr, outcomes_data):
        """辅助：直接插入 outcomes 到 DB。"""
        with mgr.write("outcomes") as conn:
            for o in outcomes_data:
                conn.execute("""
                    INSERT OR REPLACE INTO outcomes (
                        report_id, report_date, stock_code, stock_name, source,
                        scores, weighted_score, direction, close_at_report,
                        return_5d, return_10d, return_20d,
                        hit_5d, hit_10d, hit_20d,
                        actual_trade_days, evaluated_at,
                        return_benchmark_10d, beat_market_10d,
                        war_room_divergence, war_room_generals
                    ) VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?, ?,?, ?,?)
                """, (
                    o["report_id"], o["report_date"], o["stock_code"],
                    o.get("stock_name", ""), o.get("source", "report"),
                    json.dumps(o.get("scores", {})), o.get("weighted_score", 5),
                    o.get("direction", "neutral"), o.get("close_at_report"),
                    o.get("return_5d", 0), o.get("return_10d", 0), o.get("return_20d", 0),
                    1 if o.get("hit_5d") else (0 if o.get("hit_5d") is False else None),
                    1 if o.get("hit_10d") else (0 if o.get("hit_10d") is False else None),
                    1 if o.get("hit_20d") else (0 if o.get("hit_20d") is False else None),
                    o.get("actual_trade_days", 20),
                    datetime.now().isoformat(),
                    None, None, None, "{}",
                ))

    def test_empty(self, func_db_manager):
        """空表 → sample_count=0"""
        from knowledge.outcome_tracker import get_accuracy_summary

        mgr, tmp_path = func_db_manager
        with patch("knowledge.outcome_tracker.get_manager", return_value=mgr):
            result = get_accuracy_summary(days=90)
            assert result["sample_count"] == 0

    def test_score_buckets(self, func_db_manager):
        """验证高/中/低分组。"""
        from knowledge.outcome_tracker import get_accuracy_summary

        mgr, tmp_path = func_db_manager
        today = datetime.now().strftime("%Y-%m-%d")
        outcomes = [
            {"report_id": f"o_{i}", "report_date": today, "stock_code": f"60000{i}.SH",
             "weighted_score": ws, "direction": "bullish",
             "return_5d": 3.0, "return_10d": 5.0, "return_20d": 8.0,
             "hit_5d": True, "hit_10d": True, "hit_20d": True}
            for i, ws in enumerate([8.0, 7.5, 7.0, 6.5, 6.0, 5.5, 5.0, 4.0, 3.0, 2.0])
        ]
        self._insert_outcomes(mgr, outcomes)

        with patch("knowledge.outcome_tracker.get_manager", return_value=mgr):
            result = get_accuracy_summary(days=90)
            assert result["sample_count"] == 10
            buckets = result["by_score_bucket"]
            assert buckets["high_ge7"]["count"] == 3   # 8.0, 7.5, 7.0
            assert buckets["mid_5to7"]["count"] == 4    # 6.5, 6.0, 5.5, 5.0
            assert buckets["low_lt5"]["count"] == 3     # 4.0, 3.0, 2.0

    def test_hit_rates(self, func_db_manager):
        """验证 hit_rate_5d/10d/20d 计算。"""
        from knowledge.outcome_tracker import get_accuracy_summary

        mgr, tmp_path = func_db_manager
        today = datetime.now().strftime("%Y-%m-%d")
        # 10 条，5d: 6 命中, 10d: 7 命中, 20d: 8 命中
        outcomes = []
        for i in range(10):
            outcomes.append({
                "report_id": f"hit_{i}", "report_date": today,
                "stock_code": f"60000{i}.SH",
                "weighted_score": 7.0, "direction": "bullish",
                "return_5d": 3.0, "return_10d": 5.0, "return_20d": 8.0,
                "hit_5d": i < 6, "hit_10d": i < 7, "hit_20d": i < 8,
            })
        self._insert_outcomes(mgr, outcomes)

        with patch("knowledge.outcome_tracker.get_manager", return_value=mgr):
            result = get_accuracy_summary(days=90)
            assert result["hit_rate_5d"] == 60.0
            assert result["hit_rate_10d"] == 70.0
            assert result["hit_rate_20d"] == 80.0
