"""Unit tests for services.analysis_service score parsing and correction."""

from __future__ import annotations

import unittest

from services.analysis_service import (
    SCORE_WEIGHTS,
    apply_bucket_correction,
    check_score_spread,
    parse_scores,
    _cleanup_report_text,
    _split_report_and_summary,
)


class TestParseScores(unittest.TestCase):
    """Test parse_scores() across all supported formats."""

    # ── Standard /100 format ──────────────────────────────────────

    def test_standard_100_format(self, score_block_100=None):
        text = """<<<SCORES>>>
基本面: 72/100
预期差: 85/100
资金面: 68/100
技术面: 60/100
S级豁免: 否
致命缺陷: 无
操作评级: 侦察待命
<<<END_SCORES>>>"""
        scores = parse_scores(text)
        self.assertIsNotNone(scores)
        self.assertAlmostEqual(scores["基本面"], 72)
        self.assertAlmostEqual(scores["预期差"], 85)
        self.assertAlmostEqual(scores["资金面"], 68)
        self.assertAlmostEqual(scores["技术面"], 60)
        self.assertFalse(scores["_s_exempt"])
        self.assertFalse(scores["_has_fatal"])
        self.assertEqual(scores["_ai_rating"], "侦察待命")

    def test_weighted_calculation(self):
        text = """<<<SCORES>>>
基本面: 60/100
预期差: 80/100
资金面: 70/100
技术面: 50/100
<<<END_SCORES>>>"""
        scores = parse_scores(text)
        # Manual calculation: 60*0.10 + 80*0.40 + 70*0.30 + 50*0.20 = 6+32+21+10 = 69
        expected = (60 * 0.10 + 80 * 0.40 + 70 * 0.30 + 50 * 0.20) / 1.0
        self.assertAlmostEqual(scores["综合加权"], round(expected, 1))

    def test_partial_dimensions_weighted(self):
        """Only some dimensions present — weight should normalize."""
        text = """<<<SCORES>>>
基本面: 80/100
预期差: 90/100
<<<END_SCORES>>>"""
        scores = parse_scores(text)
        expected_weight = SCORE_WEIGHTS["基本面"] + SCORE_WEIGHTS["预期差"]
        expected = (80 * SCORE_WEIGHTS["基本面"] + 90 * SCORE_WEIGHTS["预期差"]) / expected_weight
        self.assertAlmostEqual(scores["综合加权"], round(expected, 1))

    # ── Old 10-point scale ────────────────────────────────────────

    def test_10_point_scale(self):
        text = """<<<SCORES>>>
基本面: 7/10
预期差: 8/10
资金面: 6/10
技术面: 5/10
<<<END_SCORES>>>"""
        scores = parse_scores(text)
        self.assertAlmostEqual(scores["基本面"], 70)
        self.assertAlmostEqual(scores["预期差"], 80)
        self.assertAlmostEqual(scores["资金面"], 60)
        self.assertAlmostEqual(scores["技术面"], 50)

    # ── Plain number format ───────────────────────────────────────

    def test_plain_number_as_100(self):
        """Numbers > 10 treated as /100."""
        text = """<<<SCORES>>>
基本面: 72
预期差: 85
资金面: 68
技术面: 60
<<<END_SCORES>>>"""
        scores = parse_scores(text)
        self.assertAlmostEqual(scores["基本面"], 72)
        self.assertAlmostEqual(scores["预期差"], 85)

    def test_plain_number_small_auto_scale(self):
        """Numbers <= 10 auto-detected as 10-point scale."""
        text = """<<<SCORES>>>
基本面: 7
预期差: 8
资金面: 6
技术面: 5
<<<END_SCORES>>>"""
        scores = parse_scores(text)
        self.assertAlmostEqual(scores["基本面"], 70)
        self.assertAlmostEqual(scores["预期差"], 80)

    # ── Fallback: no SCORES block ─────────────────────────────────

    def test_no_block_fallback(self):
        text = """## 评分
基本面: 65/100
预期差: 70/100
资金面: 55/100
技术面: 80/100"""
        scores = parse_scores(text)
        self.assertIsNotNone(scores)
        self.assertAlmostEqual(scores["基本面"], 65)
        self.assertAlmostEqual(scores["技术面"], 80)

    # ── Flags parsing ─────────────────────────────────────────────

    def test_s_exempt_flag(self):
        text = """<<<SCORES>>>
基本面: 18/100
预期差: 90/100
资金面: 75/100
技术面: 70/100
S级豁免: 是
致命缺陷: 无
<<<END_SCORES>>>"""
        scores = parse_scores(text)
        self.assertTrue(scores["_s_exempt"])
        self.assertFalse(scores["_has_fatal"])

    def test_fatal_flaw_flag(self):
        text = """<<<SCORES>>>
基本面: 15/100
预期差: 60/100
资金面: 45/100
技术面: 50/100
S级豁免: 否
致命缺陷: 有
<<<END_SCORES>>>"""
        scores = parse_scores(text)
        self.assertFalse(scores["_s_exempt"])
        self.assertTrue(scores["_has_fatal"])

    def test_stance_flag(self):
        text = """<<<SCORES>>>
基本面: 70/100
预期差: 80/100
资金面: 65/100
技术面: 60/100
立场: 看多
<<<END_SCORES>>>"""
        scores = parse_scores(text)
        self.assertEqual(scores["_stance"], "看多")

    # ── Empty / invalid input ─────────────────────────────────────

    def test_empty_text_returns_none(self):
        self.assertIsNone(parse_scores(""))

    def test_no_scores_returns_none(self):
        self.assertIsNone(parse_scores("这是一段没有评分的纯文本分析。"))

    def test_chinese_colon(self):
        """Chinese full-width colon should work too."""
        text = """<<<SCORES>>>
基本面：72/100
预期差：85/100
资金面：68/100
技术面：60/100
<<<END_SCORES>>>"""
        scores = parse_scores(text)
        self.assertIsNotNone(scores)
        self.assertAlmostEqual(scores["基本面"], 72)

    def test_with_score_suffix(self):
        """'分' suffix should be handled."""
        text = """<<<SCORES>>>
基本面: 72分/100
预期差: 85分/100
<<<END_SCORES>>>"""
        scores = parse_scores(text)
        self.assertIsNotNone(scores)
        self.assertAlmostEqual(scores["基本面"], 72)


