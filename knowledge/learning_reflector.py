# knowledge/learning_reflector.py
# -*- coding: utf-8 -*-
"""统一学习引擎 — Round 2-3: Opus 反思 + 交叉审视。

Round 2: 统一反思 — Opus 分析回测结果，输出结构化调整建议
Round 3: 交叉审视 — 质疑→答辩→仲裁，多轮 Opus 审视
"""

import json
import logging
import re
from pathlib import Path

from knowledge.kb_config import KNOWLEDGE_DIR
from knowledge.learning_config import load_production_tree, DIMENSIONS

logger = logging.getLogger(__name__)


# ── Prompt 模板 ──────────────────────────────────────────────────

REFLECT_SYSTEM = """你是投研系统的首席策略官。以下是最近一批回测结果和当前系统配置。

请从结果中找出系统性问题，并提出改进建议。你可以调整以下任何一项或多项：
1. 四维权重分配 (type=weight)
2. 五条修正规则的阈值或逻辑 (type=rule)
3. 决策树的分支结构——增/删/改问题节点 (type=tree)
4. Round 1 或 Round 2 的 system prompt 措辞 (type=prompt)

要求：
- 每条建议必须有数据支撑（引用具体案例或统计数字）
- 说明预期效果（"这个调整预计能避免 X 类失误"）
- 标注风险（"但可能导致 Y 类场景误判"）
- 如果你认为当前配置已经足够好，可以不改——不要为了改而改
- confidence 用 high/medium/low 标注

输出格式（必须严格遵守）：
<<<PROPOSALS>>>
[
  {
    "id": "P1",
    "type": "weight|rule|tree|prompt",
    "target": "具体目标（如 '技术面权重' 或 'bucket_effect 阈值'）",
    "current_value": "当前值",
    "proposed_value": "建议值",
    "evidence": "支撑数据（引用案例编号或统计）",
    "expected_effect": "预期效果",
    "risk": "潜在风险",
    "confidence": "high|medium|low"
  }
]
<<<END_PROPOSALS>>>

如果没有需要调整的，输出空数组：
<<<PROPOSALS>>>
[]
<<<END_PROPOSALS>>>"""

CHALLENGE_SYSTEM = """你是投研系统的风控官。你的职责是审查以下调整建议，专门找过拟合风险和样本偏差。

对每条 proposal，给出评判：
- pass: 建议合理，可以采纳
- concern: 有疑虑，需要提议者补充证据
- reject: 建议有明显问题，不应采纳

评判标准：
1. 样本量是否足够支撑结论？（<10个案例的统计不可信）
2. 是否存在过拟合风险？（针对个别案例的调整可能损害整体）
3. 调整幅度是否合理？（大幅调整需要更强的证据）
4. 是否考虑了副作用？（改善A类场景可能恶化B类场景）

输出格式：
<<<VERDICTS>>>
[
  {
    "proposal_id": "P1",
    "verdict": "pass|concern|reject",
    "reason": "判断理由",
    "question": "如果 concern，向提议者提出的具体问题"
  }
]
<<<END_VERDICTS>>>"""

DEFENSE_SYSTEM = """你是投研系统的策略官。风控官对你的调整建议提出了质疑。请针对每条疑虑：
- 如果你有补充证据，给出证据并维持建议
- 如果质疑有理，承认问题并修改建议（或撤回）

输出格式：
<<<DEFENSE>>>
[
  {
    "proposal_id": "P1",
    "action": "maintain|revise|withdraw",
    "response": "回应内容",
    "revised_value": "如果 revise，修改后的值"
  }
]
<<<END_DEFENSE>>>"""

ARBITRATE_SYSTEM = """你是投研系统的最终裁决人。策略官和风控官对以下建议存在分歧。
请综合双方论点，做出最终决定。

输出格式：
<<<FINAL>>>
[
  {
    "proposal_id": "P1",
    "decision": "adopt|reject",
    "reason": "裁决理由"
  }
]
<<<END_FINAL>>>"""


# ── 材料构建 ──────────────────────────────────────────────────────

