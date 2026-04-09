# -*- coding: utf-8 -*-
"""个人知识库 — 从书籍/博客/视频中提炼的投资智慧

WISDOM.md = 外部知识（前人智慧），THESIS.md = 自身经验信念——互补不冲突。

设计：
  - SQLite 存储 + FTS5 全文搜索（不用向量数据库，知识量级不需要）
  - WISDOM.md 活文档自动生成，按分类展示
  - injector 按板块/环境检索最相关的智慧注入 prompt
  - used_count 追踪哪些智慧真正被用到
  - Claude Sonnet 从长文本中批量提炼核心智慧
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

from knowledge.kb_config import KNOWLEDGE_DIR, WISDOM_CATEGORIES, SOURCE_ICONS

logger = logging.getLogger(__name__)

WISDOM_MD_PATH = KNOWLEDGE_DIR / "WISDOM.md"


# ── 添加智慧 ─────────────────────────────────────────────────────

def add_wisdom(source_type: str, source_name: str, category: str,
               wisdom: str, context: str = "", tags: list[str] = None) -> str:
    """手动添加一条智慧。返回 wisdom_id。"""
    wisdom_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat(timespec="seconds")

    if category not in WISDOM_CATEGORIES:
        category = "general"
    if source_type not in SOURCE_ICONS:
        source_type = "experience"

    from knowledge.kb_db import get_manager
    mgr = get_manager()
    with mgr.write("wisdom") as conn:
        conn.execute(
            "INSERT INTO wisdom_entries VALUES (?,?,?,?,?,?,?,?,?)",
            (wisdom_id, source_type, source_name, category,
             wisdom, context, json.dumps(tags or [], ensure_ascii=False),
             now, 0),
        )

    _regenerate_wisdom_md()
    logger.info("[wisdom] added: [%s] %s — %s", source_name, wisdom[:40], category)
    return wisdom_id


# ── 批量提炼（Claude Sonnet 从长文本中提取）──────────────────────

EXTRACT_SYSTEM = (
    "你是一个投资知识提炼专家。从给定的文本中提取可操作的投资智慧。"
    "只输出 JSON 数组，不要输出其他内容。"
)

EXTRACT_PROMPT = """从以下文本中提取核心投资智慧，每条智慧是一个可以指导实际投资决策的原则或洞察。

来源类型：{source_type}
来源名称：{source_name}

--- 文本内容 ---
{text}
--- 文本结束 ---

输出严格 JSON 数组（提取 3-10 条最有价值的智慧）：
[
  {{
    "category": "valuation/timing/risk/psychology/sector/general",
    "wisdom": "一句话概括的投资智慧（20-50字）",
    "context": "原文相关段落或解释（50-100字，可选）",
    "tags": ["相关板块或概念标签"]
  }}
]

