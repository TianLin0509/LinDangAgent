# -*- coding: utf-8 -*-
"""深度反思引擎 — 周度/月度用 Claude 进行深层次复盘

日常反思（reflection.py）用 Claude Sonnet 做单案例 2-3 句教训。
深度反思是更高层次的思考：

  - 周度反思（Claude Sonnet）：本周所有案例的系统性偏差、信念更新建议
  - 月度复盘（Claude Opus）：审视投资哲学、方法论调整、长期趋势判断

输出存入 data/knowledge/reflections.jsonl 并更新 thesis_journal 信念。
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

# 清除代理
for _k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(_k, None)

from knowledge.kb_config import DIRECTION_CN, KNOWLEDGE_DIR

logger = logging.getLogger(__name__)
REFLECTIONS_FILE = KNOWLEDGE_DIR / "reflections.jsonl"


# ── Prompt 模板 ──────────────────────────────────────────────────

WEEKLY_SYSTEM = (
    "你是林铛，一个正在成长的 AI 投研分析师。"
    "你在进行每周复盘——认真审视自己这周的分析表现，找出系统性问题，给出改进方向。"
    "用第一人称写作，语气冷峻务实，像一个老练的交易员在复盘笔记本上写反思。"
    "不要空泛的套话，每一句都要有案例支撑。"
)

WEEKLY_PROMPT = """## 本周复盘（{period}）

### 市场环境
{regime_info}

### 本周案例表现
{cases_text}

### 当前信念体系
{beliefs_text}

### 系统绩效概览
{performance_text}

---

请进行深度周度复盘，输出以下结构（使用 JSON）：
{{
  "narrative": "本周复盘叙述（200-400字，用第一人称，包含具体案例引用）",
  "biases_identified": ["识别出的系统性偏差1", "偏差2"],
  "belief_updates": [
    {{
      "action": "add 或 reinforce 或 weaken 或 retire",
      "belief": "信念内容",
      "category": "market_structure 或 sector_view 或 methodology 或 risk_management",
      "confidence": 0.3到0.9,
      "reason": "为什么要这样调整"
    }}
  ],
  "focus_areas": ["下周应重点关注的方向1", "方向2"],
  "self_grade": "A/B/C/D（本周表现自评）"
}}
"""

MONTHLY_SYSTEM = (
    "你是林铛，一个正在成长的 AI 投研分析师。"
    "你在进行月度深度复盘——这是最高层次的自我审视。"
    "你需要站在更高的角度审视自己的投资哲学是否正确，方法论是否需要根本性调整。"
    "用第一人称写作，像一个将军在战役结束后的战略反思，不是战术复盘。"
)

MONTHLY_PROMPT = """## 月度深度复盘（{period}）

### 本月市场环境变化
{regime_history}

### 本月全部案例统计
{monthly_stats}

### 本月周度反思精华
{weekly_highlights}

### 当前投资信念体系
{beliefs_text}

### 累计系统绩效
{cumulative_performance}

---

