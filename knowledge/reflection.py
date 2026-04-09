"""AI 反思 — 对已评估的 outcome 调用 Claude Sonnet 生成经验教训

每条反思使用 Claude Sonnet（MAX 订阅免费），生成 2-3 句话的因果分析。
保持与深度反思引擎同一个 Claude "主脑"，确保思维连贯性。
反思结果存入 case_memory 作为案例卡片的核心价值。
"""

import logging
import os
from datetime import datetime
from pathlib import Path

# 清除代理（豆包 API 是国内接口，不需要代理）
for _k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(_k, None)

from knowledge.case_memory import (
    CaseCard,
    build_situation_summary,
    case_exists,
    classify_outcome,
    extract_sector_tags,
    store_case,
)
from knowledge.kb_config import DIRECTION_CN

logger = logging.getLogger(__name__)

# 反思用的模型 — Claude Sonnet 作为"主脑"，保持思维连贯性
REFLECTION_MODEL = "⚡ Claude Sonnet（MAX）"
# 备选模型（Claude 不可用时回退）
REFLECTION_FALLBACK = "🟤 豆包 · Seed 2.0 Mini"

REFLECTION_SYSTEM = (
    "你是林铛，一个正在成长的 AI 投研分析师。你在复盘一次已完成的股票分析。"
    "只输出2-3句话的教训总结，不要输出任何格式标记或多余内容。"
    "用第一人称（'我'）写作，语气冷峻务实，像一个老练的交易员在复盘笔记上写批注。"
    "每句话都要有具体指向——哪个维度判断有误、为什么、下次怎么调整。"
)

REFLECTION_USER_TEMPLATE = """以下是一次股票分析的原始判断与实际结果，请生成2-3句话的复盘教训。

【分析标的】{stock_name}（{stock_code}）
【分析日期】{report_date}
【市场环境】{regime_label}

【AI原始评分】
- 基本面: {score_fundamental}/10
- 预期差: {score_expectation}/10
- 资金面: {score_capital}/10
- 技术面: {score_technical}/10
- 综合加权: {score_weighted}/10
- 方向判断: {direction_cn}

【AI原始摘要】
{reasoning_summary}

【实际结果】
- 5日收益: {return_5d:+.1f}%
- 10日收益: {return_10d:+.1f}%
- 20日收益: {return_20d:+.1f}%
- 10日命中: {hit_result}

请用2-3句话总结教训，格式为：
"我在[具体方面]判断[正确/错误]。[原因分析]。[未来类似情况应如何调整]。"
"""