要求：
- 只提取可操作的投资原则，不要提取事实描述或背景知识
- wisdom 必须简洁有力，像格言一样可以直接用于指导决策
- 偏好 A 股适用的智慧，但通用投资原则也可以
- 去重：意思相近的只保留最精炼的一条
"""


def batch_extract_from_text(text: str, source_name: str,
                            source_type: str = "book") -> int:
    """用 Claude Sonnet 从长文本中批量提炼投资智慧。返回提取数量。"""
    from ai.client import call_ai, get_ai_client

    # 截断过长文本
    if len(text) > 8000:
        text = text[:8000] + "\n\n[...文本已截断...]"

    prompt = EXTRACT_PROMPT.format(
        source_type=source_type,
        source_name=source_name,
        text=text,
    )

    model_name = "⚡ Claude Sonnet（MAX）"
    client, cfg, err = get_ai_client(model_name)
    if err and not cfg:
        model_name = "🟤 豆包 · Seed 2.0 Mini"
        client, cfg, err = get_ai_client(model_name)
        if err and not cfg:
            logger.warning("[wisdom] no model available: %s", err)
            return 0

    cfg_no_search = {**cfg, "supports_search": False}
    raw, call_err = call_ai(client, cfg_no_search, prompt, system=EXTRACT_SYSTEM, max_tokens=2000)
    if call_err:
        logger.warning("[wisdom] extraction failed: %s", call_err)
        return 0

    # 解析 JSON
    from knowledge.kb_utils import parse_ai_json
    items = parse_ai_json(raw)
    if not isinstance(items, list):
        logger.warning("[wisdom] failed to parse AI JSON or not a list")
        return 0

    # 去重后添加
    existing = get_all_wisdom()
    existing_texts = {w["wisdom"] for w in existing}
    added = 0

    for item in items:
        wisdom_text = item.get("wisdom", "")
        if not wisdom_text or len(wisdom_text) < 5:
            continue
        if wisdom_text in existing_texts:
            continue
        # 简单相似度检查
        if any(_is_similar(wisdom_text, e) for e in existing_texts):
            continue

        category = item.get("category", "general")
        context = item.get("context", "")
        tags = item.get("tags", [])
        add_wisdom(source_type, source_name, category, wisdom_text, context, tags)
        existing_texts.add(wisdom_text)
        added += 1

    logger.info("[wisdom] extracted %d wisdom from %s", added, source_name[:30])
    return added


def _is_similar(a: str, b: str) -> bool:
    """简单中文文本相似度：共同字符占比。"""
    if not a or not b:
        return False
    sa, sb = set(a), set(b)
    shorter = min(len(sa), len(sb))
    if shorter == 0:
        return False
    return len(sa & sb) / shorter > 0.7


# ── 从 URL 学习 ──────────────────────────────────────────────────

def learn_from_url(url: str, source_name: str = "", source_type: str = "blog") -> dict:
    """从 URL 抓取文章内容，提炼投资智慧。"""
    from services.intel_analyze import fetch_article_content

    text, title, err = fetch_article_content(url)
    if err and len(text) < 200:
        return {"status": "error", "message": f"抓取失败: {err}"}

    name = source_name or title or url[:50]
    count = batch_extract_from_text(text, name, source_type)

    return {
        "status": "ok",
        "source_name": name,
        "extracted": count,
        "content_length": len(text),
    }


# ── 检索（供 injector 使用）─────────────────────────────────────

def get_wisdom_for_context(sectors: list[str] = None, regime: str = "",
                           top_k: int = 3) -> list[dict]:
    """按板块/环境检索最相关的智慧，并增加 used_count。"""
    all_wisdom = get_all_wisdom()
    if not all_wisdom:
        return []

    scored = []
    for w in all_wisdom:
        relevance = 0.0
        text = w["wisdom"].lower() + " " + " ".join(w.get("tags", []))

        # 板块匹配
        if sectors:
            for s in sectors:
                if s.lower() in text:
                    relevance += 2.0
                    break

        # 环境匹配
        regime_keywords = {
            "bull": ["牛市", "上涨", "追涨", "趋势"],
            "bear": ["熊市", "下跌", "止损", "防守", "现金"],
            "shock": ["震荡", "区间", "高抛低吸"],
        }
        if regime and regime in regime_keywords:
            for kw in regime_keywords[regime]:
                if kw in text:
                    relevance += 1.5
                    break

        # 通用高频分类始终加分（确保无板块匹配时也有输出）
        if w["category"] in ("risk", "psychology"):
            relevance += 1.0  # 提高权重，风控和心理始终重要
        elif w["category"] == "general":
            relevance += 0.5  # 通用投资哲学兜底

        # used_count 越高说明越有用
        relevance += min(w.get("used_count", 0) * 0.1, 0.5)

        scored.append((relevance, w))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [w for _, w in scored[:top_k]]

    # 确保至少返回 top_k 条（即使全部低分，也返回最优的）
    if not selected and all_wisdom:
        selected = all_wisdom[:top_k]

    # 更新 used_count
    if selected:
        from knowledge.kb_db import get_manager
        mgr = get_manager()
        with mgr.write("wisdom") as conn:
            for w in selected:
                conn.execute(
                    "UPDATE wisdom_entries SET used_count = used_count + 1 WHERE wisdom_id=?",
                    (w["wisdom_id"],),
                )

    return selected


def search_wisdom(query: str, limit: int = 10) -> list[dict]:
    """搜索智慧库（LIKE 模糊匹配，简单可靠）。"""
    from knowledge.kb_db import get_manager
    mgr = get_manager()
    with mgr.read("wisdom") as conn:
        rows = conn.execute(
            "SELECT wisdom_id, source_type, source_name, category, "
            "wisdom, context, tags, used_count "
            "FROM wisdom_entries "
            "WHERE wisdom LIKE ? OR context LIKE ? OR source_name LIKE ? OR tags LIKE ? "
            "LIMIT ?",
            (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", limit),
        ).fetchall()

    return [
        {
            "wisdom_id": r[0], "source_type": r[1], "source_name": r[2],
            "category": r[3], "wisdom": r[4], "context": r[5],
            "tags": json.loads(r[6]) if r[6] else [], "used_count": r[7],
        }
        for r in rows
    ]


# ── 查询 ─────────────────────────────────────────────────────────

def get_all_wisdom() -> list[dict]:
    from knowledge.kb_db import get_manager
    mgr = get_manager()
    with mgr.read("wisdom") as conn:
        rows = conn.execute(
            "SELECT wisdom_id, source_type, source_name, category, "
            "wisdom, context, tags, added_at, used_count "
            "FROM wisdom_entries ORDER BY category, added_at",
        ).fetchall()

    return [
        {
            "wisdom_id": r[0], "source_type": r[1], "source_name": r[2],
            "category": r[3], "wisdom": r[4], "context": r[5],
            "tags": json.loads(r[6]) if r[6] else [],
            "added_at": r[7], "used_count": r[8],
        }
        for r in rows
    ]


def get_wisdom_count() -> int:
    from knowledge.kb_db import get_manager
    mgr = get_manager()
    with mgr.read("wisdom") as conn:
        return conn.execute("SELECT COUNT(*) FROM wisdom_entries").fetchone()[0]


def get_wisdom_stats() -> dict:
    """获取智慧库统计。"""
    all_w = get_all_wisdom()
    if not all_w:
        return {"total": 0}

    sources = set()
    by_category = {}
    by_source_type = {}
    for w in all_w:
        sources.add(w["source_name"])
        cat = w["category"]
        by_category[cat] = by_category.get(cat, 0) + 1
        st = w["source_type"]
        by_source_type[st] = by_source_type.get(st, 0) + 1

    return {
        "total": len(all_w),
        "sources": len(sources),
        "by_category": {WISDOM_CATEGORIES.get(k, k): v for k, v in by_category.items()},
        "by_source_type": by_source_type,
        "most_used": sorted(all_w, key=lambda x: x["used_count"], reverse=True)[:3],
    }


# ── 活文档生成 ───────────────────────────────────────────────────

def _regenerate_wisdom_md():
    """生成 WISDOM.md 活文档。"""
    all_w = get_all_wisdom()
    if not all_w:
        if WISDOM_MD_PATH.exists():
            WISDOM_MD_PATH.unlink()
        return

    sources = set(w["source_name"] for w in all_w)
    source_types = set(w["source_type"] for w in all_w)
    type_counts = []
    for st in sorted(source_types):
        count = sum(1 for s in sources if any(w["source_type"] == st and w["source_name"] == s for w in all_w))
        icon = SOURCE_ICONS.get(st, "📄")
        type_counts.append(f"{count}{icon}")

    lines = [
        "# 林铛的投资智慧库",
        f"*来源: {' + '.join(type_counts)} | 共{len(all_w)}条*",
        "",
    ]

    by_cat = {}
    for w in all_w:
        by_cat.setdefault(w["category"], []).append(w)

    for cat_key, cat_name in WISDOM_CATEGORIES.items():
        cat_items = by_cat.get(cat_key, [])
        if not cat_items:
            continue

        lines.append(f"## {cat_name}")
        for w in cat_items:
            icon = SOURCE_ICONS.get(w["source_type"], "📄")
            used = f" (用{w['used_count']}次)" if w["used_count"] > 0 else ""
            lines.append(f"- {icon} [{w['source_name']}] {w['wisdom']}{used}")
        lines.append("")

    WISDOM_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    WISDOM_MD_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("[wisdom] WISDOM.md regenerated with %d entries", len(all_w))


def get_wisdom_md() -> str:
    if WISDOM_MD_PATH.exists():
        return WISDOM_MD_PATH.read_text(encoding="utf-8")
    return ""