class TestApplyBucketCorrection(unittest.TestCase):
    """Test apply_bucket_correction() circuit breaker and rating logic."""

    def _make_scores(self, 基本面=70, 预期差=70, 资金面=70, 技术面=70, **extra):
        scores = {
            "基本面": 基本面, "预期差": 预期差,
            "资金面": 资金面, "技术面": 技术面,
            "_s_exempt": False, "_has_fatal": False,
        }
        weighted = (基本面 * 0.15 + 预期差 * 0.35 + 资金面 * 0.30 + 技术面 * 0.20)
        scores["综合加权"] = round(weighted, 1)
        scores.update(extra)
        return scores

    # ── Circuit breaker (熔断) ────────────────────────────────────

    def test_fundamentals_below_20_triggers_meltdown(self):
        scores = self._make_scores(基本面=15, 预期差=80, 资金面=70, 技术面=60)
        result = apply_bucket_correction(scores)
        self.assertTrue(result["_bucket_corrected"])
        self.assertLessEqual(result["综合加权"], 30.0)
        self.assertIn("熔断", result.get("_fatal_flaw", ""))

    def test_non_fundamental_below_20_capped_at_50(self):
        scores = self._make_scores(基本面=60, 预期差=18, 资金面=70, 技术面=60)
        result = apply_bucket_correction(scores)
        self.assertTrue(result["_bucket_corrected"])
        self.assertLessEqual(result["综合加权"], 50.0)

    def test_s_exempt_bypasses_meltdown(self):
        scores = self._make_scores(基本面=15, 预期差=90, 资金面=75, 技术面=70, _s_exempt=True)
        result = apply_bucket_correction(scores)
        self.assertFalse(result["_bucket_corrected"])

    def test_all_above_20_no_correction(self):
        scores = self._make_scores(基本面=50, 预期差=60, 资金面=55, 技术面=45)
        result = apply_bucket_correction(scores)
        self.assertFalse(result["_bucket_corrected"])

    # ── Rating determination ──────────────────────────────────────

    def test_ai_rating_preserved(self):
        scores = self._make_scores()
        scores["_ai_rating"] = "总攻信号"
        result = apply_bucket_correction(scores)
        self.assertEqual(result["_rating"], "总攻信号")

    def test_composite_rating_high(self):
        scores = self._make_scores(基本面=80, 预期差=85, 资金面=75, 技术面=70)
        result = apply_bucket_correction(scores)
        self.assertIn(result["_rating"], ("总攻信号", "侦察待命"))

    def test_composite_rating_low(self):
        scores = self._make_scores(基本面=20, 预期差=25, 资金面=20, 技术面=15)
        # After meltdown, composite <= 30
        result = apply_bucket_correction(scores)
        self.assertEqual(result["_rating"], "全线撤退")

    def test_dual_axis_total_attack(self):
        """High attract + confidence + all dims >= 60 => 总攻信号."""
        scores = self._make_scores(基本面=70, 预期差=80, 资金面=75, 技术面=65)
        scores["机会吸引力"] = 88
        scores["逻辑置信度"] = 82
        result = apply_bucket_correction(scores)
        self.assertEqual(result["_rating"], "总攻信号")

    def test_dual_axis_recon(self):
        """Medium attract + confidence => 侦察待命."""
        scores = self._make_scores(基本面=55, 预期差=65, 资金面=60, 技术面=55)
        scores["机会吸引力"] = 72
        scores["逻辑置信度"] = 67
        result = apply_bucket_correction(scores)
        self.assertEqual(result["_rating"], "侦察待命")