def _build_reflection_material(train_results: list, stats: dict) -> str:
    """构建给 Opus 的反思材料。"""
    tree = load_production_tree()

    # 排序找典型案例
    sorted_by_alpha = sorted(train_results, key=lambda r: r.get("excess_return", 0))
    failures = sorted_by_alpha[:10]  # 最差的 10 个
    successes = sorted_by_alpha[-5:]  # 最好的 5 个
    boundary = [r for r in train_results if 45 <= r.get("weighted", 50) <= 55][:5]

    def fmt_case(r, idx):
        return (
            f"案例#{idx}: {r['stock_name']}({r['ts_code']}) {r['exam_date']}\n"
            f"  评分: 基本面{r['scores'].get('基本面', '?')} 预期差{r['scores'].get('预期差', '?')} "
            f"资金面{r['scores'].get('资金面', '?')} 技术面{r['scores'].get('技术面', '?')} "
            f"综合{r['weighted']}\n"
            f"  方向: {r['direction_cn']} | 实际α: {r['excess_return']:+.1f}% | "
            f"判定: {'✅' if r['verdict'] == 'hit' else '❌'}\n"
            f"  摘要: {r.get('analysis_summary', '')[:200]}"
        )

    parts = [
        "# 回测反思材料\n",
        f"## 整体统计",
        f"- 总数: {stats['total']} | 命中: {stats['hits']} | 胜率: {stats['hit_rate']}%",
        f"- 分方向: {json.dumps(stats['by_direction'], ensure_ascii=False)}",
        f"- 分板块: {json.dumps(stats['by_sector'], ensure_ascii=False)}",
        f"- 分类别: {json.dumps(stats['by_category'], ensure_ascii=False)}",
        f"\n## 典型失败案例 (Top10 最差α)",
    ]
    for i, r in enumerate(failures):
        parts.append(fmt_case(r, i + 1))

    parts.append("\n## 典型成功案例 (Top5 最佳α)")
    for i, r in enumerate(successes):
        parts.append(fmt_case(r, i + 1))

    if boundary:
        parts.append("\n## 边界案例 (综合评分 45-55)")
        for i, r in enumerate(boundary):
            parts.append(fmt_case(r, i + 1))

    parts.append("\n## 当前配置快照")
    parts.append(f"```json\n{json.dumps(tree, ensure_ascii=False, indent=2)}\n```")

    # 历史学习记录
    from knowledge.learning_config import LEARNING_LOG_DIR
    log_files = sorted(LEARNING_LOG_DIR.glob("*.json"))[-3:] if LEARNING_LOG_DIR.exists() else []
    if log_files:
        parts.append("\n## 历史学习记录 (最近3次)")
        for f in log_files:
            try:
                log = json.loads(f.read_text(encoding="utf-8"))
                parts.append(f"- {f.stem}: {log.get('summary', '无摘要')}")
            except Exception:
                pass

    return "\n".join(parts)


def _parse_proposals(text: str) -> list[dict]:
    """从 Opus 输出中解析 proposals。"""
    m = re.search(r"<<<PROPOSALS>>>(.*?)<<<END_PROPOSALS>>>", text, re.DOTALL)
    if not m:
        logger.warning("[learn] no PROPOSALS block found")
        return []
    try:
        return json.loads(m.group(1).strip())
    except json.JSONDecodeError as exc:
        logger.warning("[learn] failed to parse proposals JSON: %s", exc)
        return []


