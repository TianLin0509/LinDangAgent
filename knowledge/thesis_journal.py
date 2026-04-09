# -*- coding: utf-8 -*-
"""投资信念系统 — 维护分身的演化投资哲学

借鉴 OpenClaw 的"活文档"记忆模式：信念不只是数据库记录，
更会生成 THESIS.md 活文档直接注入 prompt。

核心设计：
  - 每条信念有置信度（0-1），随证据积累升降
  - 所有变更留审计轨迹（belief_updates）
  - 定期用 Claude Sonnet 从案例教训中提炼/更新信念
  - 生成 THESIS.md 活文档供 injector 注入
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from knowledge.kb_config import KNOWLEDGE_DIR, BELIEF_CATEGORIES, DIRECTION_CN

logger = logging.getLogger(__name__)

THESIS_MD_PATH = KNOWLEDGE_DIR / "THESIS.md"


# ── 信念 CRUD ────────────────────────────────────────────────────

def add_belief(category: str, belief: str, confidence: float = 0.5,
               source_cases: list[str] | None = None) -> str:
    """添加一条新信念。返回 belief_id。"""
    belief_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat(timespec="seconds")

    from knowledge.kb_db import get_manager
    mgr = get_manager()
    with mgr.write("thesis_journal") as conn:
        conn.execute(
            "INSERT INTO beliefs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (belief_id, category, belief, confidence, 1, 0,
             now, now, json.dumps(source_cases or []), 1),
        )
        conn.execute(
            "INSERT INTO belief_updates VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4())[:8], belief_id, now, 0, confidence,
             "初始形成"),
        )

    logger.info("[thesis] added belief: %s (%.1f)", belief[:40], confidence)
    return belief_id


def update_belief_confidence(belief_id: str, new_confidence: float,
                             reason: str, is_evidence: bool = True):
    """更新信念置信度，留审计轨迹。"""
    now = datetime.now().isoformat(timespec="seconds")
    new_confidence = max(0.0, min(1.0, new_confidence))

    from knowledge.kb_db import get_manager
    mgr = get_manager()
    with mgr.write("thesis_journal") as conn:
        row = conn.execute(
            "SELECT confidence, evidence_count, counter_evidence FROM beliefs WHERE belief_id=?",
            (belief_id,),
        ).fetchone()
        if not row:
            return

        old_conf = row[0]
        ev_count = row[1] + (1 if is_evidence else 0)
        counter = row[2] + (0 if is_evidence else 1)

        conn.execute(
            "UPDATE beliefs SET confidence=?, evidence_count=?, counter_evidence=?, "
            "last_updated=? WHERE belief_id=?",
            (new_confidence, ev_count, counter, now, belief_id),
        )
        conn.execute(
            "INSERT INTO belief_updates VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4())[:8], belief_id, now, old_conf, new_confidence, reason),
        )

    logger.info("[thesis] updated belief %s: %.2f -> %.2f (%s)", belief_id, old_conf, new_confidence, reason[:30])


def retire_belief(belief_id: str, reason: str = ""):
    """退役一条信念（不删除，标记为不活跃）。"""
    from knowledge.kb_db import get_manager
    now = datetime.now().isoformat(timespec="seconds")
    mgr = get_manager()
    with mgr.write("thesis_journal") as conn:
        conn.execute("UPDATE beliefs SET active=0, last_updated=? WHERE belief_id=?",
                     (now, belief_id))
        conn.execute(
            "INSERT INTO belief_updates VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4())[:8], belief_id, now, None, 0, f"退役: {reason}"),
        )


def get_active_beliefs(category: str = "", min_confidence: float = 0.0) -> list[dict]:
    """获取活跃信念列表。"""
    from knowledge.kb_db import get_manager
    mgr = get_manager()
    with mgr.read("thesis_journal") as conn:
        query = "SELECT * FROM beliefs WHERE active=1"
        params = []
        if category:
            query += " AND category=?"
            params.append(category)
        if min_confidence > 0:
            query += " AND confidence>=?"
            params.append(min_confidence)
        query += " ORDER BY confidence DESC, last_updated DESC"

        rows = conn.execute(query, params).fetchall()

    return [
        {
            "belief_id": r[0], "category": r[1], "belief": r[2],
            "confidence": r[3], "evidence_count": r[4], "counter_evidence": r[5],
            "first_formed": r[6], "last_updated": r[7],
            "source_cases": json.loads(r[8]),
        }
        for r in rows
    ]


def get_beliefs_for_context(sectors: list[str] = None, regime: str = "") -> list[dict]:
    """获取与当前分析相关的信念（供 injector 使用）。

    优先返回高置信度 + 与板块/环境相关的信念。
    """
    all_beliefs = get_active_beliefs(min_confidence=0.3)
    if not all_beliefs:
        return []

    scored = []
    for b in all_beliefs:
        relevance = b["confidence"]
        text = b["belief"].lower()

        # 板块相关性加分
        if sectors:
            for s in sectors:
                if s.lower() in text:
                    relevance += 0.3
                    break

        # 环境相关性加分
        regime_keywords = {
            "bull": ["牛市", "上涨", "乐观"],
            "bear": ["熊市", "下跌", "悲观", "防守"],
            "shock": ["震荡", "波动", "区间"],
            "rotation": ["轮动", "切换", "风格"],
        }
        if regime and regime in regime_keywords:
            for kw in regime_keywords[regime]:
                if kw in text:
                    relevance += 0.2
                    break

        # 方法论和风控始终相关
        if b["category"] in ("methodology", "risk_management"):
            relevance += 0.1

        scored.append((relevance, b))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scored[:6]]  # 最多6条


def get_belief_history(belief_id: str) -> list[dict]:
    """获取信念的变更历史。"""
    from knowledge.kb_db import get_manager
    mgr = get_manager()
    with mgr.read("thesis_journal") as conn:
        rows = conn.execute(
            "SELECT update_date, old_confidence, new_confidence, reason "
            "FROM belief_updates WHERE belief_id=? ORDER BY update_date",
            (belief_id,),
        ).fetchall()

    return [
        {"date": r[0], "old": r[1], "new": r[2], "reason": r[3]}
        for r in rows
    ]


# ── 从案例教训中提炼信念（Claude Sonnet 驱动）────────────────────

THESIS_SYSTEM = (
    "你是一个投资哲学分析师。你的任务是从一批股票分析的案例教训中提炼出可复用的投资信念。"
    "用第一人称写作，语气冷峻务实。"
    "只输出 JSON 数组，不要输出其他内容。"
)

THESIS_EXTRACT_PROMPT = """以下是我最近的股票分析案例教训，请从中提炼出可复用的投资信念。