def generate_reflection(outcome: dict, regime_info: dict | None = None) -> tuple[str, str]:
    """为一条 outcome 生成反思教训。

    返回 (lesson_text, situation_summary)。
    失败时 lesson_text 为空字符串。
    """
    from ai.client import call_ai, get_ai_client

    # 准备参数
    direction = outcome.get("direction", "neutral")
    direction_cn = DIRECTION_CN.get(direction, "中性")
    hit_10d = outcome.get("hit_10d")
    hit_result = "命中 ✅" if hit_10d else ("未命中 ❌" if hit_10d is False else "中性/不适用")

    regime_label = "未知"
    if regime_info:
        regime_label = regime_info.get("regime_label", "未知")

    scores = outcome.get("scores", {})
    reasoning = outcome.get("reasoning_summary", outcome.get("short_term_advice", ""))

    prompt = REFLECTION_USER_TEMPLATE.format(
        stock_name=outcome.get("stock_name", ""),
        stock_code=outcome.get("stock_code", ""),
        report_date=outcome.get("report_date", ""),
        regime_label=regime_label,
        score_fundamental=scores.get("基本面", outcome.get("scores", {}).get("基本面", 5)),
        score_expectation=scores.get("预期差", outcome.get("scores", {}).get("预期差", 5)),
        score_capital=scores.get("资金面", outcome.get("scores", {}).get("资金面", 5)),
        score_technical=scores.get("技术面", outcome.get("scores", {}).get("技术面", 5)),
        score_weighted=outcome.get("weighted_score", 5),
        direction_cn=direction_cn,
        reasoning_summary=reasoning[:300] if reasoning else "无摘要",
        return_5d=outcome.get("return_5d", 0),
        return_10d=outcome.get("return_10d", 0),
        return_20d=outcome.get("return_20d", 0),
        hit_result=hit_result,
    )

    # Claude Sonnet 作为主脑，保持与深度反思引擎的思维连贯性
    client, cfg, err = get_ai_client(REFLECTION_MODEL)
    if err and not cfg:
        logger.warning("[reflection] Claude Sonnet unavailable: %s, trying fallback", err)
        client, cfg, err = get_ai_client(REFLECTION_FALLBACK)
        if err and not cfg:
            logger.warning("[reflection] fallback also failed: %s", err)
            return "", ""

    cfg_no_search = {**cfg, "supports_search": False}

    # 最多尝试2次（首次 + 质量不达标重试1次）
    lesson = ""
    for attempt in range(2):
        raw_lesson, call_err = call_ai(client, cfg_no_search, prompt, system=REFLECTION_SYSTEM, max_tokens=300)
        if call_err:
            logger.warning("[reflection] AI call failed (attempt %d): %s", attempt + 1, call_err)
            continue

        raw_lesson = raw_lesson.strip()
        if len(raw_lesson) < 10:
            logger.warning("[reflection] lesson too short (attempt %d): %r", attempt + 1, raw_lesson)
            continue

        # 质量校验：必须包含至少一个维度名 + 一个方向性结论关键词
        dimension_keywords = ["基本面", "预期差", "资金面", "技术面", "催化", "题材", "估值", "资金"]
        direction_keywords = ["高估", "低估", "忽视", "过度", "正确", "错误", "偏乐观", "偏悲观",
                              "不足", "充分", "命中", "失误", "遗漏", "低估了", "高估了"]
        has_dimension = any(kw in raw_lesson for kw in dimension_keywords)
        has_direction = any(kw in raw_lesson for kw in direction_keywords)

        if has_dimension and has_direction:
            lesson = raw_lesson
            break
        elif attempt == 0:
            logger.info("[reflection] quality check failed (no dimension/direction), retrying...")
        else:
            # 第二次仍未达标，降级使用（总比没有好）
            lesson = raw_lesson
            logger.info("[reflection] quality check still failed, using as-is: %s", raw_lesson[:60])

    if not lesson:
        return "", ""

    # 情境摘要（模板生成，零成本）
    dummy_case = CaseCard(
        case_id="",
        report_date=outcome.get("report_date", ""),
        stock_code=outcome.get("stock_code", ""),
        stock_name=outcome.get("stock_name", ""),
        regime=regime_info.get("regime", "shock") if regime_info else "shock",
        regime_label=regime_label,
        sector_tags=extract_sector_tags(reasoning),
        score_weighted=outcome.get("weighted_score", 5),
        direction=direction,
        return_10d=outcome.get("return_10d", 0),
        outcome_type=classify_outcome(direction, outcome.get("return_10d", 0)),
    )
    situation_summary = build_situation_summary(dummy_case)

    logger.info("[reflection] generated for %s: %s", outcome.get("stock_name", ""), lesson[:60])
    return lesson, situation_summary