class TestCheckScoreSpread(unittest.TestCase):

    def test_low_spread_warning(self):
        scores = {"基本面": 70, "预期差": 72, "资金面": 68, "技术面": 75}
        msg = check_score_spread(scores)
        self.assertIsNotNone(msg)
        self.assertIn("区分度", msg)

    def test_good_spread_no_warning(self):
        scores = {"基本面": 40, "预期差": 85, "资金面": 60, "技术面": 90}
        self.assertIsNone(check_score_spread(scores))

    def test_one_below_60_no_warning(self):
        scores = {"基本面": 55, "预期差": 70, "资金面": 65, "技术面": 75}
        self.assertIsNone(check_score_spread(scores))


class TestCleanupReportText(unittest.TestCase):

    def test_removes_scores_block(self):
        text = "before\n<<<SCORES>>>内容<<<END_SCORES>>>\nafter"
        result = _cleanup_report_text(text)
        self.assertNotIn("SCORES", result)
        self.assertIn("before", result)
        self.assertIn("after", result)

    def test_fixes_unbalanced_bold(self):
        text = "**标题内容"
        result = _cleanup_report_text(text)
        self.assertEqual(result.count("**") % 2, 0)


class TestSplitReportAndSummary(unittest.TestCase):

    def test_standard_split(self):
        text = "报告正文\n<<<REPORT_END>>>\n核心摘要 重点内容"
        summary, body = _split_report_and_summary(text)
        self.assertIn("重点内容", summary)
        self.assertIn("报告正文", body)

    def test_with_heading(self):
        text = "报告正文\n<<<REPORT_END>>>\n# 核心摘要\n摘要内容"
        summary, body = _split_report_and_summary(text)
        self.assertNotIn("# 核心摘要", summary)
        self.assertIn("摘要内容", summary)

    def test_no_marker_returns_fallback(self):
        text = "纯文本没有标记"
        summary, body = _split_report_and_summary(text)
        self.assertIn("⚠️", summary)  # fallback text

    def test_non_string_raises(self):
        with self.assertRaises(TypeError):
            _split_report_and_summary(None)


if __name__ == "__main__":
    unittest.main()
