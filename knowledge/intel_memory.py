# -*- coding: utf-8 -*-
"""情报知识库 — 持久化存储情报分析结果，支持按板块/主题检索

将 intel-analyze 的一次性分析结果持久化为可检索的知识库，
追踪主题趋势，为后续分析注入历史情报上下文。

数据存储：SQLite data/knowledge/intel_memory.db
"""

import json
import logging
import uuid
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _run_migrations() -> None:
    """列迁移：确保旧数据库包含新增列。"""
    from knowledge.kb_db import get_manager
    get_manager().run_migration("intel_memory", [
        ("SELECT source_type FROM intel_entries LIMIT 1",
         "ALTER TABLE intel_entries ADD COLUMN source_type TEXT NOT NULL DEFAULT 'article'"),
        ("SELECT raw_text FROM intel_entries LIMIT 1",
         "ALTER TABLE intel_entries ADD COLUMN raw_text TEXT NOT NULL DEFAULT ''"),
        ("SELECT publish_time FROM intel_entries LIMIT 1",
         "ALTER TABLE intel_entries ADD COLUMN publish_time TEXT NOT NULL DEFAULT ''"),
    ])


# 延迟执行迁移（首次导入时不触发，首次使用数据库时由 kb_db 保证 schema）
_migrations_done = False


def _ensure_migrations() -> None:
    global _migrations_done
    if not _migrations_done:
        _run_migrations()
        _migrations_done = True


# ── 结构化提取 ───────────────────────────────────────────────────

EXTRACT_SYSTEM = (
    "你是一个结构化信息提取器。从给定的情报分析文本中提取结构化字段。"
    "只输出 JSON，不要输出其他任何内容。"
)

EXTRACT_PROMPT = """从以下情报分析文本中提取结构化信息，输出严格 JSON 格式：

```
{analysis_text}
```

输出格式（严格 JSON，不要 markdown 包裹）：
{{
  "themes": ["主题1", "主题2"],
  "affected_sectors": ["板块1", "板块2"],
  "sentiment": "bullish 或 bearish 或 neutral",
  "key_facts": ["事实1", "事实2", "事实3"],
  "implications": "对A股市场的核心影响（100字内）",
  "source_credibility": "high 或 medium 或 low"
}}

要求：
- themes: 提取2-5个核心主题/关键词
- affected_sectors: 涉及的A股板块（如：AI算力、半导体、新能源车、白酒等）
- sentiment: 对市场整体的情绪倾向
- key_facts: 最重要的3-5个事实点
- implications: 简洁概括对市场的影响
"""


def _extract_structured_fields(analysis_text: str) -> dict:
    """用 Claude Sonnet 从分析文本中提取结构化字段。"""
    from ai.client import call_ai, get_ai_client

    model_name = "⚡ Claude Sonnet（MAX）"
    client, cfg, err = get_ai_client(model_name)
    if err or cfg is None:
        logger.warning("[intel_memory] Claude Sonnet not available: %s, trying fallback", err)
        # 回退到豆包Mini
        model_name = "🟤 豆包 · Seed 2.0 Mini"
        client, cfg, err = get_ai_client(model_name)
        if err or cfg is None:
            logger.warning("[intel_memory] fallback model also unavailable: %s", err)
            return {}

    # 截断过长的分析文本
    truncated = analysis_text[:5000] if len(analysis_text) > 5000 else analysis_text
    prompt = EXTRACT_PROMPT.format(analysis_text=truncated)

    cfg_no_search = {**cfg, "supports_search": False}
    text, call_err = call_ai(client, cfg_no_search, prompt, system=EXTRACT_SYSTEM, max_tokens=800)
    if call_err:
        logger.warning("[intel_memory] extraction call failed: %s", call_err)
        return {}

    # 解析 JSON
    from knowledge.kb_utils import parse_ai_json
    parsed = parse_ai_json(text)
    if not isinstance(parsed, dict):
        logger.warning("[intel_memory] structured fields not a dict or parse failed")
        return {"_parse_error": True}
    return parsed


# ── 存储 ─────────────────────────────────────────────────────────

def store_intel(url: str, title: str, model: str, analysis: str,
                structured: dict | None = None,
                source_type: str = "article",
                raw_text: str = "",
                publish_time: str = "") -> str:
    """存储一条情报分析结果。返回 entry_id。

    如果 structured 为 None，会自动调用 AI 提取结构化字段。
    source_type: 'article'（文章）或 'douyin_video'（抖音视频）。
    raw_text: 原始文本（如视频字幕原文），备查用，不参与检索注入。
    publish_time: 内容发布时间（如 '2025-12-23 22:16'），区别于 analyzed_at。
    """
    from knowledge.kb_db import get_manager
    _ensure_migrations()

    entry_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat(timespec="seconds")

    if structured is None:
        structured = _extract_structured_fields(analysis)

    themes = structured.get("themes", [])
    sectors = structured.get("affected_sectors", [])
    sentiment = structured.get("sentiment", "neutral")
    key_facts = structured.get("key_facts", [])
    implications = structured.get("implications", "")
    credibility = structured.get("source_credibility", "medium")

    mgr = get_manager()
    with mgr.write("intel_memory") as conn:
        conn.execute(
            "INSERT INTO intel_entries "
            "(entry_id, url, title, analyzed_at, model, themes, affected_sectors, "
            "sentiment, key_facts, implications, source_credibility, full_analysis, "
            "source_type, raw_text, publish_time) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (entry_id, url, title, now, model,
             json.dumps(themes, ensure_ascii=False),
             json.dumps(sectors, ensure_ascii=False),
             sentiment,
             json.dumps(key_facts, ensure_ascii=False),
             implications, credibility, analysis, source_type,
             raw_text, publish_time),
        )

        # 更新主题追踪
        for theme in themes:
            existing = conn.execute(
                "SELECT article_count, related_sectors FROM intel_themes WHERE theme=?",
                (theme,),
            ).fetchone()
            if existing:
                old_count = existing[0]
                old_sectors = json.loads(existing[1]) if existing[1] else []
                merged_sectors = list(set(old_sectors + sectors))
                conn.execute(
                    "UPDATE intel_themes SET last_seen=?, article_count=?, "
                    "related_sectors=? WHERE theme=?",
                    (now, old_count + 1,
                     json.dumps(merged_sectors, ensure_ascii=False), theme),
                )
            else:
                conn.execute(
                    "INSERT INTO intel_themes VALUES (?,?,?,?,?,?)",
                    (theme, now, now, 1, "emerging",
                     json.dumps(sectors, ensure_ascii=False)),
                )

    logger.info("[intel_memory] stored intel %s: %s (%d themes)", entry_id, title[:30], len(themes))
    return entry_id


