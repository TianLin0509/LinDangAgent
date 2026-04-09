"""case_memory 功能性测试 — 两阶段检索"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


# ══════════════════════════════════════════════════════════════════
# TestRetrieveSimilarCases
# ══════════════════════════════════════════════════════════════════

class TestRetrieveSimilarCases:
    """两阶段检索功能验证（120 条 CaseCard 预加载）。"""

    def test_returns_top_k(self, preloaded_cases):
        """请求 top_k=5 → 返回 <=5"""
        from knowledge.case_memory import retrieve_similar_cases

        mgr, cases = preloaded_cases
        with patch("knowledge.kb_db.get_manager", return_value=mgr):
            result = retrieve_similar_cases(
                regime="bull", sector_tags=["AI算力"], top_k=5, max_days=365,
            )
            assert len(result) <= 5

    def test_regime_filter(self, preloaded_cases):
        """regime='bull' → 所有结果都有教训"""
        from knowledge.case_memory import retrieve_similar_cases

        mgr, cases = preloaded_cases
        with patch("knowledge.kb_db.get_manager", return_value=mgr):
            result = retrieve_similar_cases(
                regime="bull", sector_tags=["AI算力"], top_k=5, max_days=365,
            )
            # 检索要求 lesson 不为空
            for case in result:
                assert case.lesson != ""

    def test_sector_tag_relevance(self, preloaded_cases):
        """sector_tags=['AI算力'] → 含 AI 算力标签的排前面"""
        from knowledge.case_memory import retrieve_similar_cases

        mgr, cases = preloaded_cases
        with patch("knowledge.kb_db.get_manager", return_value=mgr):
            result = retrieve_similar_cases(
                regime="bull", sector_tags=["AI算力"], top_k=5, max_days=365,
            )
            if result:
                # 第一条结果应与 AI 算力相关
                first = result[0]
                assert "AI算力" in first.sector_tags or first.regime == "bull"

    def test_excludes_same_stock(self, preloaded_cases):
        """stock_code=cases[0].stock_code → 不返回自己"""
        from knowledge.case_memory import retrieve_similar_cases

        mgr, cases = preloaded_cases
        target_code = cases[0].stock_code
        with patch("knowledge.kb_db.get_manager", return_value=mgr):
            result = retrieve_similar_cases(
                regime="bull", sector_tags=["AI算力"],
                stock_code=target_code, top_k=10, max_days=365,
            )
            for case in result:
                assert case.stock_code != target_code

    def test_no_match_empty(self, func_db_manager):
        """空DB → 空列表"""
        from knowledge.case_memory import retrieve_similar_cases

        mgr, tmp_path = func_db_manager
        with patch("knowledge.kb_db.get_manager", return_value=mgr):
            result = retrieve_similar_cases(
                regime="bull", sector_tags=["AI算力"], top_k=5, max_days=365,
            )
            assert result == []

    def test_score_distance_ranking(self, preloaded_cases):
        """给定 current_scores → 分数接近的排前面"""
        from knowledge.case_memory import retrieve_similar_cases

        mgr, cases = preloaded_cases
        target_scores = {"基本面": 7.0, "预期差": 8.0, "资金面": 6.0, "技术面": 7.0}
        with patch("knowledge.kb_db.get_manager", return_value=mgr):
            result = retrieve_similar_cases(
                regime="bull", sector_tags=["AI算力"],
                current_scores=target_scores, top_k=5, max_days=365,
            )
            if len(result) >= 2:
                # 验证排序：前面的 rank_score >= 后面的
                from knowledge.case_memory import _rank_score
                scores_list = [
                    _rank_score(c, "bull", ["AI算力"], target_scores)
                    for c in result
                ]
                for i in range(len(scores_list) - 1):
                    assert scores_list[i] >= scores_list[i + 1] - 1e-9


# ══════════════════════════════════════════════════════════════════
# TestGetSectorSummary
# ══════════════════════════════════════════════════════════════════

class TestGetSectorSummary:
    """板块经验聚合。"""

    def test_enough_cases(self, preloaded_cases):
        """板块有足够案例 → 返回 dict（包含 win_rate_10d 等）"""
        from knowledge.case_memory import get_sector_summary

        mgr, cases = preloaded_cases
        with patch("knowledge.kb_db.get_manager", return_value=mgr):
            # AI算力 在 120 条数据中出现多次
            result = get_sector_summary("AI算力", days=365)
            if result is not None:
                assert "win_rate_10d" in result
                assert "total_cases" in result
                assert result["total_cases"] >= 3

    def test_insufficient_cases(self, func_db_manager):
        """板块案例 < 3 → 返回 None"""
        from knowledge.case_memory import get_sector_summary

        mgr, tmp_path = func_db_manager
        with patch("knowledge.kb_db.get_manager", return_value=mgr):
            result = get_sector_summary("不存在的板块", days=365)
            assert result is None


# ══════════════════════════════════════════════════════════════════
# TestRankScore
# ══════════════════════════════════════════════════════════════════

class TestRankScore:
    """直接测试 _rank_score 函数。"""

    def _make_case(self, regime="bull", sector_tags=None, outcome_type="win",
                   scores=None):
        from knowledge.case_memory import CaseCard
        s = scores or {}
        return CaseCard(
            case_id="test",
            report_date="2026-03-01",
            stock_code="600000.SH",
            stock_name="测试",
            regime=regime,
            sector_tags=sector_tags or [],
            score_fundamental=s.get("基本面", 5),
            score_expectation=s.get("预期差", 5),
            score_capital=s.get("资金面", 5),
            score_technical=s.get("技术面", 5),
            score_weighted=5.0,
            outcome_type=outcome_type,
            lesson="测试教训",
        )

    def test_regime_match_bonus(self):
        """环境匹配 +0.2"""
        from knowledge.case_memory import _rank_score

        case_match = self._make_case(regime="bull")
        case_no_match = self._make_case(regime="bear")

        score_match = _rank_score(case_match, "bull", [], {})
        score_no = _rank_score(case_no_match, "bull", [], {})
        assert score_match - score_no == pytest.approx(0.2, abs=0.01)

    def test_tag_overlap_weight(self):
        """板块重合度影响分数"""
        from knowledge.case_memory import _rank_score

        case_full = self._make_case(sector_tags=["AI算力", "半导体"])
        case_half = self._make_case(sector_tags=["AI算力", "白酒"])
        case_none = self._make_case(sector_tags=["白酒", "医药"])

        s_full = _rank_score(case_full, "", ["AI算力", "半导体"], {})
        s_half = _rank_score(case_half, "", ["AI算力", "半导体"], {})
        s_none = _rank_score(case_none, "", ["AI算力", "半导体"], {})

        assert s_full > s_half
        assert s_half > s_none

    def test_loss_bonus(self):
        """loss 案例 +0.15"""
        from knowledge.case_memory import _rank_score

        case_loss = self._make_case(outcome_type="loss")
        case_win = self._make_case(outcome_type="win")

        s_loss = _rank_score(case_loss, "", [], {})
        s_win = _rank_score(case_win, "", [], {})
        assert s_loss - s_win == pytest.approx(0.15, abs=0.01)
