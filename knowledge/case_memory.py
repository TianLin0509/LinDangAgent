"""案例记忆 — 基于 SQLite 的经验案例存储与两阶段检索

每条案例包含：情境快照、AI 评分、实际结果、反思教训、板块标签。
检索时先用标签 + 环境粗筛，再用评分距离精排，返回最相关的 Top-K 案例。
"""

import json
import logging
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path

from knowledge.kb_config import DIRECTION_CN, OUTCOME_CN, SECTOR_KEYWORDS

logger = logging.getLogger(__name__)


def extract_sector_tags(text: str, stock_code: str = "") -> list[str]:
    """从文本+行业信息中提取匹配的板块/概念标签。

    先从 text（股票名称+摘要）做关键词匹配，
    再通过 stock_code 查行业名补充匹配。
    """
    if not text and not stock_code:
        return []

    combined_text = text or ""

    # 通过 stock_code 补充行业信息
    if stock_code:
        try:
            from data.tushare_client import load_stock_list
            sl, _ = load_stock_list()
            if sl is not None and not sl.empty:
                m = sl[sl["ts_code"] == stock_code]
                if not m.empty:
                    industry = str(m.iloc[0].get("industry", ""))
                    if industry:
                        combined_text += " " + industry
        except Exception as exc:
            logger.debug("[case_memory] industry fetch failed: %r", exc)

    return [kw for kw in SECTOR_KEYWORDS if kw in combined_text]


# ── 案例卡片 ─────────────────────────────────────────────────────

@dataclass
class CaseCard:
    case_id: str
    report_date: str
    stock_code: str
    stock_name: str
    source: str = "report"

    # 情境
    regime: str = "shock"
    regime_label: str = "震荡市"
    sector_tags: list[str] = field(default_factory=list)

    # AI 判断
    score_fundamental: float = 5.0
    score_expectation: float = 5.0
    score_capital: float = 5.0
    score_technical: float = 5.0
    score_weighted: float = 5.0
    direction: str = "neutral"
    reasoning_summary: str = ""

    # 实际结果
    return_5d: float = 0.0
    return_10d: float = 0.0
    return_20d: float = 0.0
    hit_10d: bool | None = None
    outcome_type: str = "draw"  # win / loss / draw

    # 反思
    lesson: str = ""
    lesson_generated_at: str | None = None

    # 检索辅助
    situation_summary: str = ""
    created_at: str = ""

    @property
    def direction_cn(self) -> str:
        return DIRECTION_CN.get(self.direction, "中性")

    @property
    def scores(self) -> dict:
        return {
            "基本面": self.score_fundamental,
            "预期差": self.score_expectation,
            "资金面": self.score_capital,
            "技术面": self.score_technical,
            "综合加权": self.score_weighted,
        }


def classify_outcome(direction: str, return_10d: float) -> str:
    """根据方向和 10 日收益判定 win/loss/draw。"""
    if direction == "neutral":
        return "draw"
    threshold = 2.0
    if direction == "bullish":
        if return_10d > threshold:
            return "win"
        elif return_10d < -threshold:
            return "loss"
    elif direction == "bearish":
        if return_10d < -threshold:
            return "win"
        elif return_10d > threshold:
            return "loss"
    return "draw"


def build_situation_summary(case: CaseCard) -> str:
    """用模板生成情境摘要（零 AI 成本）。"""
    tags_str = "/".join(case.sector_tags[:3]) if case.sector_tags else "未知板块"
    outcome_word = OUTCOME_CN.get(case.outcome_type, "")
    return (
        f"{case.stock_name} {tags_str} {case.regime_label} "
        f"综合{case.score_weighted}分{case.direction_cn} "
        f"10日{case.return_10d:+.1f}%{outcome_word}"
    )


# ── 数据库连接（通过 kb_db 统一管理）────────────────────────────


# ── 存储 ─────────────────────────────────────────────────────────