def process_pending_reflections(max_batch: int = 10) -> int:
    """批量处理：对未生成案例的 outcome 生成反思并存储案例卡片。

    优化策略：多条案例合并为一次 Claude 调用（batch_reflect），
    将 N 次 CLI 子进程开销（N×15-30s）压缩为 1 次（20-40s）。

    返回成功生成的反思数量。
    """
    from knowledge.outcome_tracker import load_outcomes
    from knowledge.regime_detector import get_regime_history

    outcomes = load_outcomes()
    if not outcomes:
        return 0

    # 构建日期->regime 映射
    regime_map = {}
    for entry in get_regime_history(days=365):
        regime_map[entry["date"]] = entry

    # 收集待处理的 outcome
    pending = []
    for outcome in outcomes:
        if len(pending) >= max_batch:
            break
        case_id = outcome.get("report_id", "")
        if not case_id or case_exists(case_id):
            continue
        report_date = outcome.get("report_date", "")
        regime_info = regime_map.get(report_date)
        pending.append((outcome, regime_info))

    if not pending:
        return 0

    # 批量反思：多条合并为一次 Claude 调用
    if len(pending) >= 2:
        lessons_map = _batch_reflect(pending)
    else:
        # 单条走原有逻辑
        lessons_map = {}
        outcome, regime_info = pending[0]
        lesson, _ = generate_reflection(outcome, regime_info)
        lessons_map[outcome.get("report_id", "")] = lesson

    # 存储案例卡片
    processed = 0
    for outcome, regime_info in pending:
        case_id = outcome.get("report_id", "")
        lesson = lessons_map.get(case_id, "")

        # 空 lesson 不存入 case_memory，避免占用 case_id 阻止后续重试
        if not lesson:
            continue

        scores = outcome.get("scores", {})
        direction = outcome.get("direction", "neutral")
        return_10d = outcome.get("return_10d", 0)
        regime_label = regime_info.get("regime_label", "震荡市") if regime_info else "震荡市"

        reasoning = outcome.get("reasoning_summary", outcome.get("short_term_advice", ""))
        all_text = f"{outcome.get('stock_name', '')} {reasoning}"
        sector_tags = extract_sector_tags(all_text)

        dummy_case = CaseCard(
            case_id="",
            report_date=outcome.get("report_date", ""),
            stock_code=outcome.get("stock_code", ""),
            stock_name=outcome.get("stock_name", ""),
            regime=regime_info.get("regime", "shock") if regime_info else "shock",
            regime_label=regime_label,
            sector_tags=sector_tags,
            score_weighted=outcome.get("weighted_score", 5),
            direction=direction,
            return_10d=return_10d,
            outcome_type=classify_outcome(direction, return_10d),
        )
        situation_summary = build_situation_summary(dummy_case)

        case = CaseCard(
            case_id=case_id,
            report_date=outcome.get("report_date", ""),
            stock_code=outcome.get("stock_code", ""),
            stock_name=outcome.get("stock_name", ""),
            source=outcome.get("source", "report"),
            regime=regime_info.get("regime", "shock") if regime_info else "shock",
            regime_label=regime_label,
            sector_tags=sector_tags,
            score_fundamental=scores.get("基本面", 5),
            score_expectation=scores.get("预期差", 5),
            score_capital=scores.get("资金面", 5),
            score_technical=scores.get("技术面", 5),
            score_weighted=outcome.get("weighted_score", 5),
            direction=direction,
            reasoning_summary=reasoning[:200] if reasoning else "",
            return_5d=outcome.get("return_5d", 0),
            return_10d=return_10d,
            return_20d=outcome.get("return_20d", 0),
            hit_10d=outcome.get("hit_10d"),
            outcome_type=classify_outcome(direction, return_10d),
            lesson=lesson,
            lesson_generated_at=datetime.now().isoformat(timespec="seconds") if lesson else None,
            situation_summary=situation_summary,
        )

        store_case(case)
        processed += 1

    logger.info("[reflection] processed %d pending reflections (batch mode)", processed)
    return processed


# ── 批量反思（核心优化：N条合并为1次 Claude 调用）──────────────────

BATCH_REFLECTION_SYSTEM = (
    "你是林铛，一个正在成长的 AI 投研分析师。你在批量复盘多个已完成的股票分析。"
    "对每个案例分别输出2-3句话的教训，用 JSON 数组格式返回。"
    "用第一人称（'我'）写作，语气冷峻务实。"
    "每句话都要有具体指向——哪个维度判断有误、为什么、下次怎么调整。"
)