def _parse_verdicts(text: str) -> list[dict]:
    m = re.search(r"<<<VERDICTS>>>(.*?)<<<END_VERDICTS>>>", text, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return []


def _parse_defense(text: str) -> list[dict]:
    m = re.search(r"<<<DEFENSE>>>(.*?)<<<END_DEFENSE>>>", text, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return []


def _parse_final(text: str) -> list[dict]:
    m = re.search(r"<<<FINAL>>>(.*?)<<<END_FINAL>>>", text, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return []


def _call_opus(prompt: str, system: str) -> str:
    """调用 Claude Opus，兼容 CLI 和 API 两种 provider。"""
    from ai.client import call_ai, call_ai_stream, get_ai_client

    model_name = "🧠 Claude Opus（MAX）"
    client, cfg, err = get_ai_client(model_name)
    if err and not cfg:
        logger.error("[learn] Opus unavailable: %s", err)
        return ""

    cfg_no_search = {**cfg, "supports_search": False}

    # CLI providers need call_ai_stream
    if cfg.get("provider") in ("gemini_cli", "codex_cli", "claude_cli"):
        try:
            stream = call_ai_stream(client, cfg_no_search, prompt, system=system, max_tokens=4000)
            for _ in stream:
                pass
            return stream.full_text or ""
        except Exception as exc:
            logger.error("[learn] Opus stream call failed: %s", exc)
            return ""
    else:
        text, call_err = call_ai(client, cfg_no_search, prompt, system=system, max_tokens=4000)
        if call_err:
            logger.error("[learn] Opus call failed: %s", call_err)
            return ""
        return text or ""


# ── Round 2: 统一反思 ────────────────────────────────────────────

def run_reflection(train_results: list, stats: dict,
                   progress_cb=None) -> list[dict]:
    """Round 2: Opus 分析回测结果，输出调整建议。

    返回: proposals 列表
    """
    if progress_cb:
        progress_cb("Round 2: Opus 统一反思...")

    material = _build_reflection_material(train_results, stats)
    text = _call_opus(material, REFLECT_SYSTEM)

    proposals = _parse_proposals(text)
    if progress_cb:
        progress_cb(f"Round 2 完成: {len(proposals)} 条建议")

    return proposals


# ── Round 3: 交叉审视 ────────────────────────────────────────────

def run_cross_review(
    proposals: list[dict],
    train_results: list,
    stats: dict,
    progress_cb=None,
) -> list[dict]:
    """Round 3: 质疑→答辩→仲裁。

    返回: 最终采纳的 proposals 列表（只含 adopt 的）。
    """
    if not proposals:
        if progress_cb:
            progress_cb("Round 3: 无建议需要审视")
        return []

    # Step 1: 质疑者
    if progress_cb:
        progress_cb("Round 3 Step 1: 质疑者审查...")

    challenge_prompt = (
        f"# 待审查的调整建议\n\n"
        f"```json\n{json.dumps(proposals, ensure_ascii=False, indent=2)}\n```\n\n"
        f"# 回测统计\n"
        f"总数: {stats['total']} | 胜率: {stats['hit_rate']}%\n"
        f"分方向: {json.dumps(stats['by_direction'], ensure_ascii=False)}\n"
        f"分板块: {json.dumps(stats['by_sector'], ensure_ascii=False)}"
    )
    challenge_text = _call_opus(challenge_prompt, CHALLENGE_SYSTEM)
    verdicts = _parse_verdicts(challenge_text)

    # 快速路径: 全部通过
    concerns = [v for v in verdicts if v.get("verdict") == "concern"]
    rejects = [v for v in verdicts if v.get("verdict") == "reject"]

    if not concerns and not rejects:
        if progress_cb:
            progress_cb("Round 3: 质疑者全部通过，跳过答辩")
        return proposals

    # 标记被否决的
    rejected_ids = {v["proposal_id"] for v in rejects}
    surviving = [p for p in proposals if p.get("id") not in rejected_ids]

    if not concerns:
        if progress_cb:
            progress_cb(f"Round 3: {len(rejects)} 条否决，{len(surviving)} 条通过")
        return surviving

    # Step 2: 答辩
    if progress_cb:
        progress_cb(f"Round 3 Step 2: 答辩 ({len(concerns)} 条疑虑)...")

    defense_prompt = (
        f"# 你的原始建议\n```json\n{json.dumps(proposals, ensure_ascii=False, indent=2)}\n```\n\n"
        f"# 风控官的质疑\n```json\n{json.dumps(concerns, ensure_ascii=False, indent=2)}\n```"
    )
    defense_text = _call_opus(defense_prompt, DEFENSE_SYSTEM)
    defenses = _parse_defense(defense_text)

    # 处理答辩结果
    withdrawn_ids = {d["proposal_id"] for d in defenses if d.get("action") == "withdraw"}
    revised = {d["proposal_id"]: d for d in defenses if d.get("action") == "revise"}

    # 更新 proposals
    final_proposals = []
    still_disputed = []
    for p in surviving:
        pid = p.get("id")
        if pid in withdrawn_ids:
            continue
        if pid in revised:
            p = {**p, "proposed_value": revised[pid].get("revised_value", p.get("proposed_value"))}
            # 如果修改了，不再争议
            final_proposals.append(p)
        elif any(c["proposal_id"] == pid for c in concerns):
            # 维持了但仍有疑虑 → 交仲裁
            still_disputed.append(p)
        else:
            final_proposals.append(p)

    if not still_disputed:
        if progress_cb:
            progress_cb(f"Round 3: 答辩完成，{len(final_proposals)} 条通过")
        return final_proposals

    # Step 3: 仲裁
    if progress_cb:
        progress_cb(f"Round 3 Step 3: 仲裁 ({len(still_disputed)} 条分歧)...")

    concern_map = {c["proposal_id"]: c for c in concerns}
    defense_map = {d["proposal_id"]: d for d in defenses if d.get("action") == "maintain"}

    arbitrate_prompt = "# 待仲裁的分歧\n\n"
    for p in still_disputed:
        pid = p.get("id")
        arbitrate_prompt += (
            f"## {pid}\n"
            f"建议: {json.dumps(p, ensure_ascii=False)}\n"
            f"质疑: {json.dumps(concern_map.get(pid, {}), ensure_ascii=False)}\n"
            f"答辩: {json.dumps(defense_map.get(pid, {}), ensure_ascii=False)}\n\n"
        )

    arbitrate_text = _call_opus(arbitrate_prompt, ARBITRATE_SYSTEM)
    finals = _parse_final(arbitrate_text)

    adopted_ids = {f["proposal_id"] for f in finals if f.get("decision") == "adopt"}
    for p in still_disputed:
        if p.get("id") in adopted_ids:
            final_proposals.append(p)

    if progress_cb:
        progress_cb(f"Round 3 完成: {len(final_proposals)} 条最终采纳")

    return final_proposals