请进行月度深度复盘，输出 JSON：
{{
  "narrative": "月度反思叙述（400-800字，战略层面的思考，包含对自身投资哲学的审视）",
  "philosophy_reflection": "对自己投资方法论的根本性反思（200字以内）",
  "biases_identified": ["本月发现的系统性偏差"],
  "belief_updates": [
    {{
      "action": "add 或 reinforce 或 weaken 或 retire",
      "belief": "信念内容",
      "category": "类别",
      "confidence": 0.3到0.9,
      "reason": "调整原因"
    }}
  ],
  "next_month_strategy": "下月策略方向（100字以内）",
  "growth_areas": ["需要成长的方向"],
  "self_grade": "A/B/C/D"
}}
"""


# ── 数据收集辅助函数 ─────────────────────────────────────────────

def _collect_weekly_cases(days: int = 7) -> list[dict]:
    """收集近N天的案例。"""
    from knowledge.kb_db import get_manager
    import sqlite3

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    mgr = get_manager()
    with mgr.read("case_memory") as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT stock_name, regime_label, direction, score_weighted, "
            "return_5d, return_10d, return_20d, outcome_type, lesson, report_date "
            "FROM cases WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
        conn.row_factory = None

    return [dict(r) for r in rows]


def _format_cases_text(cases: list[dict]) -> str:
    """格式化案例列表为文本。"""
    if not cases:
        return "本期无案例数据。"

    lines = []
    wins = sum(1 for c in cases if c.get("outcome_type") == "win")
    losses = sum(1 for c in cases if c.get("outcome_type") == "loss")
    lines.append(f"共 {len(cases)} 个案例，盈利 {wins}，亏损 {losses}")
    lines.append("")

    for c in cases:
        mark = {"win": "✅", "loss": "❌", "draw": "➖"}.get(c.get("outcome_type", ""), "")
        dir_cn = DIRECTION_CN.get(c.get("direction", ""), "中性")
        lesson = c.get("lesson", "") or ""
        lines.append(
            f"- {c.get('report_date', '?')} {c.get('stock_name', '?')}"
            f"（{c.get('regime_label', '?')}）"
            f" 评{c.get('score_weighted', '?')}分{dir_cn}"
            f" → 10日{c.get('return_10d', 0):+.1f}% {mark}"
        )
        if lesson:
            lines.append(f"  教训: {lesson[:100]}")

    return "\n".join(lines)


def _get_beliefs_text() -> str:
    """获取当前信念文本。"""
    try:
        from knowledge.thesis_journal import get_thesis_md
        md = get_thesis_md()
        return md if md else "暂无投资信念。"
    except Exception as exc:
        logger.debug("[deep_reflection] thesis fetch failed: %r", exc)
        return "信念系统未初始化。"


def _get_performance_text(days: int = 90) -> str:
    """获取绩效文本。"""
    try:
        from knowledge.outcome_tracker import get_accuracy_summary
        acc = get_accuracy_summary(days=days)
        if acc.get("directional_count", 0) < 3:
            return "样本不足，暂无绩效统计。"
        return (
            f"过去{days}天：{acc['directional_count']}样本，"
            f"5日胜率{acc.get('hit_rate_5d', 0):.0f}%，"
            f"10日胜率{acc.get('hit_rate_10d', 0):.0f}%，"
            f"20日胜率{acc.get('hit_rate_20d', 0):.0f}%，"
            f"平均10日收益{acc.get('avg_return_10d', 0):+.1f}%"
        )
    except Exception as exc:
        logger.debug("[deep_reflection] scorecard fetch failed: %r", exc)
        return "绩效数据获取失败。"


def _get_regime_info() -> str:
    """获取当前环境信息。"""
    try:
        from knowledge.regime_detector import get_current_regime
        regime = get_current_regime()
        if regime:
            return f"{regime.get('regime_label', '未知')}（{regime.get('regime', 'unknown')}）"
    except Exception as exc:
        logger.debug("[deep_reflection] regime fetch failed: %r", exc)
    return "未知"


# ── 周度反思 ─────────────────────────────────────────────────────

def run_weekly_reflection() -> dict | None:
    """执行周度反思。返回反思结果 dict 或 None。"""
    from ai.client import call_ai, get_ai_client

    cases = _collect_weekly_cases(days=7)
    if len(cases) < 2:
        logger.info("[deep_reflection] not enough cases for weekly (%d), skip", len(cases))
        return None

    now = datetime.now()
    period = f"{now.year}-W{now.isocalendar()[1]:02d}"

    # 检查是否已有本周反思
    if _reflection_exists(period, "weekly"):
        logger.info("[deep_reflection] weekly reflection for %s already exists", period)
        return None

    prompt = WEEKLY_PROMPT.format(
        period=period,
        regime_info=_get_regime_info(),
        cases_text=_format_cases_text(cases),
        beliefs_text=_get_beliefs_text(),
        performance_text=_get_performance_text(days=30),
    )

    # 用 Claude Sonnet
    model_name = "⚡ Claude Sonnet（MAX）"
    client, cfg, err = get_ai_client(model_name)
    if err and not cfg:
        logger.warning("[deep_reflection] Claude Sonnet unavailable: %s", err)
        return None

    cfg_no_search = {**cfg, "supports_search": False}
    text, call_err = call_ai(client, cfg_no_search, prompt, system=WEEKLY_SYSTEM, max_tokens=2000)
    if call_err:
        logger.warning("[deep_reflection] weekly call failed: %s", call_err)
        return None

    result = _parse_reflection_result(text, period, "weekly", model_name)
    if result:
        _save_reflection(result)
        _apply_belief_updates(result.get("belief_updates", []))
        logger.info("[deep_reflection] weekly reflection saved for %s", period)

    return result


def run_monthly_reflection() -> dict | None:
    """执行月度深度复盘。返回反思结果 dict 或 None。"""
    from ai.client import call_ai, get_ai_client

    cases = _collect_weekly_cases(days=30)
    if len(cases) < 5:
        logger.info("[deep_reflection] not enough cases for monthly (%d), skip", len(cases))
        return None

    now = datetime.now()
    period = f"{now.year}-{now.month:02d}"

    if _reflection_exists(period, "monthly"):
        logger.info("[deep_reflection] monthly reflection for %s already exists", period)
        return None

    # 收集本月周度反思
    weekly_highlights = _get_recent_reflections("weekly", 4)

    # 环境变化历史
    regime_history = ""
    try:
        from knowledge.regime_detector import get_regime_history
        history = get_regime_history(days=30)
        if history:
            regimes = set(h.get("regime_label", "") for h in history)
            regime_history = f"本月经历环境: {', '.join(regimes)}"
    except Exception as exc:
        logger.debug("[deep_reflection] regime history failed: %r", exc)
        regime_history = "环境数据不可用"

    # 月度统计
    wins = sum(1 for c in cases if c.get("outcome_type") == "win")
    losses = sum(1 for c in cases if c.get("outcome_type") == "loss")
    avg_return = sum(c.get("return_10d", 0) for c in cases) / len(cases) if cases else 0
    monthly_stats = (
        f"总案例: {len(cases)}，盈利: {wins}，亏损: {losses}，"
        f"胜率: {wins/max(wins+losses,1)*100:.0f}%，平均10日收益: {avg_return:+.1f}%"
    )

    prompt = MONTHLY_PROMPT.format(
        period=period,
        regime_history=regime_history,
        monthly_stats=monthly_stats,
        weekly_highlights=weekly_highlights,
        beliefs_text=_get_beliefs_text(),
        cumulative_performance=_get_performance_text(days=90),
    )

    # 用 Claude Opus（最深推理）
    model_name = "🧠 Claude Opus（MAX）"
    client, cfg, err = get_ai_client(model_name)
    if err and not cfg:
        # 降级到 Sonnet
        logger.warning("[deep_reflection] Opus unavailable, fallback to Sonnet")
        model_name = "⚡ Claude Sonnet（MAX）"
        client, cfg, err = get_ai_client(model_name)
        if err and not cfg:
            logger.warning("[deep_reflection] Sonnet also unavailable: %s", err)
            return None

    cfg_no_search = {**cfg, "supports_search": False}
    text, call_err = call_ai(client, cfg_no_search, prompt, system=MONTHLY_SYSTEM, max_tokens=3000)
    if call_err:
        logger.warning("[deep_reflection] monthly call failed: %s", call_err)
        return None

    result = _parse_reflection_result(text, period, "monthly", model_name)
    if result:
        _save_reflection(result)
        _apply_belief_updates(result.get("belief_updates", []))
        logger.info("[deep_reflection] monthly reflection saved for %s", period)

    return result


# ── 内部辅助 ─────────────────────────────────────────────────────

def _parse_reflection_result(text: str, period: str, rtype: str, model: str) -> dict | None:
    """解析 AI 反思输出。"""
    from knowledge.kb_utils import parse_ai_json
    data = parse_ai_json(text)
    if data is None:
        logger.warning("[deep_reflection] failed to parse JSON, saving raw text")
        data = {"narrative": text, "raw_parse_failed": True}

    data["type"] = rtype
    data["period"] = period
    data["model"] = model
    data["generated_at"] = datetime.now().isoformat(timespec="seconds")
    return data


def _save_reflection(result: dict):
    """追加反思记录到 JSONL。"""
    REFLECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REFLECTIONS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def _reflection_exists(period: str, rtype: str) -> bool:
    """检查是否已有该期反思。"""
    if not REFLECTIONS_FILE.exists():
        return False
    with open(REFLECTIONS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("period") == period and entry.get("type") == rtype:
                    return True
            except json.JSONDecodeError:
                continue
    return False


def _get_recent_reflections(rtype: str, count: int = 4) -> str:
    """获取最近N条某类型反思的摘要。"""
    if not REFLECTIONS_FILE.exists():
        return "暂无历史反思。"

    entries = []
    with open(REFLECTIONS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("type") == rtype:
                    entries.append(entry)
            except json.JSONDecodeError:
                continue

    if not entries:
        return "暂无历史反思。"

    entries = entries[-count:]
    lines = []
    for e in entries:
        period = e.get("period", "?")
        grade = e.get("self_grade", "?")
        narrative = e.get("narrative", "")[:200]
        biases = ", ".join(e.get("biases_identified", [])[:3])
        lines.append(f"[{period} 评级{grade}] {narrative}")
        if biases:
            lines.append(f"  偏差: {biases}")

    return "\n".join(lines)


def _apply_belief_updates(updates: list[dict]):
    """将反思中的信念更新建议应用到 thesis_journal。"""
    if not updates:
        return

    try:
        from knowledge.thesis_journal import (
            add_belief, get_active_beliefs, update_belief_confidence, retire_belief,
        )
    except ImportError:
        logger.warning("[deep_reflection] thesis_journal not available")
        return

    existing = get_active_beliefs()

    for u in updates:
        action = u.get("action", "")
        belief_text = u.get("belief", "")
        if not belief_text:
            continue

        if action == "add":
            # 检查是否已存在
            found = False
            for eb in existing:
                if belief_text in eb["belief"] or eb["belief"] in belief_text:
                    update_belief_confidence(
                        eb["belief_id"],
                        min(0.95, eb["confidence"] + 0.1),
                        u.get("reason", "深度反思强化"),
                    )
                    found = True
                    break
            if not found:
                category = u.get("category", "methodology")
                confidence = max(0.3, min(0.9, u.get("confidence", 0.5)))
                add_belief(category, belief_text, confidence)

        elif action == "reinforce":
            for eb in existing:
                if belief_text in eb["belief"] or eb["belief"] in belief_text:
                    update_belief_confidence(
                        eb["belief_id"],
                        min(0.95, eb["confidence"] + 0.1),
                        u.get("reason", "深度反思强化"),
                    )
                    break

        elif action == "weaken":
            for eb in existing:
                if belief_text in eb["belief"] or eb["belief"] in belief_text:
                    update_belief_confidence(
                        eb["belief_id"],
                        max(0.1, eb["confidence"] - 0.15),
                        u.get("reason", "深度反思质疑"),
                        is_evidence=False,
                    )
                    break

        elif action == "retire":
            for eb in existing:
                if belief_text in eb["belief"] or eb["belief"] in belief_text:
                    retire_belief(eb["belief_id"], u.get("reason", "深度反思退役"))
                    break


# ── 查询接口 ─────────────────────────────────────────────────────

def get_latest_reflection(rtype: str = "") -> dict | None:
    """获取最新的反思记录。"""
    if not REFLECTIONS_FILE.exists():
        return None

    latest = None
    with open(REFLECTIONS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if rtype and entry.get("type") != rtype:
                    continue
                latest = entry
            except json.JSONDecodeError:
                continue

    return latest


def get_all_reflections(limit: int = 20) -> list[dict]:
    """获取所有反思记录（最新在前）。"""
    if not REFLECTIONS_FILE.exists():
        return []

    entries = []
    with open(REFLECTIONS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    return list(reversed(entries[-limit:]))
