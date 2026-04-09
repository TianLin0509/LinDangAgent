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

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

# ── 指挥部阵容预设 ──────────────────────────────────────────────
# 每个阵容定义 3 将领各自用什么模型 + 刘亚楼 + 林彪

WAR_ROOM_PRESETS = {
    # ── 负载均衡阵容（推荐，三路模型分散避免限流，Claude裁决）──
    "balanced": {
        "label": "负载均衡阵容（Gemini+Codex将领，Claude Opus裁决）",
        "scouts": [
            "🔮 Gemini CLI（免费）",     # 黄永胜·攻势：Gemini联网搜催化
            "🤖 Codex CLI（Plus）",       # 韩先楚·侦察：Codex联网查财务
            "🔮 Gemini CLI（免费）",     # 邓华·全局：Gemini联网看板块
        ],
        "commander": "🧠 Claude Opus（MAX）",   # 林彪：Claude Opus 裁决
    },
    # ── 最高配置（全Claude，关键股票专用）───────────────────────
    "max": {
        "label": "全 Claude MAX 阵容（Sonnet将领+Opus裁决）",
        "scouts": [
            "⚡ Claude Sonnet（MAX）",    # 黄永胜
            "⚡ Claude Sonnet（MAX）",    # 韩先楚
            "⚡ Claude Sonnet（MAX）",    # 邓华
        ],
        "commander": "🧠 Claude Opus（MAX）",  # 林彪：Opus裁决
    },
    # ── 经济阵容（全免费，单股或测试用）─────────────────────────
    "gemini": {
        "label": "全 Gemini 阵容（免费，单股可用，批量易限流）",
        "scouts": ["🔮 Gemini CLI（免费）"] * 3,
        "commander": "🔮 Gemini CLI（免费）",
    },
}

DEFAULT_PRESET = "balanced"


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


