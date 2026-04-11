# -*- coding: utf-8 -*-
"""四野指挥部 — 多模型并行分析 + 刘亚楼汇总 + 林彪裁决

流程：
  Phase 0: 侦察科情报采集（一次）
  Phase 1: 三位将领并行全面分析（同一 prompt，独立出报告）
  Phase 2: 追加侦察（可选，根据将领需求）
  Phase 3: 参谋长刘亚楼汇总（标注共识/分歧/盲区）
  Phase 4: 司令员林彪独立判断（回看原始数据 + 裁决分歧）
  Phase 5: 上报毛主席（用户）
"""

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from services.decision_tree import load_tree, compute_weighted, apply_corrections, format_tree_for_prompt
from ai.prompts_analyst import build_round1_system, ROUND2_SYSTEM, build_round2_user, build_report_header

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

# ── 指挥部阵容预设 ──────────────────────────────────────────────
# 每个阵容定义 3 将领各自用什么模型 + 刘亚楼 + 林彪

WAR_ROOM_PRESETS = {
    # ── 新版：两轮深度分析 ──────────────────────────────────────
    "opus": {
        "label": "Opus 深度分析（两轮自我对话）",
        "analyst": "🧠 Claude Opus（MAX）",
    },
    "sonnet": {
        "label": "Sonnet 深度分析（速度优先）",
        "analyst": "⚡ Claude Sonnet（MAX）",
    },
    # ── 旧版：多将领模式（Top10/批量兼容）──────────────────────
    "balanced": {
        "label": "负载均衡阵容（Gemini+Codex将领，Claude Opus裁决）",
        "scouts": [
            "🔮 Gemini CLI（免费）",
            "🤖 Codex CLI（Plus）",
            "🔮 Gemini CLI（免费）",
        ],
        "commander": "🧠 Claude Opus（MAX）",
        "_legacy": True,
    },
    "max": {
        "label": "全 Claude MAX 阵容（Sonnet将领+Opus裁决）",
        "scouts": ["⚡ Claude Sonnet（MAX）"] * 3,
        "commander": "🧠 Claude Opus（MAX）",
        "_legacy": True,
    },
    "gemini": {
        "label": "全 Gemini 阵容（免费，单股可用，批量易限流）",
        "scouts": ["🔮 Gemini CLI（免费）"] * 3,
        "commander": "🔮 Gemini CLI（免费）",
        "_legacy": True,
    },
}
DEFAULT_PRESET = "opus"


@dataclass
class WarRoomResult:
    stock_name: str = ""
    stock_code: str = ""
    general_reports: list = field(default_factory=list)  # [{report_text, summary, scores}, ...]
    general_scores: list = field(default_factory=list)
    staff_brief: str = ""
    final_report: str = ""
    final_summary: str = ""
    final_scores: dict = field(default_factory=dict)
    combined_markdown: str = ""
    report_id: str = ""


def _call_single_model(prompt: str, system: str, model_name: str, max_tokens: int = 8000) -> str:
    """调用单个模型，返回完整文本。任何模型失败时自动降级到 Claude Sonnet。"""
    from ai.client import call_ai, call_ai_stream, get_ai_client

    client, cfg, err = get_ai_client(model_name)
    if err and not cfg:
        # 主模型不可用，直接尝试 Claude Sonnet 兜底
        logger.warning("[_call_single_model] %s 不可用（%s），尝试 Claude Sonnet 兜底", model_name, err)
        client, cfg, err = get_ai_client("⚡ Claude Sonnet（MAX）")
        if err and not cfg:
            return f"⚠️ 模型不可用且兜底失败：{err}"

    text = ""
    try:
        # CLI 模型走 call_ai_stream（内部会路由到 CLI provider）
        if cfg.get("provider") in ("gemini_cli", "codex_cli", "claude_cli"):
            stream = call_ai_stream(client, cfg, prompt, system=system, max_tokens=max_tokens)
            for _ in stream:
                pass
            text = stream.full_text
        else:
            # API 模型走 call_ai
            text, call_err = call_ai(client, cfg, prompt, system=system, max_tokens=max_tokens)
            if call_err:
                text = f"⚠️ 调用失败：{call_err}"
    except Exception as exc:
        text = f"⚠️ 异常：{exc}"

    # ★ 失败重试：输出为错误、过短、或AI拒绝回答时，重试一次
    _REFUSAL_PATTERNS = [
        "无法分析", "我无法", "作为AI", "作为一个AI", "I cannot", "I'm unable",
        "不能提供投资", "无法提供具体", "我没有能力", "不具备分析",
    ]
    is_refusal = any(p in text[:200] for p in _REFUSAL_PATTERNS)
    is_failure = "⚠️" in text[:20] or len(text.strip()) < 50 or is_refusal
    if is_refusal:
        logger.warning("[_call_single_model] %s 检测到拒绝回答模式", model_name)
    if is_failure:
        # Claude 模型：用同模型重试一次（不降级，保持评估质量）
        # 非 Claude 模型：用 Claude Sonnet 兜底
        is_claude = "claude" in model_name.lower()
        retry_name = model_name if is_claude else "⚡ Claude Sonnet（MAX）"
        logger.warning("[_call_single_model] %s 输出异常（%d字），重试: %s",
                       model_name, len(text.strip()), retry_name)
        try:
            fb_client, fb_cfg, fb_err = get_ai_client(retry_name)
            if fb_cfg:
                stream = call_ai_stream(fb_client, fb_cfg, prompt, system=system, max_tokens=max_tokens)
                for _ in stream:
                    pass
                if stream.full_text and len(stream.full_text.strip()) > 50:
                    text = stream.full_text
                else:
                    logger.error("[_call_single_model] 重试 %s 仍失败（%d字），放弃",
                                 retry_name, len((stream.full_text or "").strip()))
            else:
                logger.error("[_call_single_model] 重试模型 %s 不可用: %s", retry_name, fb_err)
        except Exception as exc:
            logger.error("[_call_single_model] 重试 %s 异常: %r", retry_name, exc)

    return text