def store_case(case: CaseCard) -> None:
    """存储一个案例卡片（含标签）。已存在则更新 lesson。"""
    from knowledge.kb_validators import validate_case_id, validate_stock_code
    from knowledge.kb_db import get_manager
    if not validate_case_id(case.case_id):
        logger.warning("[case_memory] store_case rejected: empty case_id")
        return
    if case.stock_code and not validate_stock_code(case.stock_code):
        logger.warning("[case_memory] store_case: invalid stock_code %s, proceeding anyway", case.stock_code)
    if not case.created_at:
        case.created_at = datetime.now().isoformat(timespec="seconds")
    if not case.situation_summary:
        case.situation_summary = build_situation_summary(case)

    mgr = get_manager()
    with mgr.write("case_memory") as conn:
        conn.execute("""
            INSERT OR REPLACE INTO cases (
                case_id, report_date, stock_code, stock_name, source,
                regime, regime_label,
                score_fundamental, score_expectation, score_capital, score_technical, score_weighted,
                direction, reasoning_summary,
                return_5d, return_10d, return_20d, hit_10d, outcome_type,
                lesson, lesson_generated_at, situation_summary, embedding, created_at
            ) VALUES (?,?,?,?,?, ?,?, ?,?,?,?,?, ?,?, ?,?,?,?,?, ?,?,?,?,?)
        """, (
            case.case_id, case.report_date, case.stock_code, case.stock_name, case.source,
            case.regime, case.regime_label,
            case.score_fundamental, case.score_expectation, case.score_capital,
            case.score_technical, case.score_weighted,
            case.direction, case.reasoning_summary,
            case.return_5d, case.return_10d, case.return_20d,
            1 if case.hit_10d else (0 if case.hit_10d is False else None),
            case.outcome_type,
            case.lesson, case.lesson_generated_at, case.situation_summary,
            None,  # embedding 暂不使用
            case.created_at,
        ))

        # 重建标签
        conn.execute("DELETE FROM case_tags WHERE case_id = ?", (case.case_id,))
        tags = []
        tags.append((case.case_id, "regime", case.regime))
        tags.append((case.case_id, "outcome", case.outcome_type))
        tags.append((case.case_id, "direction", case.direction))
        for sector in case.sector_tags:
            tags.append((case.case_id, "sector", sector))
        conn.executemany(
            "INSERT INTO case_tags (case_id, tag_type, tag_value) VALUES (?,?,?)",
            tags,
        )

    logger.info(
        "[case_memory] stored case %s: %s %s %s lesson=%s",
        case.case_id[:12], case.stock_name, case.outcome_type,
        case.regime_label, "yes" if case.lesson else "no",
    )


