"""regime_detector 功能性测试 — 环境检测完整循环"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from tests.conftest_functional import make_kline_df


# ══════════════════════════════════════════════════════════════════
# TestDetectCurrentRegime
# ══════════════════════════════════════════════════════════════════

class TestDetectCurrentRegime:
    """detect_current_regime 市场环境检测。"""

    def test_bull_market(self, kline_bull, tmp_path):
        """kline_bull → regime='bull'"""
        from knowledge.regime_detector import detect_current_regime

        regime_file = tmp_path / "regime_log.jsonl"

        with patch("data.tushare_client.get_price_df", return_value=(kline_bull, None)), \
             patch("knowledge.regime_detector.REGIME_FILE", regime_file), \
             patch("knowledge.regime_detector.get_current_regime", return_value=None):
            result = detect_current_regime()
            assert result["regime"] == "bull"

    def test_bear_market(self, kline_bear, tmp_path):
        """kline_bear → regime='bear'"""
        from knowledge.regime_detector import detect_current_regime

        regime_file = tmp_path / "regime_log.jsonl"

        with patch("data.tushare_client.get_price_df", return_value=(kline_bear, None)), \
             patch("knowledge.regime_detector.REGIME_FILE", regime_file), \
             patch("knowledge.regime_detector.get_current_regime", return_value=None):
            result = detect_current_regime()
            assert result["regime"] == "bear"

    def test_shock_market(self, kline_shock, tmp_path):
        """kline_shock → regime='shock'"""
        from knowledge.regime_detector import detect_current_regime

        regime_file = tmp_path / "regime_log.jsonl"

        with patch("data.tushare_client.get_price_df", return_value=(kline_shock, None)), \
             patch("knowledge.regime_detector.REGIME_FILE", regime_file), \
             patch("knowledge.regime_detector.get_current_regime", return_value=None):
            result = detect_current_regime()
            # shock 数据无明确趋势，可能是 shock 或 rotation
            assert result["regime"] in ("shock", "rotation")

    def test_data_unavailable_fallback(self, tmp_path):
        """get_price_df 返回 (None, 'err') → 'shock'"""
        from knowledge.regime_detector import detect_current_regime

        regime_file = tmp_path / "regime_log.jsonl"

        with patch("data.tushare_client.get_price_df", return_value=(None, "network error")), \
             patch("knowledge.regime_detector.REGIME_FILE", regime_file):
            result = detect_current_regime()
            assert result["regime"] == "shock"
            assert "fallback_reason" in result.get("indicators", {})

    def test_insufficient_data_fallback(self, tmp_path):
        """只有 30 天数据 → 'shock'（需要60天）"""
        from knowledge.regime_detector import detect_current_regime

        short_df = make_kline_df("bull", days=30)
        regime_file = tmp_path / "regime_log.jsonl"

        with patch("data.tushare_client.get_price_df", return_value=(short_df, None)), \
             patch("knowledge.regime_detector.REGIME_FILE", regime_file):
            result = detect_current_regime()
            assert result["regime"] == "shock"


# ══════════════════════════════════════════════════════════════════
# TestHysteresis
# ══════════════════════════════════════════════════════════════════

class TestHysteresis:
    """滞后逻辑测试。"""

    def test_first_detection_no_hysteresis(self):
        """首次 → 直接采用"""
        from knowledge.regime_detector import _apply_hysteresis

        with patch("knowledge.regime_detector.get_current_regime", return_value=None):
            result = _apply_hysteresis("bull")
            assert result == "bull"

    def test_single_day_no_switch(self):
        """1 天不同 → 保持原状"""
        from knowledge.regime_detector import _apply_hysteresis

        with patch("knowledge.regime_detector.get_current_regime",
                   return_value={"regime": "shock"}), \
             patch("knowledge.regime_detector.get_regime_history",
                   return_value=[{"date": "2026-03-08", "regime": "shock",
                                  "indicators": {"raw_regime": "bull"}}]):
            result = _apply_hysteresis("bull")
            # 只有 1 天 raw_regime=bull，不够 HYSTERESIS_DAYS=2
            assert result == "shock"

    def test_confirm_after_n_days(self):
        """N 天连续相同 → 切换"""
        from knowledge.regime_detector import _apply_hysteresis
        from knowledge.kb_config import REGIME_HYSTERESIS_DAYS

        history = [
            {"date": f"2026-03-{8+i:02d}", "regime": "shock",
             "indicators": {"raw_regime": "bull"}}
            for i in range(REGIME_HYSTERESIS_DAYS)
        ]

        with patch("knowledge.regime_detector.get_current_regime",
                   return_value={"regime": "shock"}), \
             patch("knowledge.regime_detector.get_regime_history",
                   return_value=history):
            result = _apply_hysteresis("bull")
            assert result == "bull"


# ══════════════════════════════════════════════════════════════════
# TestPersistence
# ══════════════════════════════════════════════════════════════════

class TestPersistence:
    """持久化读写测试。"""

    def test_write_and_read_back(self, kline_bull, tmp_path):
        """detect → get_current_regime → 一致"""
        from knowledge.regime_detector import detect_current_regime, get_current_regime

        regime_file = tmp_path / "regime_log.jsonl"

        with patch("data.tushare_client.get_price_df", return_value=(kline_bull, None)), \
             patch("knowledge.regime_detector.REGIME_FILE", regime_file), \
             patch("knowledge.regime_detector.get_current_regime",
                   wraps=lambda: _read_regime(regime_file)):
            # 首次检测：直接写入
            detected = detect_current_regime()

        # 重新 patch 读取
        with patch("knowledge.regime_detector.REGIME_FILE", regime_file):
            current = get_current_regime()
            assert current is not None
            assert current["regime"] == detected["regime"]

    def test_get_regime_history(self, kline_bull, tmp_path):
        """多次 detect → history 完整"""
        from knowledge.regime_detector import detect_current_regime, get_regime_history

        regime_file = tmp_path / "regime_log.jsonl"

        # 手动写入多条记录
        for i in range(3):
            entry = {
                "date": f"2026-03-{10+i:02d}",
                "regime": "bull",
                "regime_label": "牛市",
                "indicators": {},
            }
            with open(regime_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        with patch("knowledge.regime_detector.REGIME_FILE", regime_file):
            history = get_regime_history(days=30)
            assert len(history) >= 3


def _read_regime(regime_file: Path):
    """辅助：从 regime_file 读最后一条。"""
    from knowledge.kb_io import read_jsonl_tail
    tail = read_jsonl_tail(regime_file, n=1)
    return tail[0] if tail else None
