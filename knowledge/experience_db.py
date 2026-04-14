"""经验数据库 — JSON 文件存储 + 相关度检索

每条经验包含：股票信息、催化类型、形态标签、预测、实际结果、教训。
检索时按同股票、同行业、催化/形态重叠度评分，并应用时间衰减，返回 Top-K。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, date
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge" / "experience_db.json"


# ── I/O ──────────────────────────────────────────────────────────────

def load_db(db_path: Path | None = None) -> list[dict]:
    """Load experience entries from JSON file."""
    path = db_path or _DEFAULT_PATH
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else []
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as exc:
        logger.warning("[experience_db] JSON parse error: %r", exc)
        return []


def _save_db(entries: list[dict], path: Path) -> None:
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


# ── ID generation ────────────────────────────────────────────────────

def _generate_id(entries: list[dict], today: str | None = None) -> str:
    """Generate auto-incrementing ID like EXP-20260410-001."""
    if today is None:
        today = date.today().strftime("%Y%m%d")
    prefix = f"EXP-{today}-"
    existing = [e["id"] for e in entries if isinstance(e.get("id"), str) and e["id"].startswith(prefix)]
    if existing:
        max_seq = max(int(eid.split("-")[-1]) for eid in existing)
    else:
        max_seq = 0
    return f"{prefix}{max_seq + 1:03d}"


# ── Add ──────────────────────────────────────────────────────────────

def add_experience(exp: dict, db_path: Path | None = None) -> str:
    """Add an experience entry. Auto-generates ID like EXP-20260410-001. Returns ID."""
    path = db_path or _DEFAULT_PATH
    entries = load_db(path)
    exp = dict(exp)  # don't mutate caller's dict
    if not exp.get("id"):
        exp["id"] = _generate_id(entries)
    entries.append(exp)
    _save_db(entries, path)
    return exp["id"]


# ── Scoring helpers ──────────────────────────────────────────────────

def _time_decay(entry_date_str: str) -> float:
    """30d→1.0, 30-90d→0.7, 90d+→0.5"""
    try:
        entry_date = datetime.strptime(entry_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 0.5
    days = (date.today() - entry_date).days
    if days <= 30:
        return 1.0
    elif days <= 90:
        return 0.7
    else:
        return 0.5


def _score_entry(
    entry: dict,
    ts_code: str,
    current_industry: str,
    current_catalysts: list[str],
    current_patterns: list[str],
) -> float:
    score = 0.0

    # Same stock: +10
    entry_code = entry.get("stock_code", "")
    if entry_code and entry_code == ts_code:
        score += 10

    # Same industry: +5
    entry_industry = entry.get("industry", "")
    if entry_industry and current_industry and entry_industry == current_industry:
        score += 5

    # Catalyst overlap: +3 per match
    entry_catalysts = set(entry.get("catalyst_type") or [])
    for cat in current_catalysts:
        if cat in entry_catalysts:
            score += 3

    # Pattern overlap: +3 per match
    entry_patterns = set(entry.get("pattern_tags") or [])
    for pat in current_patterns:
        if pat in entry_patterns:
            score += 3

    # Time decay
    decay = _time_decay(entry.get("date", ""))
    return score * decay


# ── Retrieve ─────────────────────────────────────────────────────────

def retrieve_lessons(
    ts_code: str,
    stock_name: str,
    current_industry: str = "",
    current_catalysts: list[str] | None = None,
    current_patterns: list[str] | None = None,
    top_k: int = 5,
    db_path: Path | None = None,
) -> str:
    """Retrieve relevant lessons formatted as 【历史镜鉴】text.

    Returns formatted text or empty string if no matches.
    """
    catalysts = current_catalysts or []
    patterns = current_patterns or []

    entries = load_db(db_path)
    if not entries:
        return ""

    scored = []
    for entry in entries:
        s = _score_entry(entry, ts_code, current_industry, catalysts, patterns)
        if s > 0:
            scored.append((s, entry))

    if not scored:
        return ""

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    lines = [f"【历史镜鉴】（共{len(top)}条相关经验）", ""]
    for _, entry in top:
        entry_code = entry.get("stock_code", "")
        same_stock = entry_code and entry_code == ts_code

        icon = "⚠️" if same_stock else "📌"
        label = "本股历史" if same_stock else "参考案例"

        entry_date = entry.get("date", "未知日期")
        name = entry.get("stock_name", entry_code or "未知")
        pred = entry.get("prediction") or {}
        actual = entry.get("actual") or {}
        score_val = pred.get("score", "?")
        ret20 = actual.get("return_20d")
        ret20_str = f"{ret20:+.1f}%" if ret20 is not None else "N/A"
        lesson = entry.get("lesson", "")

        lines.append(f"{icon} {label}：{entry_date} {name}，评分{score_val}，T+20收益{ret20_str}")
        if lesson:
            lines.append(f"   教训：{lesson}")

    return "\n".join(lines)
