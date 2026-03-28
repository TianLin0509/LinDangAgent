"""知识注入 — 构建紧凑的知识上下文段落注入到 AI prompt

将结果追踪、市场环境、规律识别、模型绩效等信息压缩成
≤1200 字符的文本段落，注入到报告生成 prompt 中。
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def build_knowledge_context(
    stock_code: str = "",
    stock_name: str = "",
    scores: dict | None = None,
    model_name: str = "",
    max_chars: int = 1200,
) -> str:
    """构建知识库参考段落。scores 为当前分析的四维评分（可选，用于模式匹配）。

    返回空字符串表示知识库无可用数据。
    """
    sections = []

    # 1. 市场环境
    try:
        from knowledge.regime_detector import get_current_regime, get_regime_accuracy
        regime = get_current_regime()
        if regime and regime.get("regime"):
            label = regime.get("regime_label", regime["regime"])
            regime_acc = get_regime_accuracy(regime["regime"])
            if regime_acc.get("directional_count", 0) >= 3:
                sections.append(
                    f"▸ 市场环境：当前{label}，"
                    f"该环境下系统10日胜率{regime_acc['hit_rate_10d']:.0f}%"
                    f"（{regime_acc['directional_count']}样本）"
                )
            else:
                sections.append(f"▸ 市场环境：当前{label}")
    except Exception as exc:
        logger.debug("[injector] regime error: %r", exc)

    # 2. 系统整体绩效
    try:
        from knowledge.outcome_tracker import get_accuracy_summary
        acc = get_accuracy_summary(days=90)
        if acc.get("directional_count", 0) >= 5:
            high_info = ""
            if acc.get("high_score_count", 0) >= 3:
                high_info = f"，评分≥7推荐胜率{acc['high_score_hit_10d']:.0f}%"
            sections.append(
                f"▸ 系统绩效：过去90天10日胜率{acc['hit_rate_10d']:.0f}%"
                f"（{acc['directional_count']}样本{high_info}）"
            )
    except Exception as exc:
        logger.debug("[injector] accuracy error: %r", exc)

    # 3. 当前模式匹配
    if scores:
        try:
            from knowledge.pattern_memory import match_current
            matched = match_current(scores)
            for m in matched[:2]:  # 最多展示 2 个
                if m.get("sample_count", 0) >= 3:
                    sections.append(
                        f"▸ 模式匹配：\"{m['description']}\"（{m['sample_count']}样本），"
                        f"10日胜率{m['win_rate_10d']:.0f}%，"
                        f"平均收益{m['avg_return_10d']:+.1f}%"
                    )
                elif m.get("sample_count", 0) >= 1:
                    sections.append(
                        f"▸ 模式匹配：\"{m['description']}\"（初步，仅{m['sample_count']}样本）"
                    )
        except Exception as exc:
            logger.debug("[injector] pattern error: %r", exc)

    # 4. 该股历史
    if stock_code:
        try:
            from knowledge.outcome_tracker import get_stock_history
            history = get_stock_history(stock_code)
            if history:
                recent = sorted(history, key=lambda x: x.get("report_date", ""), reverse=True)
                total = len(recent)
                bullish = [h for h in recent if h.get("direction") == "bullish"]
                hit_count = sum(1 for h in bullish if h.get("hit_10d"))
                last = recent[0]
                parts = [f"▸ 该股历史：过去{total}次分析"]
                if bullish:
                    parts.append(f"看多{len(bullish)}次（10日命中{hit_count}次）")
                parts.append(
                    f"上次{last.get('report_date', '?')}"
                    f"评{last.get('weighted_score', '?')}分"
                    f"实际10日{last.get('return_10d', 0):+.1f}%"
                )
                sections.append("，".join(parts))
        except Exception as exc:
            logger.debug("[injector] stock history error: %r", exc)

    # 5. 模型表现
    if model_name:
        try:
            from knowledge.analyst_scorecard import load_scorecard
            sc = load_scorecard()
            by_model = sc.get("by_model", {})
            model_stats = by_model.get(model_name, {})
            if model_stats.get("total", 0) >= 3:
                sections.append(
                    f"▸ 当前模型({_short_model(model_name)})："
                    f"10日胜率{model_stats['hit_rate_10d']:.0f}%"
                    f"（{model_stats['total']}样本）"
                )
        except Exception as exc:
            logger.debug("[injector] model stats error: %r", exc)

    if not sections:
        return ""

    # 拼装
    header = "【历史知识库参考】"
    footer = "⚠️ 以上为统计参考，样本量有限时仅供辅助判断"
    body = "\n".join(sections)

    full = f"{header}\n{body}\n{footer}"

    # 截断保护
    if len(full) > max_chars:
        # 优先保留前几个 section
        while len(full) > max_chars and sections:
            sections.pop()
            body = "\n".join(sections)
            full = f"{header}\n{body}\n{footer}"

    return full


def _short_model(model_name: str) -> str:
    """缩写模型名称。"""
    # "🟣 豆包 · Seed 2.0 Pro" → "豆包Pro"
    if "豆包" in model_name:
        if "Pro" in model_name:
            return "豆包Pro"
        return "豆包Mini"
    if "Qwen" in model_name or "千问" in model_name:
        return "Qwen"
    if "GLM" in model_name or "智谱" in model_name:
        return "GLM-5"
    if "DeepSeek" in model_name:
        return "DeepSeek"
    if "Gemini" in model_name:
        if "3" in model_name:
            return "Gemini3"
        return "Gemini2.5"
    if "GPT-5" in model_name:
        return "GPT-5.2"
    if "GPT-4" in model_name:
        return "GPT-4o"
    return model_name[:10]