# ── 检索 ─────────────────────────────────────────────────────────

def query_recent_intel(days: int = 30, sector_filter: str = "") -> list[dict]:
    """查询最近N天的情报条目。可选按板块过滤。"""
    from knowledge.kb_db import get_manager
    _ensure_migrations()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    mgr = get_manager()
    with mgr.read("intel_memory") as conn:
        rows = conn.execute(
            "SELECT entry_id, url, title, analyzed_at, model, themes, "
            "affected_sectors, sentiment, key_facts, implications, source_credibility "
            "FROM intel_entries WHERE analyzed_at >= ? ORDER BY analyzed_at DESC",
            (cutoff,),
        ).fetchall()

    results = []
    for r in rows:
        entry = {
            "entry_id": r[0], "url": r[1], "title": r[2],
            "analyzed_at": r[3], "model": r[4],
            "themes": json.loads(r[5]), "affected_sectors": json.loads(r[6]),
            "sentiment": r[7], "key_facts": json.loads(r[8]),
            "implications": r[9], "source_credibility": r[10],
        }
        if sector_filter:
            if sector_filter not in str(entry["affected_sectors"]):
                continue
        results.append(entry)

    return results


def query_by_sectors(sectors: list[str], days: int = 30) -> list[dict]:
    """按板块标签查询相关情报（用于 injector 注入）。"""
    if not sectors:
        return []
    all_intel = query_recent_intel(days=days)
    matched = []
    for entry in all_intel:
        entry_sectors = entry.get("affected_sectors", [])
        if any(s in entry_sectors or s in str(entry.get("themes", [])) for s in sectors):
            matched.append(entry)
    return matched[:5]  # 最多返回5条


def get_active_themes(days: int = 14) -> list[dict]:
    """获取近期活跃主题（按文章数排序）。"""
    from knowledge.kb_db import get_manager
    _ensure_migrations()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    mgr = get_manager()
    with mgr.read("intel_memory") as conn:
        rows = conn.execute(
            "SELECT theme, first_seen, last_seen, article_count, "
            "sentiment_trend, related_sectors "
            "FROM intel_themes WHERE last_seen >= ? "
            "ORDER BY article_count DESC LIMIT 20",
            (cutoff,),
        ).fetchall()

    return [
        {
            "theme": r[0], "first_seen": r[1], "last_seen": r[2],
            "article_count": r[3], "sentiment_trend": r[4],
            "related_sectors": json.loads(r[5]),
        }
        for r in rows
    ]


def update_theme_stats():
    """更新主题趋势标签（在 scheduler 中调用）。"""
    from knowledge.kb_db import get_manager
    _ensure_migrations()
    cutoff_recent = (datetime.now() - timedelta(days=7)).isoformat()
    cutoff_old = (datetime.now() - timedelta(days=30)).isoformat()

    mgr = get_manager()
    with mgr.write("intel_memory") as conn:
        themes = conn.execute("SELECT theme FROM intel_themes").fetchall()
        for (theme,) in themes:
            # 最近7天的文章数
            recent = conn.execute(
                "SELECT COUNT(*) FROM intel_entries "
                "WHERE themes LIKE ? AND analyzed_at >= ?",
                (f'%"{theme}"%', cutoff_recent),
            ).fetchone()[0]
            # 7-30天前的文章数
            older = conn.execute(
                "SELECT COUNT(*) FROM intel_entries "
                "WHERE themes LIKE ? AND analyzed_at >= ? AND analyzed_at < ?",
                (f'%"{theme}"%', cutoff_old, cutoff_recent),
            ).fetchone()[0]

            if recent > older:
                trend = "strengthening"
            elif recent < older:
                trend = "weakening"
            else:
                trend = "stable"

            conn.execute(
                "UPDATE intel_themes SET sentiment_trend=? WHERE theme=?",
                (trend, theme),
            )

    logger.info("[intel_memory] theme stats updated for %d themes", len(themes))


def get_intel_count() -> int:
    """获取情报总数。"""
    from knowledge.kb_db import get_manager
    _ensure_migrations()
    mgr = get_manager()
    with mgr.read("intel_memory") as conn:
        return conn.execute("SELECT COUNT(*) FROM intel_entries").fetchone()[0]