【当前市场环境】{regime_label}

【近期案例教训】
{lessons_text}

请提炼出1-3条投资信念（如果教训不足以支撑信念，可以少于1条），输出严格JSON数组：
[
  {{
    "category": "market_structure 或 sector_view 或 methodology 或 risk_management",
    "belief": "简洁的信念描述（30字以内）",
    "confidence": 0.3到0.8之间的浮点数,
    "reasoning": "为什么这条信念成立（基于上述案例）"
  }}
]

要求：
- 信念必须有案例支撑，不要凭空编造
- 置信度反映支撑证据的强度：1-2个案例给0.3-0.4，3-5个给0.5-0.6，更多给0.6-0.8
- 如果案例中有明显矛盾，不要硬提炼，跳过
- 偏好可操作的方法论和风控信念，而非泛泛的市场判断
"""


def update_beliefs_from_cases(max_lessons: int = 20):
    """从近期案例教训中提炼/更新信念。在 scheduler 中定期调用。"""
    from ai.client import call_ai, get_ai_client
    from knowledge.kb_db import get_manager
    from knowledge.regime_detector import get_current_regime

    # 获取近期有教训的案例
    mgr = get_manager()
    cutoff = (datetime.now() - timedelta(days=14)).isoformat()
    with mgr.read("case_memory") as case_conn:
        rows = case_conn.execute(
            "SELECT stock_name, regime_label, direction, score_weighted, "
            "return_10d, outcome_type, lesson "
            "FROM cases WHERE lesson IS NOT NULL AND lesson != '' "
            "AND created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (cutoff, max_lessons),
        ).fetchall()

    if len(rows) < 3:
        logger.info("[thesis] not enough recent lessons (%d), skip", len(rows))
        return 0

    # 构建教训文本
    lessons_text = ""
    for r in rows:
        mark = {"win": "✅", "loss": "❌", "draw": "➖"}.get(r[5], "")
        dir_cn = DIRECTION_CN.get(r[2], "中性")
        ret_10d = r[4] if r[4] is not None else 0
        lessons_text += f"- {r[0]}({r[1]}) 评{r[3]}分{dir_cn} → 10日{ret_10d:+.1f}% {mark}: {r[6]}\n"

    regime = get_current_regime()
    regime_label = regime.get("regime_label", "未知") if regime else "未知"

    prompt = THESIS_EXTRACT_PROMPT.format(
        regime_label=regime_label,
        lessons_text=lessons_text,
    )

    # 用 Claude Sonnet 提炼
    model_name = "⚡ Claude Sonnet（MAX）"
    client, cfg, err = get_ai_client(model_name)
    if err and not cfg:
        logger.warning("[thesis] Claude Sonnet unavailable: %s", err)
        return 0

    cfg_no_search = {**cfg, "supports_search": False}
    text, call_err = call_ai(client, cfg_no_search, prompt, system=THESIS_SYSTEM, max_tokens=1000)
    if call_err:
        logger.warning("[thesis] extraction failed: %s", call_err)
        return 0

    # 解析 JSON
    from knowledge.kb_utils import parse_ai_json
    new_beliefs = parse_ai_json(text)
    if not isinstance(new_beliefs, list):
        logger.warning("[thesis] failed to parse AI JSON or not a list")
        return 0

    # 去重：检查是否已有相似信念
    existing = get_active_beliefs()
    added = 0
    for nb in new_beliefs:
        belief_text = nb.get("belief", "")
        if not belief_text or len(belief_text) < 5:
            continue

        # 简单去重：检查是否有包含关系
        is_duplicate = False
        for eb in existing:
            if (belief_text in eb["belief"] or eb["belief"] in belief_text
                    or _similarity_check(belief_text, eb["belief"])):
                # 已有相似信念，增加证据
                new_conf = min(0.95, eb["confidence"] + 0.05)
                update_belief_confidence(
                    eb["belief_id"], new_conf,
                    f"新证据支持: {nb.get('reasoning', '')[:60]}",
                    is_evidence=True,
                )
                is_duplicate = True
                break

        if not is_duplicate:
            category = nb.get("category", "methodology")
            if category not in BELIEF_CATEGORIES:
                category = "methodology"
            confidence = max(0.3, min(0.8, nb.get("confidence", 0.5)))
            add_belief(category, belief_text, confidence)
            added += 1

    # 生成 THESIS.md 活文档
    _regenerate_thesis_md()

    logger.info("[thesis] update complete: %d new beliefs added", added)
    return added


def _similarity_check(a: str, b: str) -> bool:
    """简单的文本相似度检查（共同字符占比）。"""
    if not a or not b:
        return False
    common = set(a) & set(b)
    shorter = min(len(set(a)), len(set(b)))
    if shorter == 0:
        return False
    return len(common) / shorter > 0.7


# ── 活文档生成 ───────────────────────────────────────────────────

def _regenerate_thesis_md():
    """生成 THESIS.md 活文档（类似 OpenClaw 的 SOUL.md）。"""
    beliefs = get_active_beliefs()
    if not beliefs:
        return

    lines = [
        "# 林铛的投资信念",
        f"*最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
    ]

    by_category = {}
    for b in beliefs:
        cat = b["category"]
        by_category.setdefault(cat, []).append(b)

    for cat_key, cat_name in BELIEF_CATEGORIES.items():
        cat_beliefs = by_category.get(cat_key, [])
        if not cat_beliefs:
            continue

        lines.append(f"## {cat_name}")
        for b in sorted(cat_beliefs, key=lambda x: x["confidence"], reverse=True):
            conf_bar = "●" * int(b["confidence"] * 5) + "○" * (5 - int(b["confidence"] * 5))
            lines.append(f"- [{conf_bar}] {b['belief']}")
            if b["counter_evidence"] > 0:
                lines.append(f"  *(支持{b['evidence_count']}次，反对{b['counter_evidence']}次)*")
        lines.append("")

    THESIS_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    THESIS_MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("[thesis] THESIS.md regenerated with %d beliefs", len(beliefs))


def get_thesis_md() -> str:
    """读取 THESIS.md 活文档内容。"""
    if THESIS_MD_PATH.exists():
        return THESIS_MD_PATH.read_text(encoding="utf-8")
    return ""


def get_belief_count() -> int:
    """获取活跃信念总数。"""
    from knowledge.kb_db import get_manager
    mgr = get_manager()
    with mgr.read("thesis_journal") as conn:
        return conn.execute("SELECT COUNT(*) FROM beliefs WHERE active=1").fetchone()[0]