def _fallback_scores_from_generals(general_reports: list, final_text: str = "") -> dict:
    """当林彪未输出 <<<SCORES>>> 块时，从将领评分加权推算最终分数。

    策略：
    1. 取3位将领各维度评分的中位数（防止单人极端值）
    2. 尝试从林彪文本中用正则提取裸分数（如 "基本面：72/100"）
    3. 用标准加权公式计算综合分
    """
    from services.analysis_service import apply_bucket_correction, SCORE_WEIGHTS
    import re

    dims = ["基本面", "预期差", "资金面", "技术面"]
    final_scores = {}

    # 策略1：从林彪文本中尝试正则提取分数（即使没有SCORES块）
    # 覆盖格式：基本面：72/100 | B档→75/100 | **基本面**：72 | 基本面 [0-100]：72
    if final_text:
        for dim in dims:
            m = re.search(
                rf"\*{{0,2}}{dim}\*{{0,2}}\s*(?:\[[\d-]+\])?\s*[：:]\s*(?:[A-E]档[→→]?)?\s*(\d+(?:\.\d+)?)\s*(?:/\s*100)?",
                final_text,
            )
            if m:
                val = float(m.group(1))
                if val <= 10:
                    val *= 10  # 旧10分制兼容
                final_scores[dim] = val

    # 策略2：不足的维度从将领评分取中位数补全
    for dim in dims:
        if dim in final_scores:
            continue
        vals = []
        for g in general_reports:
            s = g.get("scores", {})
            if dim in s and isinstance(s[dim], (int, float)):
                vals.append(s[dim])
        if vals:
            vals.sort()
            final_scores[dim] = vals[len(vals) // 2]  # 中位数

    # 计算综合加权
    if final_scores:
        weighted_sum = 0.0
        total_weight = 0.0
        for dim, weight in SCORE_WEIGHTS.items():
            if dim in final_scores:
                weighted_sum += final_scores[dim] * weight
                total_weight += weight
        if total_weight > 0:
            final_scores["综合加权"] = round(weighted_sum / total_weight, 1)

    # 尝试从林彪文本中提取操作评级
    if final_text:
        for rating in ["总攻信号", "侦察待命", "按兵不动", "全线撤退"]:
            if rating in final_text:
                final_scores["_ai_rating"] = rating
                break

    # 应用修正
    if final_scores:
        final_scores["_s_exempt"] = False
        final_scores["_has_fatal"] = False
        final_scores = apply_bucket_correction(final_scores)

    if not final_scores:
        logger.warning("[war_room] 所有评分源均失败，使用兜底默认分数")
        return {"综合加权": 50.0, "_rating": "按兵不动", "_all_fallback": True}
    return final_scores


def _apply_premortem_cap(scores: dict, final_text: str) -> dict:
    """Pre-mortem一致性检查：高概率致命风险→评分封顶。

    从林彪报告的Pre-mortem段中提取概率标签，与最终评分交叉验证。
    """
    # 提取Pre-mortem段落
    pm_match = re.search(r"(?:Pre-mortem|沙盘推演|验尸)(.*?)(?:Step\s*4|双轨评分|分歧裁决|### [四五])", final_text, re.DOTALL | re.IGNORECASE)
    if not pm_match:
        return scores

    pm_text = pm_match.group(1)

    # 统计概率标签
    high_count = len(re.findall(r"(?:当前)?(?:发生)?概率\s*[:：]?\s*高", pm_text))
    mid_count = len(re.findall(r"(?:当前)?(?:发生)?概率\s*[:：]?\s*中", pm_text))

    composite = scores.get("综合加权", 50)

    if high_count >= 1 and composite > 70:
        scores["综合加权"] = min(composite, 70.0)
        scores["_premortem_cap"] = f"高概率致命风险{high_count}项→综合上限70"
        if scores.get("_rating") in ("总攻信号", "侦察待命"):
            scores["_rating"] = "按兵不动"
        logger.info("[war_room] Pre-mortem封顶: 高概率%d项, 综合%.1f→70", high_count, composite)
    elif mid_count >= 2 and composite > 75:
        scores["综合加权"] = min(composite, 75.0)
        scores["_premortem_cap"] = f"中概率致命风险{mid_count}项→综合上限75"
        logger.info("[war_room] Pre-mortem封顶: 中概率%d项, 综合%.1f→75", mid_count, composite)

    return scores


def _parse_general_report(text: str) -> dict:
    """从将领报告中提取评分和摘要"""
    from services.analysis_service import parse_scores, _split_report_and_summary

    scores = parse_scores(text)
    summary, _ = _split_report_and_summary(text)

    # 提取追加侦察需求
    recon_match = re.search(r"【追加侦察需求】\s*\n(.+?)(?:\n\n|\Z)", text, re.DOTALL)
    recon_needs = recon_match.group(1).strip() if recon_match else ""
    if recon_needs.lower() in ("无", "无。", "none", ""):
        recon_needs = ""

    result_scores = scores or {}
    if not scores:
        # Fallback: 从文本中提取总分（如 "评分：8.6/10" 或 "综合评分 86"）
        total_match = re.search(r"(?:评分|打分|得分|总分)[：:]\s*\**(\d+(?:\.\d+)?)\s*(?:分)?\s*(?:/\s*(100|10))?\**", text)
        if total_match:
            val = float(total_match.group(1))
            scale = total_match.group(2)
            if scale == "10" or (scale is None and val <= 10):
                val = val * 10
            val = max(0.0, min(100.0, val))
            result_scores = {"基本面": val, "预期差": val, "资金面": val, "技术面": val}
            from services.analysis_service import SCORE_WEIGHTS
            weighted = sum(val * w for w in SCORE_WEIGHTS.values()) / sum(SCORE_WEIGHTS.values())
            result_scores["综合加权"] = round(weighted, 1)
            result_scores["_from_total"] = True
            logger.info("[war_room] 将领评分从总分推算: %.1f → 四维均分 %.1f", val, weighted)
        else:
            result_scores["_parse_failed"] = True
            logger.warning("[war_room] 将领评分解析失败，标记 _parse_failed")

    return {
        "report_text": text,
        "summary": summary,
        "scores": result_scores,
        "recon_needs": recon_needs,
    }


_SCORE_DIMS = ["基本面", "预期差", "资金面", "技术面"]


def _is_score_broken(scores: dict) -> bool:
    """评分是否缺失或异常：完全失败、综合加权缺失、或多维度缺失/为0（解析不全）"""
    if not scores or scores.get("_parse_failed") or scores.get("综合加权") is None:
        return True
    # 统计缺失维度（key不存在）和零值维度
    missing_count = sum(1 for d in _SCORE_DIMS if d not in scores)
    zero_count = sum(1 for d in _SCORE_DIMS if scores.get(d, 0) == 0)
    # 任何维度缺失即视为broken（之前要求>=3太宽松）
    return missing_count >= 1 or zero_count >= 3


def _build_score_extraction_prompt(report_text: str) -> str:
    """构建评分提取专用 prompt：从已完成的报告中提取/推断分数"""
    truncated = report_text[:4000]
    return f"""以下是一位分析师的完整报告，请从中提取或推断四维评分。

报告内容：
{truncated}

请严格按以下格式输出（仅输出此块，不要其他内容）：

<<<SCORES>>>
基本面: X/100
预期差: X/100
资金面: X/100
技术面: X/100
---
机会吸引力: X/100
逻辑置信度: X/100
立场: 推进/观察/否决
<<<END_SCORES>>>
"""


def _build_scores_table(general_reports: list) -> str:
    """构建三将领评分对比表"""
    dims = ["基本面", "预期差", "资金面", "技术面", "综合加权"]
    header = "| 维度 | " + " | ".join(f"将领{chr(65+i)}" for i in range(len(general_reports))) + " |"
    sep = "|------|" + "|".join("------" for _ in general_reports) + "|"
    rows = [header, sep]
    for dim in dims:
        vals = []
        for g in general_reports:
            v = g["scores"].get(dim, "?")
            vals.append(str(v))
        rows.append(f"| {dim} | " + " | ".join(vals) + " |")
    return "\n".join(rows)


def _extract_common_recon_needs(general_reports: list) -> list[str]:
    """提取≥2位将领共同提出的侦察需求"""
    all_needs = []
    for g in general_reports:
        needs = g.get("recon_needs", "")
        if needs:
            # 按行拆分
            for line in needs.split("\n"):
                line = re.sub(r"^\d+[\.\)、]\s*", "", line.strip())
                if line:
                    all_needs.append(line)

    if len(all_needs) < 2:
        return []

    # 简单去重：找关键词重叠度高的需求
    # 这里用简化逻辑：如果 ≥2 条需求有共同关键词，则视为共性需求
    return list(set(all_needs))[:3]  # 最多3条


def run_war_room(
    stock_name: str,
    username: str = "cli",
    preset: str = DEFAULT_PRESET,
    skip_extra_recon: bool = False,
    time_lock: str = "",            # NEW: data cutoff date YYYYMMDD
    skip_report_save: bool = False,  # NEW: don't write to reports.db
) -> WarRoomResult:
    """Main entry point for stock analysis.

    New presets (opus/sonnet) use 2-round deep analysis.
    Legacy presets (balanced/max/gemini) use old multi-general flow.
    """
    cfg = WAR_ROOM_PRESETS.get(preset)
    if not cfg:
        logger.error("Unknown preset: %s", preset)
        return WarRoomResult(stock_name=stock_name)

    if cfg.get("_legacy"):
        return _run_war_room_legacy(stock_name, username, preset, skip_extra_recon)

    return _run_war_room_v2(stock_name, username, cfg, time_lock=time_lock, skip_report_save=skip_report_save)


def _run_war_room_v2(
    stock_name: str,
    username: str,
    preset_cfg: dict,
    time_lock: str = "",
    skip_report_save: bool = False,
) -> WarRoomResult:
    """2-round Opus deep analysis flow."""
    from data.indicators import compute_indicators, format_indicators_section
    from data.report_data import build_report_context
    from data.tushare_client import resolve_stock
    from knowledge.injector import build_knowledge_context
    from repositories.report_repo import init_db, save_report
    from services.analysis_service import apply_bucket_correction, parse_scores

    report_id = str(uuid.uuid4())
    analyst_model = preset_cfg["analyst"]
    CLAUDE_FALLBACK = "⚡ Claude Sonnet（MAX）"

    # ── Phase 0: Scout (data collection) ────────────────────────
    logger.info("[war_room_v2] Phase 0: 侦察 — %s", stock_name)

    ts_code, resolved_name, resolve_warn = resolve_stock(stock_name)
    if not ts_code:
        raise ValueError(f"未识别到股票：{stock_name}")

    from data.macro_intel import get_macro_context
    context, raw_data = build_report_context(ts_code, resolved_name, time_lock=time_lock)
    price_df = raw_data.get("_price_df")
    indicators = compute_indicators(price_df) if price_df is not None and not price_df.empty else {}
    indicators_section = format_indicators_section(indicators)
    snap = context.get("price_summary", "")
    knowledge_ctx = build_knowledge_context(
        stock_code=ts_code, stock_name=resolved_name, model_name=analyst_model,
        price_snapshot=snap, indicators=indicators, time_lock=time_lock,
    )

    if not time_lock:
        # 舆情并行采集
        _sentiment_result = [None]
        import threading
        def _sentiment_worker():
            try:
                from data.stock_sentiment import fetch_stock_sentiment
                _sentiment_result[0] = fetch_stock_sentiment(ts_code=ts_code, stock_name=resolved_name)
            except Exception as exc:
                logger.debug("[war_room_v2] sentiment worker failed: %s", exc)

        sentiment_thread = threading.Thread(target=_sentiment_worker, daemon=True)
        sentiment_thread.start()

        # 等待舆情
        sentiment_ctx = ""
        if sentiment_thread.is_alive():
            sentiment_thread.join(timeout=30)
        if _sentiment_result[0]:
            try:
                from data.stock_sentiment import format_sentiment_for_prompt
                sentiment_ctx = format_sentiment_for_prompt(_sentiment_result[0])
            except Exception:
                pass
        if not sentiment_ctx or sentiment_ctx.strip() == "【雪球舆情参考】":
            sentiment_ctx = "（舆情数据采集失败或不足，请以基本面和技术面为主要判断依据）"

        macro_full, macro_brief = "", ""
        try:
            macro_full, macro_brief = get_macro_context()
        except Exception:
            pass
        if not macro_brief:
            macro_brief = "（宏观数据采集失败，请以个股基本面为主要判断依据）"
    else:
        sentiment_ctx = "（回测模式：舆情数据不注入）"
        macro_brief = "（回测模式：宏观数据不注入）"

    # 构建数据包（复用现有函数）
    from ai.prompts_report import build_war_room_prompts
    full_data_brief, _system_prompt, _output_formats = build_war_room_prompts(
        name=resolved_name,
        ts_code=ts_code,
        context=context,
        price_snapshot=snap,
        indicators_section=indicators_section,
        knowledge_context=knowledge_ctx,
        sentiment_context=sentiment_ctx,
        macro_context=macro_brief,
    )

    # 注入每日大盘深度分析（当日首次触发 Opus 分析，后续缓存）
    market_md = ""
    if not time_lock:
        from services.market_analysis import get_or_run_market_analysis
        market_md = get_or_run_market_analysis(analyst_model)
    if market_md:
        full_data_brief = market_md + "\n\n" + full_data_brief

    # 注入进化经验
    experience_text = _get_experience_lessons(ts_code, resolved_name)

    # 加载决策树
    tree = load_tree()
    tree_text = format_tree_for_prompt(tree["trees"])

    # ── Phase 1: Round 1 — Deep Analysis ────────────────────────
    logger.info("[war_room_v2] Phase 1: Round 1 deep analysis with %s", analyst_model)
    round1_system = build_round1_system(tree_text, experience_text)
    round1_text = _call_single_model(full_data_brief, round1_system, analyst_model, max_tokens=8000)

    # Parse scores
    round1_scores = parse_scores(round1_text, tree["weights"])
    if not round1_scores or round1_scores.get("_parse_failed"):
        logger.warning("[war_room_v2] Round 1 score parse failed, retrying with Sonnet")
        round1_text = _call_single_model(full_data_brief, round1_system, CLAUDE_FALLBACK, max_tokens=8000)
        round1_scores = parse_scores(round1_text, tree["weights"])

    if not round1_scores:
        round1_scores = {"基本面": 50, "预期差": 50, "资金面": 50, "技术面": 50, "综合加权": 50.0, "_parse_failed": True}

    # ── Phase 2: Round 2 — Self-Critique ────────────────────────
    logger.info("[war_room_v2] Phase 2: Round 2 self-critique with %s", analyst_model)
    round2_user = build_round2_user(round1_text)
    round2_text = _call_single_model(round2_user, ROUND2_SYSTEM, analyst_model, max_tokens=6000)

    # Parse score corrections
    final_scores = _apply_round2_corrections(round1_scores, round2_text, tree)

    # Apply code-level corrections
    final_scores = apply_corrections(
        final_scores,
        tree["correction_rules"],
        high_prob_fatal_count=_extract_fatal_count(round2_text),
    )
    # Rename _final to 综合加权 if decision_tree module uses _final
    if "_final" in final_scores and "综合加权" not in final_scores:
        final_scores["综合加权"] = final_scores.pop("_final")

    # Generate rating
    final_scores = apply_bucket_correction(final_scores)

    # ── Phase 3: Assemble Report ────────────────────────────────
    logger.info("[war_room_v2] Phase 3: 组装报告")
    combined_md = _build_v2_report(resolved_name, round1_text, round2_text, final_scores)

    if not skip_report_save:
        try:
            init_db()
            save_report(
                report_id=report_id,
                openid="war_room",
                stock_name=resolved_name,
                stock_code=ts_code,
                summary=final_scores.get("_rating", "分析完成"),
                markdown_text=combined_md,
            )
        except Exception as exc:
            logger.error("[war_room_v2] save_report failed: %r", exc)

    if not skip_report_save:
        _save_v2_tracker(report_id, resolved_name, ts_code, round1_scores, final_scores, round1_text, round2_text)

    result = WarRoomResult(
        stock_name=resolved_name,
        stock_code=ts_code,
        general_reports=[{"report_text": round1_text, "scores": round1_scores}],
        final_report=round2_text,
        final_summary=final_scores.get("_rating", ""),
        final_scores=final_scores,
        combined_markdown=combined_md,
        report_id=report_id,
    )
    logger.info("[war_room_v2] Done: %s score=%s", resolved_name, final_scores.get("综合加权", "?"))

    return result


def _get_experience_lessons(ts_code: str, stock_name: str) -> str:
    """Retrieve relevant lessons from experience DB."""
    try:
        from knowledge.experience_db import retrieve_lessons
        return retrieve_lessons(ts_code, stock_name)
    except Exception as e:
        logger.warning("[war_room_v2] experience retrieval failed: %s", e)
        return ""


def _apply_round2_corrections(round1_scores: dict, round2_text: str, tree: dict) -> dict:
    """Parse Round 2 score corrections and apply to Round 1 scores."""
    scores = dict(round1_scores)
    # Remove internal flags from Round 1 that shouldn't carry over
    scores = {k: v for k, v in scores.items() if not k.startswith("_")}

    m = re.search(
        r"<<<SCORE_CORRECTIONS>>>(.*?)<<<END_SCORE_CORRECTIONS>>>",
        round2_text, re.DOTALL
    )
    if not m:
        logger.warning("[war_room_v2] No SCORE_CORRECTIONS block in Round 2")
        scores["综合加权"] = compute_weighted(scores, tree["weights"])
        return scores

    block = m.group(1)
    for dim in ("基本面", "预期差", "资金面", "技术面"):
        pat = re.compile(rf"{dim}:\s*([+-]?\d+)\s*分")
        dm = pat.search(block)
        if dm:
            correction = int(dm.group(1))
            correction = max(-10, min(10, correction))
            scores[dim] = max(0, min(100, scores.get(dim, 50) + correction))

    scores["综合加权"] = compute_weighted(scores, tree["weights"])
    return scores


def _extract_fatal_count(round2_text: str) -> int:
    """Extract high-probability fatal count from Round 2."""
    m = re.search(
        r"<<<HIGH_PROB_FATAL_COUNT>>>\s*(\d+)\s*<<<END_HIGH_PROB_FATAL_COUNT>>>",
        round2_text
    )
    if m:
        return int(m.group(1))
    # Fallback: count "高" probability in Pre-mortem section
    premortem = round2_text.split("Pre-mortem")[-1] if "Pre-mortem" in round2_text else ""
    return len(re.findall(r"概率[：:]\s*高", premortem))


def _strip_markers(text: str) -> str:
    """Remove parsing markers and noise from AI output before display."""
    # Remove structured blocks that are already extracted by code
    text = re.sub(r"<<<SCORES>>>.*?<<<END_SCORES>>>", "", text, flags=re.DOTALL)
    text = re.sub(r"<<<SCORE_CORRECTIONS>>>.*?<<<END_SCORE_CORRECTIONS>>>", "", text, flags=re.DOTALL)
    text = re.sub(r"<<<HIGH_PROB_FATAL_COUNT>>>.*?<<<END_HIGH_PROB_FATAL_COUNT>>>", "", text, flags=re.DOTALL)
    # Remove remaining bare markers
    text = re.sub(r"<<<\w+>>>", "", text)
    # Remove standalone --- separator lines (AI-generated noise)
    text = re.sub(r"\n-{3,}\n", "\n\n", text)
    # Clean up excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _build_v2_report(stock_name: str, round1_text: str, round2_text: str, final_scores: dict) -> str:
    """Assemble the final v2 report markdown."""
    header = build_report_header(stock_name, final_scores)
    dims = ("基本面", "预期差", "资金面", "技术面")
    score_line = " | ".join(f"{d}: {final_scores.get(d, 50):.0f}" for d in dims)
    composite = final_scores.get("综合加权", 50)
    rating = final_scores.get("_rating", "按兵不动")

    clean_r1 = _strip_markers(round1_text)
    clean_r2 = _strip_markers(round2_text)

    return f"""{header}

**四维评分**：{score_line}
**综合加权**：{composite:.0f} — {rating}

---

## 深度分析（Round 1）

{clean_r1}

---

## 魔鬼代言人质疑（Round 2）

{clean_r2}
"""


def _save_v2_tracker(
    report_id: str, stock_name: str, ts_code: str,
    round1_scores: dict, final_scores: dict,
    round1_text: str, round2_text: str,
):
    """Save v2 tracker entry for evolution engine."""
    import datetime as _dt
    tracker_path = BASE_DIR / "data" / "knowledge" / "war_room_tracker.jsonl"
    dims = ("基本面", "预期差", "资金面", "技术面")

    # Extract tree paths from Round 1 text
    tree_paths = {}
    for dim in dims:
        pat = re.compile(rf"{dim}.*?决策树路径[：:]\s*(.+?)(?:\n|$)", re.MULTILINE)
        m = pat.search(round1_text)
        if m:
            tree_paths[dim] = m.group(1).strip()

    entry = {
        "report_id": report_id,
        "stock_name": stock_name,
        "ts_code": ts_code,
        "timestamp": _dt.datetime.now().isoformat(),
        "version": "v2",
        "round1_scores": {d: round1_scores.get(d) for d in list(dims) + ["综合加权"]},
        "final_scores": {d: final_scores.get(d) for d in list(dims) + ["综合加权"]},
        "rating": final_scores.get("_rating", ""),
        "tree_paths": tree_paths,
    }

    try:
        with open(tracker_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("[war_room_v2] tracker save failed: %r", exc)


def _run_war_room_legacy(
    stock_name: str,
    username: str = "cli",
    preset: str = "balanced",
    skip_extra_recon: bool = False,
) -> WarRoomResult:
    """Legacy multi-general war room flow. Used by old presets (balanced/max/gemini)."""
    from ai.prompts_report import build_report_prompt, build_war_room_prompts
    from ai.prompts_war_room import (
        GENERAL_PERSONALITIES,
        GENERAL_SCOUT_SUFFIX,
        build_lin_biao_prompt,
    )
    from data.indicators import compute_indicators, format_indicators_section
    from data.report_data import build_report_context
    from data.tushare_client import get_price_df, price_summary, resolve_stock
    from knowledge.injector import build_knowledge_context
    from repositories.report_repo import init_db, save_report
    from services.analysis_service import apply_bucket_correction, parse_scores

    # ── 解析阵容预设 ─────────────────────────────────────────────
    cfg_preset = WAR_ROOM_PRESETS.get(preset, WAR_ROOM_PRESETS[DEFAULT_PRESET])
    scout_models = cfg_preset["scouts"]
    commander_model = cfg_preset["commander"]
    num_generals = len(scout_models)
    logger.info("[war_room] 阵容: %s — 将领%s, 林彪%s",
                cfg_preset["label"], scout_models, commander_model)

    # ── Phase 0: 侦察科情报采集 ─────────────────────────────────
    logger.info("[war_room] Phase 0: 侦察科情报采集 — %s", stock_name)

    ts_code, resolved_name, resolve_warn = resolve_stock(stock_name)
    if not ts_code:
        raise ValueError(f"未识别到股票：{stock_name}")

    # 舆情+宏观情报并行启动
    _sentiment_result = [None]
    _macro_result = [None]

    def _sentiment_worker():
        try:
            from data.stock_sentiment import fetch_stock_sentiment
            _sentiment_result[0] = fetch_stock_sentiment(ts_code=ts_code, stock_name=resolved_name)
        except Exception as exc:
            logger.debug("[war_room] sentiment worker failed: %s", exc)

    import threading
    sentiment_thread = threading.Thread(target=_sentiment_worker, daemon=True)
    sentiment_thread.start()

    # 宏观情报：全局当日缓存，首次采集后所有股票共享（不开线程，直接取）
    from data.macro_intel import get_macro_context

    context, raw_data = build_report_context(ts_code, resolved_name)
    price_df = raw_data.get("_price_df")
    indicators = compute_indicators(price_df) if price_df is not None and not price_df.empty else {}
    indicators_section = format_indicators_section(indicators)
    snap = context.get("price_summary", "")
    knowledge_ctx = build_knowledge_context(
        stock_code=ts_code, stock_name=resolved_name, model_name=scout_models[0],
        price_snapshot=snap, indicators=indicators,
    )

    # 等待舆情完成（最多 30s）
    sentiment_ctx = ""
    if sentiment_thread.is_alive():
        sentiment_thread.join(timeout=30)
    if _sentiment_result[0]:
        try:
            from data.stock_sentiment import format_sentiment_for_prompt
            sentiment_ctx = format_sentiment_for_prompt(_sentiment_result[0])
        except Exception as exc:
            logger.debug("[war_room] sentiment format failed: %s", exc)
    if not sentiment_ctx or sentiment_ctx.strip() == "【雪球舆情参考】":
        sentiment_ctx = "（舆情数据采集失败或不足，请以基本面和技术面为主要判断依据）"

    # 宏观情报（全局缓存，首次采集约5s，后续瞬间返回）
    macro_full = ""   # 完整版：给林彪小本本
    macro_brief = ""  # 精简版：给将领prompt（节省token）
    try:
        macro_full, macro_brief = get_macro_context()
    except Exception as exc:
        logger.debug("[war_room] macro context failed: %s", exc)
    if not macro_brief:
        macro_brief = "（宏观数据采集失败，请以个股基本面为主要判断依据）"

    # 构建全维度 prompt：统一全量数据 + 风格化输出格式（v4.0）
    full_data_brief, system_prompt, output_formats = build_war_room_prompts(
        name=resolved_name,
        ts_code=ts_code,
        context=context,
        price_snapshot=snap,
        indicators_section=indicators_section,
        knowledge_context=knowledge_ctx,
        sentiment_context=sentiment_ctx,
        macro_context=macro_brief,
    )

    # ── Phase 1: 三位将领分析 ──────────────────────────────────
    # 全Claude阵容（claude_cli）串行调用避免MAX并发限制；其他模型可并行
    from config import MODEL_CONFIGS
    all_claude = all(
        MODEL_CONFIGS.get(m, {}).get("provider") == "claude_cli"
        for m in scout_models
    )
    if all_claude:
        logger.info("[war_room] Phase 1: %d 位将领串行分析（全Claude，避免并发限制）", num_generals)
    else:
        logger.info("[war_room] Phase 1: %d 位将领并行分析", num_generals)

    general_reports = []
    CLAUDE_FALLBACK = "⚡ Claude Sonnet（MAX）"

    def _run_general(i, model):
        # v4.0: system = 角色设定 + 将领人设 + 输出模板
        #        stdin = 统一全量数据（三人相同）
        full_system = (
            system_prompt
            + GENERAL_PERSONALITIES[i % len(GENERAL_PERSONALITIES)]
            + "\n\n" + output_formats[i % len(output_formats)]
        )
        stdin_prompt = full_data_brief

        logger.info("[war_room] 将领%s stdin %d字 / system %d字",
                    chr(65 + i), len(stdin_prompt), len(full_system))

        text = _call_single_model(stdin_prompt, full_system, model)
        parsed = _parse_general_report(text)

        # ★ 三级重试：确保评分不缺失
        # 第1级：原模型输出 → 解析
        if _is_score_broken(parsed["scores"]) or "⚠️" in text[:20]:
            # 第2级：Claude Sonnet 兜底（同 prompt）
            logger.warning("[war_room] 将领%s（%s）评分缺失，Claude Sonnet 兜底...",
                           chr(65 + i), model)
            text = _call_single_model(stdin_prompt, full_system, CLAUDE_FALLBACK)
            parsed = _parse_general_report(text)

        if _is_score_broken(parsed["scores"]):
            # 第3级：评分提取专用 prompt（从报告原文推断分数）
            logger.warning("[war_room] 将领%s 二次评分缺失，发送评分提取请求...", chr(65 + i))
            from services.analysis_service import parse_scores
            score_text = _call_single_model(
                _build_score_extraction_prompt(text),
                "你是评分提取助手。从分析报告中提取四维评分，严格按格式输出。",
                CLAUDE_FALLBACK,
            )
            extracted = parse_scores(score_text)
            if extracted and not _is_score_broken(extracted):
                parsed["scores"] = extracted
                parsed["scores"]["_extracted_retry"] = True
                logger.info("[war_room] 将领%s 评分提取成功: %s", chr(65 + i), extracted.get("综合加权"))

        score_str = parsed["scores"].get("综合加权", "?")
        logger.info("[war_room] 将领%s 完成，综合分: %s", chr(65 + i), score_str)
        return parsed

    if all_claude:
        # 串行：一个一个调，避免 MAX 并发限制
        for i, model in enumerate(scout_models):
            general_reports.append(_run_general(i, model))
    else:
        # 并行：不同模型（Gemini/Codex）不受限
        with ThreadPoolExecutor(max_workers=num_generals) as pool:
            futures = [pool.submit(_run_general, i, model) for i, model in enumerate(scout_models)]
            for fut in futures:
                general_reports.append(fut.result())

    # ── Phase 1 审查：将领评分缺失/异常则替补保护（最后防线）────────
    # 先收集所有将领的各维度评分，用于后续精准补全
    _all_dim_vals = {d: [] for d in _SCORE_DIMS}
    for g in general_reports:
        for d in _SCORE_DIMS:
            v = g["scores"].get(d)
            if isinstance(v, (int, float)) and v > 0:
                _all_dim_vals[d].append(v)

    for i, g in enumerate(general_reports):
        if _is_score_broken(g["scores"]):
            # 精准补全：仅填充缺失维度，保留已有维度的原始评分
            missing_dims = [d for d in _SCORE_DIMS if d not in g["scores"] or g["scores"].get(d, 0) == 0]
            has_any_valid = any(d in g["scores"] and isinstance(g["scores"].get(d), (int, float)) and g["scores"][d] > 0
                                for d in _SCORE_DIMS)

            if has_any_valid and missing_dims:
                # 部分缺失：从其他将领取该维度中位数补全
                for d in missing_dims:
                    vals = _all_dim_vals.get(d, [])
                    if vals:
                        median_val = sorted(vals)[len(vals) // 2]
                        g["scores"][d] = median_val
                        logger.info("[war_room] Phase1审查：将领%s %s缺失，用其他将领中位数%.1f补全",
                                    chr(65+i), d, median_val)
                    else:
                        g["scores"][d] = 50.0
                        logger.info("[war_room] Phase1审查：将领%s %s缺失且无参考，默认50", chr(65+i), d)
                g["scores"]["_partial_filled"] = True
            else:
                # 全部缺失：整体替补
                valid_scores = [gg["scores"].get("综合加权") for gg in general_reports
                                if gg["scores"] and not _is_score_broken(gg["scores"])]
                if valid_scores:
                    median_val = sorted(valid_scores)[len(valid_scores) // 2]
                    logger.warning("[war_room] Phase1审查：将领%s评分全缺(%s)，用中位数%.1f替补",
                                   chr(65+i), g["scores"].get("综合加权", "?"), median_val)
                    g["scores"] = {"基本面": median_val, "预期差": median_val,
                                   "资金面": median_val, "技术面": median_val,
                                   "综合加权": median_val, "_substituted": True}
                else:
                    logger.warning("[war_room] Phase1审查：将领%s评分全缺且无可用替补，默认50分", chr(65+i))
                    g["scores"] = {"基本面": 50, "预期差": 50, "资金面": 50, "技术面": 50,
                                   "综合加权": 50, "_substituted": True}

            # 重算综合加权
            from services.analysis_service import SCORE_WEIGHTS
            ws = sum(g["scores"].get(d, 50) * SCORE_WEIGHTS[d] for d in _SCORE_DIMS)
            tw = sum(SCORE_WEIGHTS[d] for d in _SCORE_DIMS if d in g["scores"])
            if tw > 0:
                g["scores"]["综合加权"] = round(ws / tw, 1)

    # ── 提前止损检查：三将领全线撤退则跳过后续 ────────────────
    all_weighted = [g["scores"].get("综合加权", 50) for g in general_reports if g["scores"]]
    early_exit = len(all_weighted) >= 2 and all(w < 35 for w in all_weighted)
    if early_exit:
        logger.info("[war_room] 提前止损：三将领综合分均<35 (%s)，跳过Phase 2-4", all_weighted)

    # ── Phase 1.5: 韩先楚交叉质疑（一票否决权）────────────────
    han_veto = False
    han_veto_reason = ""
    if not early_exit and len(general_reports) >= 2:
        try:
            from ai.prompts_war_room import build_han_veto_prompt
            # 取将领 A（黄永胜）和 C（邓华）的摘要给韩先楚审查
            ac_brief = ""
            general_names_ac = ["A·黄永胜(进攻型)", "C·邓华(均衡型)"]
            for idx in [0, 2]:
                if idx < len(general_reports):
                    g = general_reports[idx]
                    label = general_names_ac[0 if idx == 0 else 1]
                    # v4.0: 传入更完整的摘要（1500字），支持逻辑质疑
                    summary = g.get('summary', '无')[:1500]
                    s = g['scores']
                    dims_str = (f"基{s.get('基本面', '?')}/期{s.get('预期差', '?')}/"
                                f"资{s.get('资金面', '?')}/技{s.get('技术面', '?')}")
                    ac_brief += f"【{label}】综合{s.get('综合加权', '?')}分({dims_str})：{summary}\n\n"

            han_user, han_system = build_han_veto_prompt(ac_brief, resolved_name)
            # 韩先楚用将领B的模型（如果有专门的侦察模型则更好）
            han_model = scout_models[1] if len(scout_models) > 1 else scout_models[0]
            logger.info("[war_room] Phase 1.5: 韩先楚交叉质疑（%s）", han_model)
            han_text = _call_single_model(han_user, han_system, han_model)

            if han_text:
                # 解析否决决策（re 已在文件顶部导入）
                veto_match = re.search(r"<<<VETO_DECISION>>>(.*?)<<<END_VETO_DECISION>>>", han_text, re.DOTALL)
                if veto_match:
                    veto_block = veto_match.group(1)
                    veto_val_match = re.search(r"是否否决\s*[:：]\s*(是|否)", veto_block)
                    is_veto = veto_val_match.group(1) == "是" if veto_val_match else False
                    conf_match = re.search(r"置信度[:：]\s*(\d+)", veto_block)
                    confidence = int(conf_match.group(1)) if conf_match else 0

                    if is_veto and confidence >= 80:
                        han_veto = True
                        han_veto_reason = han_text
                        logger.info("[war_room] Phase 1.5: 韩先楚一票否决！置信度 %d%%", confidence)
                elif "<<<VETO>>>" in han_text:
                    # <<<VETO>>> 裸标记安全解析：排除代码块、引用块、格式示例中的误触发
                    veto_pos = han_text.index("<<<VETO>>>")
                    # 检查标记前50字符是否在代码块或引用块中
                    context_before = han_text[max(0, veto_pos - 80):veto_pos]
                    in_code_block = "```" in context_before or context_before.strip().startswith(">")
                    in_example = any(kw in context_before.lower() for kw in ["格式", "示例", "example", "format", "输出格式"])

                    if in_code_block or in_example:
                        logger.info("[war_room] Phase 1.5: <<<VETO>>>出现在代码块/示例中，忽略")
                    else:
                        veto_keywords = ["否决", "一票否决", "致命", "重大风险", "全线撤退"]
                        has_veto_intent = any(kw in han_text[:500] for kw in veto_keywords)
                        if has_veto_intent:
                            han_veto = True
                            han_veto_reason = han_text
                            logger.info("[war_room] Phase 1.5: 韩先楚一票否决（<<<VETO>>>标记+否决意图确认）")
                        else:
                            logger.info("[war_room] Phase 1.5: 韩先楚输出含<<<VETO>>>但无明确否决意图，忽略")

                # 韩先楚的反驳注入将领报告（无论是否否决）
                if len(general_reports) > 1:
                    general_reports[1]["han_veto_review"] = han_text
                    logger.info("[war_room] Phase 1.5: 韩先楚审查完成 (%d chars, veto=%s)", len(han_text), han_veto)
        except Exception as exc:
            logger.warning("[war_room] Phase 1.5 veto check failed: %r", exc)

    # 韩先楚一票否决：短路后续流程
    if han_veto:
        early_exit = True
        all_weighted = [g["scores"].get("综合加权", 50) for g in general_reports if g["scores"]]
        logger.info("[war_room] 韩先楚一票否决生效，跳过 Phase 2-4")

    # ── Phase 2: 追加侦察（可选，批量模式下跳过以节省时间）───────
    extra_intel = ""
    if early_exit or skip_extra_recon:
        logger.info("[war_room] Phase 2: 跳过%s", "（提前止损）" if early_exit else "（批量模式）")
    else:
        common_needs = _extract_common_recon_needs(general_reports)
        if common_needs:
            logger.info("[war_room] Phase 2: 追加侦察 — %d 条共性需求", len(common_needs))
            search_prompt = f"请搜索以下关于{resolved_name}的信息：\n" + "\n".join(f"- {n}" for n in common_needs)
            extra_intel = _call_single_model(search_prompt, "你是情报员，简洁回答，每条不超过100字。", scout_models[0])
        else:
            logger.info("[war_room] Phase 2: 无追加侦察需求，跳过")

    # ── Phase 3: 跳过刘亚楼（评分对比表由代码生成，林彪直接裁决）─
    scores_table = _build_scores_table(general_reports)

    # 将领报告摘要（结构化格式，节省token）
    general_names = ["A·黄永胜(进攻型)", "B·韩先楚(防守型)", "C·邓华(均衡型)"]
    generals_brief_parts = []
    for i, g in enumerate(general_reports):
        label = general_names[i] if i < len(general_names) else f"将领{chr(65+i)}"
        s = g["scores"]
        summary = g.get("summary", "无")
        # 结构化：评分+立场+核心判断，比自由文本节省~40% token
        stance = s.get("_stance", s.get("_ai_rating", ""))
        stance_str = f" 立场:{stance}" if stance else ""
        dims_str = (f"基{s.get('基本面', '?')}/期{s.get('预期差', '?')}/"
                    f"资{s.get('资金面', '?')}/技{s.get('技术面', '?')}")
        # 摘要截断到200字（原来不限）
        brief_summary = summary[:200] + ("…" if len(summary) > 200 else "")
        generals_brief_parts.append(
            f"【{label}】综合{s.get('综合加权', '?')}分({dims_str}){stance_str}\n{brief_summary}"
        )
    generals_brief = "\n".join(generals_brief_parts)

    # ★ 韩先楚交叉审查结果注入（非否决时的反驳意见供林彪参考）
    if not han_veto and len(general_reports) > 1:
        han_review = general_reports[1].get("han_veto_review", "")
        if han_review:
            generals_brief += f"\n\n【韩先楚交叉质疑】\n{han_review[:800]}"

    # ★ 追加侦察情报注入将领摘要（修复：原来 extra_intel 未传给林彪）
    if extra_intel and extra_intel.strip():
        generals_brief += f"\n\n【追加侦察情报】\n{extra_intel.strip()}"

    # ── Phase 3.5: Bull vs Bear 辩论（看多方 vs 看空方反驳）─────
    debate_text = ""
    if not early_exit and len(general_reports) >= 2:
        try:
            debate_text = _run_bull_bear_debate(general_reports, generals_brief, scout_models[0])
            if debate_text:
                logger.info("[war_room] Phase 3.5: Bull vs Bear 辩论完成 (%d chars)", len(debate_text))
        except Exception as exc:
            logger.warning("[war_room] Phase 3.5 debate failed: %r", exc)

    # ── Phase 4: 司令员林彪独立判断 ─────────────────────────────
    # v4.0: 林彪收到与将领完全一致的全量数据（真正独立初判）
    data_summary = full_data_brief
    if macro_full and macro_full not in data_summary:
        data_summary = macro_full + "\n\n" + data_summary

    final_text = ""
    final_scores = None
    final_summary = ""

    if early_exit:
        logger.info("[war_room] Phase 4: 跳过（%s）", "韩先楚一票否决" if han_veto else "提前止损")
        avg_score = round(sum(all_weighted) / len(all_weighted), 1) if all_weighted else 20

        if han_veto:
            # 韩先楚一票否决：特殊报告
            final_scores = {
                "基本面": avg_score, "预期差": avg_score,
                "资金面": avg_score, "技术面": avg_score,
                "综合加权": min(avg_score, 25), "_rating": "全线撤退",
                "_han_veto": True,
            }
            final_text = (
                f"## 韩先楚一票否决\n\n"
                f"韩先楚（B·侦察将领）发现致命风险，行使一票否决权，全线撤退。\n\n"
                f"### 否决理由\n\n{han_veto_reason[:1500]}\n\n"
                f"综合评分：{min(avg_score, 25)}分（全线撤退·韩先楚否决）"
            )
            final_summary = f"{resolved_name} 韩先楚一票否决(致命风险)，综合{min(avg_score, 25)}分，全线撤退。"
        else:
            # 三将领全线撤退
            final_scores = {
                "基本面": avg_score, "预期差": avg_score,
                "资金面": avg_score, "技术面": avg_score,
                "综合加权": avg_score, "_rating": "全线撤退",
            }
            final_text = (
                f"## 提前止损\n\n"
                f"三位将领综合评分均低于35分（{', '.join(f'{w:.0f}' for w in all_weighted)}），"
                f"战场凶险，无需刘亚楼汇总和林彪裁决，直接全线撤退。\n\n"
                f"综合评分：{avg_score}分（全线撤退）"
            )
            final_summary = f"{resolved_name} 综合{avg_score}分(全线撤退)，三将领一致看空，跳过后续决策。"
    else:
        logger.info("[war_room] Phase 4: 司令员林彪独立判断（%s）", commander_model)

        # ★ 近期评分分布（相对锚定）
        score_distribution_hint = ""
        try:
            from knowledge.outcome_tracker import get_recent_scores_distribution
            dist = get_recent_scores_distribution(limit=20)
            if dist:
                score_distribution_hint = (
                    f"\n\n【近期评分分布参考（最近{dist['count']}次分析）】\n"
                    f"中位数: {dist['median']:.0f}分 | 均值: {dist['mean']:.0f}分 | "
                    f"25%分位: {dist['p25']:.0f}分 | 75%分位: {dist['p75']:.0f}分\n"
                    f"⚠️ 若你认为此股优于近期平均水平，评分应显著高于中位数{dist['median']:.0f}分；"
                    f"反之亦然。避免所有股票都给出相近的安全分。"
                )
        except Exception as exc:
            logger.debug("[war_room] score distribution hint failed: %r", exc)

        # 辩论结果注入林彪 prompt（如果有）
        debate_section = ""
        if debate_text:
            debate_section = f"\n\n【Bull vs Bear 辩论记录】\n{debate_text[:1000]}"

        lin_user, lin_system = build_lin_biao_prompt(
            staff_brief=generals_brief + debate_section + score_distribution_hint,
            data_summary=data_summary,
            scores_table=scores_table,
            knowledge_context=knowledge_ctx,
        )
        final_text = _call_single_model(lin_user, lin_system, commander_model, max_tokens=12000)

        # ★ Claude兜底：commander 输出为错误时，用 Claude Sonnet 重试
        if "⚠️" in final_text[:20] or len(final_text.strip()) < 100:
            logger.warning("[war_room] 林彪（%s）输出异常，Claude Sonnet 兜底...", commander_model)
            final_text = _call_single_model(lin_user, lin_system, CLAUDE_FALLBACK, max_tokens=12000)

        final_scores = parse_scores(final_text)
        if final_scores:
            final_scores = apply_bucket_correction(final_scores)
            # ★ Pre-mortem一致性检查：高概率致命风险→评分上限70
            final_scores = _apply_premortem_cap(final_scores, final_text)
            # ★ 三将领分歧熔断：标准差>15时下修5分（不确定性溢价）
            g_weighted = [g["scores"].get("综合加权", 50) for g in general_reports
                          if g["scores"] and not g["scores"].get("_substituted")]
            if len(g_weighted) >= 2:
                import statistics
                g_std = statistics.stdev(g_weighted)
                if g_std > 15:
                    old_w = final_scores.get("综合加权", 50)
                    final_scores["综合加权"] = round(old_w - 5, 1)
                    final_scores["_divergence_penalty"] = f"将领分歧大(σ={g_std:.1f})→-5分"
                    logger.info("[war_room] 分歧熔断: σ=%.1f>15, 综合%.1f→%.1f",
                                g_std, old_w, final_scores["综合加权"])
            # ★ 评分区分度自动修正
            from services.analysis_service import check_score_spread
            spread_msg = check_score_spread(final_scores, auto_correct=True)
            if spread_msg:
                logger.info("[war_room] %s", spread_msg)
        else:
            # ★ fallback：林彪输出没有 <<<SCORES>>> 块时，从将领评分加权计算
            logger.warning("[war_room] 林彪未输出SCORES块，从将领评分推算最终分数")
            final_scores = _fallback_scores_from_generals(general_reports, final_text)

        from services.analysis_service import _split_report_and_summary
        final_summary, _ = _split_report_and_summary(final_text)

    # ── Phase 5: 组装完整战报 + 保存 ────────────────────────────
    logger.info("[war_room] Phase 5: 组装战报并保存")

    combined_md = _build_combined_markdown(
        stock_name=resolved_name,
        general_reports=general_reports,
        scores_table=scores_table,
        final_text=final_text,
        final_scores=final_scores,
    )

    report_id = str(uuid.uuid4())
    try:
        init_db()
        save_report(
            report_id=report_id,
            openid="war_room",
            stock_name=resolved_name,
            stock_code=ts_code,
            summary=final_summary or "指挥部分析完成",
            markdown_text=combined_md,
        )
    except Exception as exc:
        logger.error("[war_room] save_report 失败: %r（分析结果仍会返回）", exc)

    try:
        _save_war_room_tracker(report_id, resolved_name, ts_code, general_reports, final_scores or {})
    except Exception as exc:
        logger.warning("[war_room] save_tracker 失败: %r", exc)

    result = WarRoomResult(
        stock_name=resolved_name,
        stock_code=ts_code,
        general_reports=general_reports,
        general_scores=[g["scores"] for g in general_reports],
        staff_brief=generals_brief,
        final_report=final_text,
        final_summary=final_summary,
        final_scores=final_scores or {},
        combined_markdown=combined_md,
        report_id=report_id,
    )
    logger.info("[war_room] 完成: %s 综合 %s", resolved_name, (final_scores or {}).get("综合加权", "?"))

    # 邮件由 cli.py export-image 统一发送，war_room 不再自动发
    # （避免 war_room 自动发 + export-image 再发 = 重复两封）

    return result


def _send_war_room_email(result) -> None:
    """发送指挥部战报邮件（含完整报告 PNG 截图附件）。"""
    try:
        from utils.email_sender import send_text_email, send_image_email, smtp_configured
    except ImportError:
        return

    if not smtp_configured():
        return

    scores = result.final_scores or {}
    rating = scores.get("_rating", "")
    weighted = scores.get("综合加权", "?")

    # 生成 PNG 截图
    png_path = None
    try:
        from utils.html_render import md_to_html as _md_to_html, html_to_image as _html_to_image
        safe_name = result.stock_name.replace(" ", "_")
        out_dir = BASE_DIR / "storage" / "export"
        out_dir.mkdir(parents=True, exist_ok=True)

        html_text = _md_to_html(result.combined_markdown, title=f"{result.stock_name} 指挥部战报")
        html_file = out_dir / f"{safe_name}_{result.report_id[:8]}.html"
        html_file.write_text(html_text, encoding="utf-8")

        png_file = out_dir / f"{safe_name}_{result.report_id[:8]}.png"
        _html_to_image(str(html_file), str(png_file))

        if png_file.exists() and png_file.stat().st_size > 0:
            png_path = png_file
            logger.info("[war_room] PNG截图生成: %s (%dKB)", png_path, png_file.stat().st_size // 1024)
    except Exception as exc:
        logger.warning("[war_room] PNG截图失败（降级为纯文本邮件）: %r", exc)

    subject = f"【指挥部战报】{result.stock_name} {weighted}分 {rating}"

    lines = [
        f"四野指挥部·战报",
        f"{'=' * 40}",
        f"标的：{result.stock_name}（{result.stock_code}）",
        f"综合评分：{weighted} — {rating}",
        f"",
        f"【四维评分】",
        f"  基本面: {scores.get('基本面', '?')}/100",
        f"  预期差: {scores.get('预期差', '?')}/100",
        f"  资金面: {scores.get('资金面', '?')}/100",
        f"  技术面: {scores.get('技术面', '?')}/100",
        f"",
        f"【战役总结】",
        f"  {result.final_summary or '无'}",
        f"",
        f"{'=' * 40}",
        f"{'完整报告见附件 PNG' if png_path else '（PNG截图生成失败，仅文本摘要）'}",
        f"报告ID: {result.report_id}",
        f"LinDangAgent 四野指挥部",
    ]

    body = "\n".join(lines)

    if png_path and png_path.exists():
        send_image_email(subject, body, str(png_path),
                         filename=f"{result.stock_name}_战报.png")
    else:
        send_text_email(subject, body)

    logger.info("[war_room] email sent: %s (png=%s)", subject, "yes" if png_path else "no")


def _build_combined_markdown(
    stock_name: str,
    general_reports: list,
    scores_table: str,
    final_text: str,
    final_scores: dict,
) -> str:
    """组装完整战报 Markdown — 按章节合并，每章取专长将领的内容。

    将领专长分工：
      A（黄永胜·攻势）：第一章(战场态势) + 第三章(预期差催化) + 第四章(题材资金)
      B（韩先楚·侦察）：第二章(基本面排雷) + 第六章(撤退纪律)
      C（邓华·全局）  ：第一章(宏观全局) + 第四章(板块身位) + 第五章(技术面)
    """
    parts = [f"# 【{stock_name}】四野指挥部战报\n"]

    # 林彪最终裁决（置顶）
    if final_scores:
        rating = final_scores.get('_rating', '')
        parts.append(f"**林彪最终裁决**：综合 {final_scores.get('综合加权', '?')} — {rating}\n")

    # 评分对比表
    parts.append("## 三将领评分对比\n")
    parts.append(scores_table)

    # 林彪最终判断
    parts.append("\n---\n## 司令员林彪·最终战术判断\n")
    parts.append(final_text)

    # v4.0: 从每位将领的报告中提取章节，按质量选最优
    general_chapters: list[dict[str, str]] = []
    for g in general_reports:
        text = g.get("report_text", "")
        chapters = _extract_chapters(text)
        general_chapters.append(chapters)

    parts.append("\n---\n## 合并战报（按章节·质量优选）\n")

    general_names = ["A·黄永胜", "B·韩先楚", "C·邓华"]
    for ch_num in ["一", "二", "三", "四", "五", "六"]:
        best_idx, best_content = _select_best_chapter(ch_num, general_chapters)
        if best_content:
            source = general_names[best_idx] if best_idx < len(general_names) else f"将领{best_idx}"
            parts.append(f"<!-- 来源: {source} -->")
            parts.append(best_content.strip())
            parts.append("")

    # 第七章（评分）从林彪报告中提取
    if final_text and "七" in final_text:
        ch7 = _extract_chapters(final_text).get("七", "")
        if ch7:
            parts.append(ch7.strip())

    return "\n".join(parts)


def _extract_chapters(text: str) -> dict[str, str]:
    """从报告文本中提取各章节内容。返回 {"一": "...", "二": "...", ...}

    用 finditer 而非 re.split，避免捕获组导致的索引混乱。
    """
    import re

    chapters = {}
    # 匹配章节标题行（宽松：支持 ## 一、##第一章、### 一、## 一. 等变体）
    ch_pattern = re.compile(r"(#{1,3}\s*(?:第)?[一二三四五六七](?:章)?[\s、\.．：:]*[^\n]*)")
    matches = list(ch_pattern.finditer(text))

    for idx, match in enumerate(matches):
        # 提取章节号
        ch_num_match = re.search(r"([一二三四五六七])", match.group(1))
        if not ch_num_match:
            continue
        ch_num = ch_num_match.group(1)

        # 章节内容：从标题开始到下一个章节标题（或文末）
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chapters[ch_num] = text[start:end]

    return chapters


def _select_best_chapter(chapter_num: str, general_chapters: list[dict]) -> tuple[int, str]:
    """按质量从三位将领中选最优章节内容。

    质量评分 = 表格行数×3 + 数字引用数×1 + 结论性词汇×2
    字数<100的章节不参选。
    """
    candidates = []
    for i, gc in enumerate(general_chapters):
        content = gc.get(chapter_num, "")
        if len(content) < 100:
            continue
        table_count = content.count("|")
        number_count = len(re.findall(r"\d+\.?\d*%?", content))
        conclusion_words = sum(1 for w in ["结论", "判断", "建议", "评级", "档", "证伪", "否决"]
                               if w in content)
        quality = table_count * 3 + number_count + conclusion_words * 2
        candidates.append((i, quality, content))

    if not candidates:
        # 兜底：取任何有内容的
        for i, gc in enumerate(general_chapters):
            content = gc.get(chapter_num, "")
            if content.strip():
                return (i, content)
        return (0, "")

    candidates.sort(key=lambda x: x[1], reverse=True)
    return (candidates[0][0], candidates[0][2])


def _run_bull_bear_debate(general_reports: list, generals_brief: str, model_name: str) -> str:
    """Phase 3.5: 看多方 vs 看空方辩论。

    从将领中找出分歧最大的两方，让看多方和看空方各出一段反驳。
    用最便宜的模型（一次调用），输出200-400字的辩论记录。
    """
    # 找最看多和最看空的将领
    scored = []
    for i, g in enumerate(general_reports):
        w = g["scores"].get("综合加权", 50)
        scored.append((i, w))

    if len(scored) < 2:
        return ""

    scored.sort(key=lambda x: x[1])
    bear_idx, bear_score = scored[0]
    bull_idx, bull_score = scored[-1]

    # 分歧太小不辩论
    if bull_score - bear_score < 10:
        return ""

    general_names = ["A·黄永胜", "B·韩先楚", "C·邓华"]
    bull_name = general_names[bull_idx] if bull_idx < len(general_names) else f"将领{chr(65+bull_idx)}"
    bear_name = general_names[bear_idx] if bear_idx < len(general_names) else f"将领{chr(65+bear_idx)}"

    bull_summary = general_reports[bull_idx].get("summary", "")[:300]
    bear_summary = general_reports[bear_idx].get("summary", "")[:300]

    debate_prompt = f"""以下两位将领对同一标的判断分歧严重，请模拟双方辩论：

【看多方 {bull_name}】综合{bull_score}分
观点：{bull_summary}

【看空方 {bear_name}】综合{bear_score}分
观点：{bear_summary}

请按以下格式输出辩论（共200-400字）：

🐂 看多方反驳看空方：（针对看空方最弱的论据，给出1-2个反驳）
🐻 看空方反驳看多方：（针对看多方最弱的论据，给出1-2个反驳）
⚖️ 辩论焦点：（一句话总结核心分歧在哪里）"""

    debate_system = "你是一个客观的投资辩论主持人。模拟双方最有力的论据对抗，不偏袒任何一方。简洁犀利。"

    # 用轻量模型（不值得用 Opus）
    text = _call_single_model(debate_prompt, debate_system, model_name)
    if text and not text.startswith("⚠️"):
        return text
    return ""


def _save_war_room_tracker(
    report_id: str, stock_name: str, ts_code: str,
    general_reports: list, final_scores: dict,
):
    """保存将领追踪数据到 war_room_tracker.jsonl"""
    from datetime import datetime

    tracker_file = BASE_DIR / "data" / "knowledge" / "war_room_tracker.jsonl"
    tracker_file.parent.mkdir(parents=True, exist_ok=True)

    generals = {}
    for i, g in enumerate(general_reports):
        label = f"general_{chr(97 + i)}"  # general_a, general_b, general_c
        generals[label] = {
            "综合加权": g["scores"].get("综合加权", 0),
            "基本面": g["scores"].get("基本面", 0),
            "预期差": g["scores"].get("预期差", 0),
            "资金面": g["scores"].get("资金面", 0),
            "技术面": g["scores"].get("技术面", 0),
        }

    # 计算将领间最大分歧
    weighted_scores = [g["scores"].get("综合加权", 0) for g in general_reports if g["scores"]]
    divergence = max(weighted_scores) - min(weighted_scores) if len(weighted_scores) >= 2 else 0

    record = {
        "report_id": report_id,
        "stock_name": stock_name,
        "stock_code": ts_code,
        "report_date": datetime.now().strftime("%Y-%m-%d"),
        "generals": generals,
        "lin_biao": {
            "综合加权": final_scores.get("综合加权", 0),
        },
        "divergence": round(divergence, 2),
    }

    with open(tracker_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