def case_exists(case_id: str) -> bool:
    """检查案例是否已存在。"""
    from knowledge.kb_db import get_manager
    mgr = get_manager()
    with mgr.read("case_memory") as conn:
        row = conn.execute("SELECT 1 FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    return row is not None


def get_case_count() -> int:
    from knowledge.kb_db import get_manager
    mgr = get_manager()
    with mgr.read("case_memory") as conn:
        row = conn.execute("SELECT COUNT(*) FROM cases").fetchone()
    return row[0] if row else 0


def get_sector_summary(sector_tag: str, days: int = 90) -> dict | None:
    """聚合某板块/题材的历史表现，返回胜率+常见失误。

    需至少3条案例才返回结果，避免小样本误导。
    """
    from collections import Counter
    from datetime import timedelta
    from knowledge.kb_db import get_manager

    mgr = get_manager()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # 查询该板块所有有 lesson 的案例
    with mgr.read("case_memory") as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT c.outcome_type, c.return_10d, c.lesson, c.direction
            FROM cases c
            JOIN case_tags ct ON c.case_id = ct.case_id
            WHERE ct.tag_type = 'sector' AND ct.tag_value = ?
              AND c.report_date >= ? AND c.lesson IS NOT NULL AND c.lesson != ''
        """, (sector_tag, cutoff)).fetchall()
        conn.row_factory = None

    if len(rows) < 3:
        return None

    total = len(rows)
    directional = [r for r in rows if r["direction"] != "neutral"]
    wins = sum(1 for r in rows if r["outcome_type"] == "win")
    losses = [r for r in rows if r["outcome_type"] == "loss"]

    hit_rate = round(wins / len(directional) * 100, 0) if directional else 0
    avg_return = round(sum(r["return_10d"] or 0 for r in rows) / total, 1)

    # 从 loss 案例的 lesson 中提取高频失误关键词
    common_mistakes = []
    if losses:
        dimension_keywords = [
            "高估", "低估", "忽视", "过度", "偏乐观", "偏悲观",
            "基本面", "预期差", "资金面", "技术面", "催化", "题材",
            "估值", "解禁", "减持", "质押", "周期", "竞争",
        ]
        word_counter = Counter()
        for r in losses:
            lesson = r["lesson"] or ""
            for kw in dimension_keywords:
                if kw in lesson:
                    word_counter[kw] += 1
        # 取出现>=2次的关键词组合成失误描述
        common_mistakes = [kw for kw, cnt in word_counter.most_common(5) if cnt >= 2]

    return {
        "sector": sector_tag,
        "total_cases": total,
        "win_rate_10d": hit_rate,
        "avg_return_10d": avg_return,
        "loss_count": len(losses),
        "common_mistakes": common_mistakes,
    }


# ── 两阶段检索 ───────────────────────────────────────────────────

def retrieve_similar_cases(
    regime: str = "",
    sector_tags: list[str] | None = None,
    current_scores: dict | None = None,
    stock_code: str = "",
    top_k: int = 3,
    max_days: int = 180,
) -> list[CaseCard]:
    """两阶段检索：标签粗筛 + 评分距离精排。"""
    from knowledge.kb_db import get_manager
    mgr = get_manager()
    cutoff = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d")

    # Stage 1: 标签粗筛
    with mgr.read("case_memory") as conn:
        candidates = _tag_filter(conn, regime, sector_tags or [], cutoff, stock_code, limit=30)
    if not candidates:
        return []

    # Stage 2: 评分距离排序
    scored = []
    for case in candidates:
        score = _rank_score(case, regime, sector_tags or [], current_scores or {})
        scored.append((score, case))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [case for _, case in scored[:top_k]]


def _tag_filter(
    conn: sqlite3.Connection,
    regime: str,
    sector_tags: list[str],
    cutoff: str,
    stock_code: str,
    limit: int = 30,
) -> list[CaseCard]:
    """Stage 1: SQL 标签粗筛。"""
    # 构建动态 SQL
    conditions = ["c.report_date >= ?", "c.lesson IS NOT NULL", "c.lesson != ''"]
    params: list = [cutoff]

    # 排除同一只股票（避免循环引用自己的案例）
    if stock_code:
        conditions.append("c.stock_code != ?")
        params.append(stock_code)

    # regime 或 sector 匹配
    tag_conditions = []
    if regime:
        tag_conditions.append("(ct.tag_type = 'regime' AND ct.tag_value = ?)")
        params.append(regime)
    for tag in sector_tags[:5]:  # 最多 5 个 sector tag
        tag_conditions.append("(ct.tag_type = 'sector' AND ct.tag_value = ?)")
        params.append(tag)

    if tag_conditions:
        tag_filter = " OR ".join(tag_conditions)
        sql = f"""
            SELECT DISTINCT c.* FROM cases c
            LEFT JOIN case_tags ct ON c.case_id = ct.case_id
            WHERE {' AND '.join(conditions)}
              AND ({tag_filter})
            ORDER BY c.report_date DESC
            LIMIT ?
        """
    else:
        sql = f"""
            SELECT c.* FROM cases c
            WHERE {' AND '.join(conditions)}
            ORDER BY c.report_date DESC
            LIMIT ?
        """
    params.append(limit)

    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.row_factory = None

    if not rows:
        return []

    # 批量获取所有 case_id 的 sector tags（避免 _row_to_case 逐条查询嵌套连接）
    case_ids = [row["case_id"] for row in rows]
    placeholders = ",".join("?" * len(case_ids))
    tag_rows = conn.execute(
        f"SELECT case_id, tag_value FROM case_tags WHERE case_id IN ({placeholders}) AND tag_type = 'sector'",
        case_ids,
    ).fetchall()
    tags_map: dict[str, list[str]] = {}
    for cid, val in tag_rows:
        tags_map.setdefault(cid, []).append(val)

    return [_row_to_case(row, sector_tags=tags_map.get(row["case_id"], [])) for row in rows]


def _rank_score(
    case: CaseCard,
    current_regime: str,
    current_sector_tags: list[str],
    current_scores: dict,
) -> float:
    """Stage 2: 计算案例与当前情境的匹配分数。"""
    score = 0.0

    # 1. 环境匹配 (0.2 权重)
    if case.regime == current_regime:
        score += 0.2

    # 2. 板块重合 (0.5 权重)
    if current_sector_tags and case.sector_tags:
        overlap = len(set(current_sector_tags) & set(case.sector_tags))
        total = len(set(current_sector_tags) | set(case.sector_tags))
        if total > 0:
            score += 0.5 * (overlap / total)

    # 3. 评分距离 (0.3 权重)
    if current_scores:
        dims = ["基本面", "预期差", "资金面", "技术面"]
        case_scores = case.scores
        l1 = sum(abs(case_scores.get(d, 5) - current_scores.get(d, 5)) for d in dims)
        # L1 范围 0-40，归一化后取反
        score += 0.3 * max(0, 1 - l1 / 20)

    # 4. 失败案例加权——反面教材比成功经验更值钱
    if case.outcome_type == "loss":
        score += 0.15

    return score


def _row_to_case(row: sqlite3.Row, sector_tags: list[str] | None = None) -> CaseCard:
    """将 SQLite Row 转为 CaseCard。

    Args:
        row: SQLite Row 对象
        sector_tags: 预加载的板块标签（如果为 None，从数据库查询）
    """
    if sector_tags is None:
        from knowledge.kb_db import get_manager
        mgr = get_manager()
        with mgr.read("case_memory") as conn:
            tag_rows = conn.execute(
                "SELECT tag_value FROM case_tags WHERE case_id = ? AND tag_type = 'sector'",
                (row["case_id"],),
            ).fetchall()
        sector_tags = [r[0] for r in tag_rows]

    return CaseCard(
        case_id=row["case_id"],
        report_date=row["report_date"],
        stock_code=row["stock_code"],
        stock_name=row["stock_name"],
        source=row["source"] or "report",
        regime=row["regime"] or "shock",
        regime_label=row["regime_label"] or "震荡市",
        sector_tags=sector_tags,
        score_fundamental=row["score_fundamental"] or 5,
        score_expectation=row["score_expectation"] or 5,
        score_capital=row["score_capital"] or 5,
        score_technical=row["score_technical"] or 5,
        score_weighted=row["score_weighted"] or 5,
        direction=row["direction"] or "neutral",
        reasoning_summary=row["reasoning_summary"] or "",
        return_5d=row["return_5d"] or 0,
        return_10d=row["return_10d"] or 0,
        return_20d=row["return_20d"] or 0,
        hit_10d=bool(row["hit_10d"]) if row["hit_10d"] is not None else None,
        outcome_type=row["outcome_type"] or "draw",
        lesson=row["lesson"] or "",
        lesson_generated_at=row["lesson_generated_at"],
        situation_summary=row["situation_summary"] or "",
        created_at=row["created_at"] or "",
    )