BATCH_REFLECTION_TEMPLATE = """以下是 {count} 个股票分析的原始判断与实际结果，请逐一复盘。

{cases_text}

请输出严格 JSON 数组，每个元素对应一个案例：
[
  {{"id": "案例1的report_id", "lesson": "2-3句话的复盘教训"}},
  {{"id": "案例2的report_id", "lesson": "..."}},
  ...
]

要求：
- 每条教训必须提及具体维度（基本面/预期差/资金面/技术面）
- 必须包含方向性结论（高估/低估/正确/错误/偏乐观/偏悲观等）
- 不要笼统套话，每条教训要有区分度
"""


def _batch_reflect(pending: list[tuple[dict, dict | None]]) -> dict[str, str]:
    """批量反思：多条 outcome 合并为一次 Claude 调用。

    返回 {report_id: lesson_text} 的映射。
    """
    import json
    from ai.client import call_ai, get_ai_client
    from knowledge.kb_utils import parse_ai_json

    # 构建批量 prompt
    cases_parts = []
    id_list = []
    for idx, (outcome, regime_info) in enumerate(pending, 1):
        case_id = outcome.get("report_id", "")
        id_list.append(case_id)
        direction = outcome.get("direction", "neutral")
        direction_cn = DIRECTION_CN.get(direction, "中性")
        hit_10d = outcome.get("hit_10d")
        hit_result = "命中 ✅" if hit_10d else ("未命中 ❌" if hit_10d is False else "中性")
        regime_label = regime_info.get("regime_label", "未知") if regime_info else "未知"
        scores = outcome.get("scores", {})
        reasoning = outcome.get("reasoning_summary", "")[:200]

        cases_parts.append(
            f"--- 案例{idx} (id: {case_id}) ---\n"
            f"标的: {outcome.get('stock_name', '')}（{outcome.get('stock_code', '')}）\n"
            f"日期: {outcome.get('report_date', '')} | 环境: {regime_label}\n"
            f"评分: 基{scores.get('基本面', 5)} 预{scores.get('预期差', 5)} "
            f"资{scores.get('资金面', 5)} 技{scores.get('技术面', 5)} "
            f"加权{outcome.get('weighted_score', 5)} | 方向: {direction_cn}\n"
            f"结果: 5日{outcome.get('return_5d', 0):+.1f}% "
            f"10日{outcome.get('return_10d', 0):+.1f}% "
            f"20日{outcome.get('return_20d', 0):+.1f}% | {hit_result}\n"
            f"摘要: {reasoning or '无'}\n"
        )

    cases_text = "\n".join(cases_parts)
    prompt = BATCH_REFLECTION_TEMPLATE.format(count=len(pending), cases_text=cases_text)

    # 调用 Claude Sonnet（一次搞定）
    client, cfg, err = get_ai_client(REFLECTION_MODEL)
    if err and not cfg:
        logger.warning("[batch_reflect] Claude Sonnet unavailable: %s, trying fallback", err)
        client, cfg, err = get_ai_client(REFLECTION_FALLBACK)
        if err and not cfg:
            logger.warning("[batch_reflect] all models unavailable")
            return {}

    cfg_no_search = {**cfg, "supports_search": False}
    max_tokens = min(300 * len(pending), 4000)  # 每条约300 tokens
    text, call_err = call_ai(client, cfg_no_search, prompt, system=BATCH_REFLECTION_SYSTEM, max_tokens=max_tokens)
    if call_err:
        logger.warning("[batch_reflect] AI call failed: %s", call_err)
        return {}

    # 解析 JSON 数组
    results = parse_ai_json(text)
    if not isinstance(results, list):
        logger.warning("[batch_reflect] failed to parse AI JSON or not a list")
        return {}

    # 构建映射
    lessons_map = {}
    for item in results:
        rid = item.get("id", "")
        lesson = item.get("lesson", "")
        if rid and lesson and len(lesson) >= 10:
            lessons_map[rid] = lesson

    logger.info("[batch_reflect] got %d/%d lessons in 1 call", len(lessons_map), len(pending))
    return lessons_map
