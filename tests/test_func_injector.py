"""injector 功能性测试 — 知识注入策展流程"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


# ══════════════════════════════════════════════════════════════════
# TestBuildKnowledgeContext
# ══════════════════════════════════════════════════════════════════

class TestBuildKnowledgeContext:
    """build_knowledge_context 主入口测试。"""

    def _patch_all_collectors(self, ai_return=None, ai_error=None, candidates=None):
        """构建通用 patch context。"""
        if candidates is None:
            candidates = [
                {"type": "该股历史", "priority": 10, "content": "过去3次分析：看多2次，10日胜率66%"},
                {"type": "校准警示", "priority": 9, "content": "技术面维度系统性偏乐观，建议下调1分"},
                {"type": "相似案例", "priority": 8, "content": "某AI算力股 牛市 7.5分看多 → +8%"},
            ]

        patches = {
            "knowledge.injector._collect_knowledge_candidates": MagicMock(return_value=candidates),
            "knowledge.injector._build_stock_profile": MagicMock(return_value="【待分析股票画像】测试股(600000.SH)"),
        }

        if ai_return is not None:
            mock_client = MagicMock()
            patches["ai.client.get_ai_client"] = MagicMock(return_value=(mock_client, {"model": "test"}, None))
            patches["ai.client.call_ai"] = MagicMock(return_value=(ai_return, ai_error))
        else:
            patches["ai.client.get_ai_client"] = MagicMock(return_value=(None, {}, "unavailable"))
            patches["ai.client.call_ai"] = MagicMock(return_value=("", "error"))

        return patches

    def test_returns_nonempty_with_ai(self, mock_ai_curation):
        """mock AI 成功 → 包含'历史知识库参考'"""
        from knowledge.injector import build_knowledge_context

        patches = self._patch_all_collectors(ai_return=mock_ai_curation)
        with patch.multiple("knowledge.injector", _collect_knowledge_candidates=patches["knowledge.injector._collect_knowledge_candidates"],
                           _build_stock_profile=patches["knowledge.injector._build_stock_profile"]):
            with patch("ai.client.get_ai_client", patches["ai.client.get_ai_client"]), \
                 patch("ai.client.call_ai", patches["ai.client.call_ai"]):
                result = build_knowledge_context(
                    stock_code="600000.SH", stock_name="测试股",
                )
                assert "历史知识库参考" in result

    def test_ai_failure_fallback(self):
        """mock AI 失败 → 规则拼接（仍有内容）"""
        from knowledge.injector import build_knowledge_context

        candidates = [
            {"type": "该股历史", "priority": 10, "content": "过去3次分析记录"},
            {"type": "校准警示", "priority": 9, "content": "技术面偏乐观"},
        ]
        patches = self._patch_all_collectors(ai_return=None, candidates=candidates)

        with patch.multiple("knowledge.injector",
                           _collect_knowledge_candidates=patches["knowledge.injector._collect_knowledge_candidates"],
                           _build_stock_profile=patches["knowledge.injector._build_stock_profile"]):
            with patch("ai.client.get_ai_client", patches["ai.client.get_ai_client"]), \
                 patch("ai.client.call_ai", patches["ai.client.call_ai"]):
                result = build_knowledge_context(
                    stock_code="600000.SH", stock_name="测试股",
                )
                assert len(result) > 0
                assert "历史知识库参考" in result

    def test_empty_when_no_data(self):
        """全空 DB + 无历史 → 返回空字符串"""
        from knowledge.injector import build_knowledge_context

        with patch("knowledge.injector._collect_knowledge_candidates", return_value=[]), \
             patch("knowledge.injector._build_stock_profile", return_value="画像"):
            result = build_knowledge_context(stock_code="600000.SH", stock_name="测试")
            assert result == ""


# ══════════════════════════════════════════════════════════════════
# TestFallbackRuleBased
# ══════════════════════════════════════════════════════════════════

class TestFallbackRuleBased:
    """直接测试 _fallback_rule_based。"""

    def test_priority_order(self):
        """候选按 priority 降序"""
        from knowledge.injector import _fallback_rule_based

        candidates = [
            {"type": "低优先", "priority": 3, "content": "低优内容"},
            {"type": "高优先", "priority": 10, "content": "高优内容"},
            {"type": "中优先", "priority": 6, "content": "中优内容"},
        ]
        result = _fallback_rule_based(candidates, max_chars=4000)
        # 高优先应出现在低优先之前
        high_pos = result.index("高优内容")
        low_pos = result.index("低优内容")
        assert high_pos < low_pos

    def test_max_chars_limit(self):
        """超长内容截断"""
        from knowledge.injector import _fallback_rule_based

        candidates = [
            {"type": "大块", "priority": 10, "content": "A" * 3000},
            {"type": "小块", "priority": 9, "content": "B" * 100},
        ]
        result = _fallback_rule_based(candidates, max_chars=500)
        # 结果长度不超过 max_chars
        assert len(result) <= 500


# ══════════════════════════════════════════════════════════════════
# TestBuildStockProfile
# ══════════════════════════════════════════════════════════════════

class TestBuildStockProfile:
    """_build_stock_profile 测试。"""

    def test_with_valid_kline(self, kline_bull):
        """mock K线数据 → 返回包含 K 线形态信息的文本。

        注意：_build_stock_profile 内部 get_price_df 返回 tuple，
        代码不解包直接 len()，所以需要 mock 返回 DataFrame 本身才能触发形态分析。
        这里验证当 get_price_df 返回 tuple 时仍能生成基本画像。
        """
        from knowledge.injector import _build_stock_profile

        mock_pattern = MagicMock()
        mock_pattern.name = "锤子线"
        mock_pattern.pattern_id = "hammer"

        with patch("data.tushare_client.get_price_df", return_value=kline_bull), \
             patch("knowledge.kline_patterns.detect_all_patterns", return_value=[mock_pattern]), \
             patch("knowledge.kline_patterns.classify_position", return_value="底部"), \
             patch("knowledge.kline_patterns.classify_volume_state", return_value="放量"), \
             patch("knowledge.case_memory.extract_sector_tags", return_value=["AI算力"]), \
             patch("knowledge.regime_detector.get_current_regime", return_value={"regime": "bull", "regime_label": "牛市"}):
            result = _build_stock_profile("600000.SH", "测试股")
            assert "K线" in result or "位置" in result or "量能" in result

    def test_with_no_kline(self):
        """K线不可用 → 返回基本画像（仅名称）"""
        from knowledge.injector import _build_stock_profile

        with patch("data.tushare_client.get_price_df", side_effect=Exception("no data")), \
             patch("knowledge.case_memory.extract_sector_tags", return_value=[]), \
             patch("knowledge.regime_detector.get_current_regime", return_value=None):
            result = _build_stock_profile("600000.SH", "测试股")
            assert "测试股" in result
            assert "600000.SH" in result