def _call_single_model(prompt: str, system: str, model_name: str) -> str:
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
            stream = call_ai_stream(client, cfg, prompt, system=system, max_tokens=12000)
            for _ in stream:
                pass
            text = stream.full_text
        else:
            # API 模型走 call_ai
            text, call_err = call_ai(client, cfg, prompt, system=system, max_tokens=12000)
            if call_err:
                text = f"⚠️ 调用失败：{call_err}"
    except Exception as exc:
        text = f"⚠️ 异常：{exc}"

    # ★ 失败重试：输出为错误或过短时，重试一次（同模型或 Claude Sonnet）
    is_failure = "⚠️" in text[:20] or len(text.strip()) < 50
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
                stream = call_ai_stream(fb_client, fb_cfg, prompt, system=system, max_tokens=12000)
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
) -> WarRoomResult:
    """四野指挥部完整流程"""
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

    # 构建差异化 prompt：共用简报 + 专长详情 + 精简输出格式（省55%+ token）
    shared_brief, system_prompt, detail_sections, output_formats = build_war_room_prompts(
        name=resolved_name,
        ts_code=ts_code,
        context=context,
        price_snapshot=snap,
        indicators_section=indicators_section,
        knowledge_context=knowledge_ctx,
        sentiment_context=sentiment_ctx,
        macro_context=macro_brief,
    )

    # 同时保留旧接口用于非指挥部场景（单模型分析等）
    user_prompt, _ = build_report_prompt(
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
        # system prompt 文件通道：角色设定 + 将领人设 + 共用简报 + 输出模板
        # stdin 通道：仅将领专长数据（2800-3500字）
        full_system = (
            system_prompt
            + GENERAL_PERSONALITIES[i % len(GENERAL_PERSONALITIES)]
            + "\n\n" + shared_brief
            + "\n\n" + output_formats[i % len(output_formats)]
        )
        stdin_prompt = detail_sections[i % len(detail_sections)]

        logger.info("[war_room] 将领%s stdin %d字 / system %d字",
                    chr(65 + i), len(stdin_prompt), len(full_system))

        text = _call_single_model(stdin_prompt, full_system, model)
        parsed = _parse_general_report(text)

        # ★ Claude兜底：将领输出为错误/无评分时，自动用Claude Sonnet重试
        if not parsed["scores"] or "⚠️" in text[:20]:
            logger.warning("[war_room] 将领%s（%s）失败，Claude Sonnet 兜底...",
                           chr(65 + i), model)
            text = _call_single_model(stdin_prompt, full_system, CLAUDE_FALLBACK)
            parsed = _parse_general_report(text)

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

    # ── Phase 1 审查：将领评分缺失/异常则替补保护 ────────────────
    _dims = ["基本面", "预期差", "资金面", "技术面"]

    def _is_score_broken(scores: dict) -> bool:
        """评分是否缺失或异常：完全失败、综合加权缺失、或多维度为0（解析不全）"""
        if not scores or scores.get("_parse_failed") or scores.get("综合加权") is None:
            return True
        # 3个及以上维度=0 视为解析缺失（真实评分不会出现多维度恰好为0）
        zero_count = sum(1 for d in _dims if scores.get(d, 0) == 0)
        return zero_count >= 3

    valid_scores = [g["scores"].get("综合加权") for g in general_reports
                    if g["scores"] and not _is_score_broken(g["scores"])]
    for i, g in enumerate(general_reports):
        if _is_score_broken(g["scores"]):
            if valid_scores:
                median_val = sorted(valid_scores)[len(valid_scores) // 2]
                logger.warning("[war_room] Phase1审查：将领%s评分异常(%s)，用中位数%.1f替补",
                               chr(65+i), g["scores"].get("综合加权", "?"), median_val)
                g["scores"] = {"基本面": median_val, "预期差": median_val,
                               "资金面": median_val, "技术面": median_val,
                               "综合加权": median_val, "_substituted": True}
            else:
                logger.warning("[war_room] Phase1审查：将领%s评分异常且无可用替补，默认50分", chr(65+i))
                g["scores"] = {"基本面": 50, "预期差": 50, "资金面": 50, "技术面": 50,
                               "综合加权": 50, "_substituted": True}

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
            general_names_ac = ["A·黄永胜(攻势)", "C·邓华(全局)"]
            for idx in [0, 2]:
                if idx < len(general_reports):
                    g = general_reports[idx]
                    label = general_names_ac[0 if idx == 0 else 1]
                    ac_brief += f"【{label}】综合{g['scores'].get('综合加权', '?')}分：{g.get('summary', '无')}\n"

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
                    # <<<VETO>>> 裸标记也要验证：检查是否有明确的否决陈述
                    # 避免模型把标记当格式示例输出而误触否决
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

    # 将领报告摘要（代替刘亚楼的汇总，零AI成本）
    general_names = ["A·黄永胜(攻势)", "B·韩先楚(侦察)", "C·邓华(全局)"]
    generals_brief_parts = []
    for i, g in enumerate(general_reports):
        label = general_names[i] if i < len(general_names) else f"将领{chr(65+i)}"
        summary = g.get("summary", "无")
        generals_brief_parts.append(f"【{label}】综合{g['scores'].get('综合加权', '?')}分：{summary}")
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
    data_summary = f"""{macro_full + chr(10) + chr(10) if macro_full else ''}【价格快照】
{snap}

【技术指标】
{indicators_section[:800]}

【风险清单】
{context.get('risk_checklist', '未排查')}

【财报情报概况】
{context.get('report_period_info', '暂无')}

【券商研报】
{context.get('research_reports', '暂无')[:600]}

【分析师一致预期】
{context.get('analyst_consensus', '暂无')[:400]}"""

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

        # 辩论结果注入林彪 prompt（如果有）
        debate_section = ""
        if debate_text:
            debate_section = f"\n\n【Bull vs Bear 辩论记录】\n{debate_text[:1000]}"

        lin_user, lin_system = build_lin_biao_prompt(
            staff_brief=generals_brief + debate_section,
            data_summary=data_summary,
            scores_table=scores_table,
            knowledge_context=knowledge_ctx,
        )
        final_text = _call_single_model(lin_user, lin_system, commander_model)

        # ★ Claude兜底：commander 输出为错误时，用 Claude Sonnet 重试
        if "⚠️" in final_text[:20] or len(final_text.strip()) < 100:
            logger.warning("[war_room] 林彪（%s）输出异常，Claude Sonnet 兜底...", commander_model)
            final_text = _call_single_model(lin_user, lin_system, CLAUDE_FALLBACK)

        final_scores = parse_scores(final_text)
        if final_scores:
            final_scores = apply_bucket_correction(final_scores)
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

    # 发送指挥部战报邮件
    try:
        _send_war_room_email(result)
    except Exception as exc:
        logger.warning("[war_room] email failed: %r", exc)

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

    # 按章节合并将领报告
    # 章节→将领映射：每章优先取专长将领的内容
    chapter_map = {
        "一": 0,   # 第一章 → 将领A（黄永胜，战场态势），将领C也写但A优先
        "二": 1,   # 第二章 → 将领B（韩先楚，基本面）
        "三": 0,   # 第三章 → 将领A（预期差催化）
        "四": 0,   # 第四章 → 将领A（题材资金），将领C也写但A有资金面数据
        "五": 2,   # 第五章 → 将领C（邓华，技术面）
        "六": 1,   # 第六章 → 将领B（韩先楚，撤退纪律）
    }

    # 从每位将领的报告中提取章节
    general_chapters: list[dict[str, str]] = []
    for g in general_reports:
        text = g.get("report_text", "")
        chapters = _extract_chapters(text)
        general_chapters.append(chapters)

    parts.append("\n---\n## 合并战报（按章节·各取专长）\n")

    for ch_num, preferred_idx in chapter_map.items():
        # 优先取专长将领的章节，如果没有则从其他将领找
        content = ""
        if preferred_idx < len(general_chapters):
            content = general_chapters[preferred_idx].get(ch_num, "")
        if not content:
            # 兜底：从任何有该章节的将领中找
            for gc in general_chapters:
                content = gc.get(ch_num, "")
                if content:
                    break
        if content:
            parts.append(content.strip())
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
