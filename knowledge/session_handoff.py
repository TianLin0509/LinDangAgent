# -*- coding: utf-8 -*-
"""会话交接系统 — 生成 STATE.md 工作记忆快照

STATE.md 是林铛跨会话的"工作记忆"——每次新对话开始时读取，
快速恢复"我是谁、市场怎样、最近做了什么、关注什么"。

两个入口：
  1. generate_state_md()  — 从数据库自动提炼（scheduler 每日调用）
  2. save_session_summary() — 保存会话摘要（用户/Claude 手动调用）

Token 预算：STATE.md 严格控制在 ~500 tokens（约 1500 中文字符）。
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from knowledge.kb_config import KNOWLEDGE_DIR

logger = logging.getLogger(__name__)
STATE_MD_PATH = KNOWLEDGE_DIR / "STATE.md"
SESSION_LOG_PATH = KNOWLEDGE_DIR / "session_log.jsonl"


def generate_state_md():
    """从各数据库提炼关键信息，生成 STATE.md 工作记忆快照。"""
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    sections = []

    now = datetime.now()
    sections.append(f"# 林铛工作状态\n*更新: {now.strftime('%Y-%m-%d %H:%M')}*")

    # 1. 市场环境
    regime_text = _get_regime_section()
    if regime_text:
        sections.append(f"## 市场环境\n{regime_text}")

    # 2. 最近3天动态
    recent_text = _get_recent_activity(days=3)
    if recent_text:
        sections.append(f"## 最近动态\n{recent_text}")

    # 3. 核心信念 Top 3
    beliefs_text = _get_top_beliefs(top_k=3)
    if beliefs_text:
        sections.append(f"## 核心信念\n{beliefs_text}")

    # 4. 关注清单
    watchlist_text = _get_watchlist()
    if watchlist_text:
        sections.append(f"## 关注清单\n{watchlist_text}")

    # 5. 系统绩效速览
    perf_text = _get_performance_brief()
    if perf_text:
        sections.append(f"## 系统绩效\n{perf_text}")

    # 6. 上次会话摘要
    session_text = _get_last_session()
    if session_text:
        sections.append(f"## 上次会话\n{session_text}")

    # 写入 STATE.md
    content = "\n\n".join(sections)

    # Token 预算保护：截断到 ~1500 字符
    if len(content) > 1500:
        content = content[:1500] + "\n\n*（已截断，详细信息请用 CLI 查询）*"

    STATE_MD_PATH.write_text(content, encoding="utf-8")
    logger.info("[session_handoff] STATE.md generated (%d chars)", len(content))
    return content


def save_session_summary(summary: str, pending: str = ""):
    """保存会话摘要到 session_log.jsonl，并重新生成 STATE.md。"""
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)

    entry = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "pending": pending,
    }

    with open(SESSION_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("[session_handoff] session summary saved: %s", summary[:60])

    # 重新生成 STATE.md（包含最新会话摘要）
    generate_state_md()


# ── 各段落生成 ───────────────────────────────────────────────────

def _get_regime_section() -> str:
    try:
        from knowledge.regime_detector import get_current_regime
        regime = get_current_regime()
        if regime and regime.get("regime"):
            label = regime.get("regime_label", regime["regime"])
            # 加准确率
            from knowledge.outcome_tracker import get_accuracy_summary
            acc = get_accuracy_summary(days=90)
            if acc.get("directional_count", 0) >= 5:
                return (
                    f"{label} | 90天10日胜率{acc['hit_rate_10d']:.0f}%"
                    f"（{acc['directional_count']}样本）"
                )
            return label
    except Exception as exc:
        logger.debug("[state] regime error: %r", exc)
    return ""


def _get_recent_activity(days: int = 3) -> str:
    """从 reports.db 和 session_log 获取近期活动。"""
    lines = []

    # 从 reports.db 获取最近分析
    try:
        import sqlite3
        db_path = Path(__file__).resolve().parent.parent / "storage" / "reports.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = conn.execute(
                "SELECT created_at, stock_name, summary FROM reports "
                "WHERE created_at >= ? ORDER BY created_at DESC LIMIT 10",
                (cutoff,),
            ).fetchall()
            conn.close()

            # 按日期分组
            by_date = {}
            for row in rows:
                date_str = row[0][:10]
                stock = row[1] or "未知"
                by_date.setdefault(date_str, []).append(stock)

            for date_str in sorted(by_date.keys(), reverse=True)[:3]:
                stocks = "、".join(by_date[date_str][:5])
                lines.append(f"- {date_str[5:]}: 分析了{stocks}")
    except Exception as exc:
        logger.debug("[state] reports error: %r", exc)

    # 从 session_log 补充
    try:
        if SESSION_LOG_PATH.exists():
            entries = []
            with open(SESSION_LOG_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            recent = [e for e in entries if e.get("date", "") >= cutoff]
            for e in recent[-3:]:
                date_short = e["date"][5:10]
                summary = e.get("summary", "")[:60]
                if summary and not any(date_short in l for l in lines):
                    lines.append(f"- {date_short}: {summary}")
    except Exception as exc:
        logger.debug("[state] session_log error: %r", exc)

    return "\n".join(lines[:6]) if lines else ""


def _get_top_beliefs(top_k: int = 3) -> str:
    try:
        from knowledge.thesis_journal import get_active_beliefs
        beliefs = get_active_beliefs()
        if not beliefs:
            return ""

        lines = []
        for b in beliefs[:top_k]:
            filled = int(b["confidence"] * 5)
            bar = "●" * filled + "○" * (5 - filled)
            lines.append(f"- [{bar}] {b['belief']}")
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("[state] beliefs error: %r", exc)
    return ""


def _get_watchlist() -> str:
    watchlist_path = KNOWLEDGE_DIR / "watchlist.json"
    if not watchlist_path.exists():
        return ""

    try:
        data = json.loads(watchlist_path.read_text(encoding="utf-8"))
        watches = data.get("watches", [])
        if not watches:
            return ""

        now = datetime.now().strftime("%Y-%m-%d")
        lines = []
        for w in watches:
            expiry = w.get("expiry", "")
            if expiry and expiry < now:
                continue  # 过期的不显示
            lines.append(
                f"- {w['stock_name']} {w.get('score', '?')}分"
                f" | {w.get('trigger', '观察中')}"
                f"（{w.get('added_date', '?')}起）"
            )
        return "\n".join(lines[:5])
    except Exception as exc:
        logger.debug("[session_handoff] belief summary failed: %r", exc)
        return ""


def _get_performance_brief() -> str:
    try:
        from knowledge.outcome_tracker import get_accuracy_summary
        acc = get_accuracy_summary(days=90)
        if acc.get("directional_count", 0) < 5:
            return ""

        parts = [f"90天: {acc['directional_count']}样本"]
        parts.append(f"10日胜率{acc['hit_rate_10d']:.0f}%")
        if acc.get("avg_return_10d") is not None:
            parts.append(f"平均收益{acc['avg_return_10d']:+.1f}%")
        return " | ".join(parts)
    except Exception as exc:
        logger.debug("[session_handoff] accuracy section failed: %r", exc)
        return ""


def _get_last_session() -> str:
    if not SESSION_LOG_PATH.exists():
        return ""

    try:
        last = None
        with open(SESSION_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line)
                    except json.JSONDecodeError:
                        continue

        if last:
            parts = [last.get("summary", "")]
            pending = last.get("pending", "")
            if pending:
                parts.append(f"待办: {pending}")
            return " ".join(p for p in parts if p)[:200]
    except Exception as exc:
        logger.debug("[session_handoff] macro snapshot failed: %r", exc)
    return ""


# ── 关注清单管理 ─────────────────────────────────────────────────

def add_to_watchlist(stock_code: str, stock_name: str, score: float = 0,
                     trigger: str = "观察中", days: int = 5):
    """添加到关注清单。"""
    watchlist_path = KNOWLEDGE_DIR / "watchlist.json"
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)

    data = {"watches": []}
    if watchlist_path.exists():
        try:
            data = json.loads(watchlist_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    # 去重
    data["watches"] = [w for w in data.get("watches", [])
                       if w.get("stock_code") != stock_code]

    now = datetime.now()
    data["watches"].append({
        "stock_code": stock_code,
        "stock_name": stock_name,
        "added_date": now.strftime("%Y-%m-%d"),
        "score": score,
        "trigger": trigger,
        "expiry": (now + timedelta(days=days)).strftime("%Y-%m-%d"),
    })

    watchlist_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    logger.info("[watchlist] added %s (%s)", stock_name, stock_code)


def get_watchlist() -> list[dict]:
    """获取当前关注清单。"""
    watchlist_path = KNOWLEDGE_DIR / "watchlist.json"
    if not watchlist_path.exists():
        return []

    try:
        data = json.loads(watchlist_path.read_text(encoding="utf-8"))
        now = datetime.now().strftime("%Y-%m-%d")
        return [w for w in data.get("watches", [])
                if not w.get("expiry") or w["expiry"] >= now]
    except Exception as exc:
        logger.debug("[session_handoff] watchlist load failed: %r", exc)
        return []
