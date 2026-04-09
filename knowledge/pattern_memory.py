"""规律识别 — 从 outcome 数据中提炼可复用的评分模式及其历史胜率

预定义若干模式模板，从所有历史 outcome 中统计各模式的胜率和平均收益率。
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from knowledge.kb_config import KNOWLEDGE_DIR, PATTERN_TEMPLATES

logger = logging.getLogger(__name__)

PATTERNS_FILE = KNOWLEDGE_DIR / "patterns.jsonl"


def rebuild_patterns(outcomes: list[dict] | None = None) -> list[dict]:
    """从 outcome 数据重新计算所有模式的统计。"""
    if outcomes is None:
        from knowledge.outcome_tracker import load_outcomes
        outcomes = load_outcomes()

    results = []
    for pattern_id, template in PATTERN_TEMPLATES.items():
        matched = []
        for o in outcomes:
            scores = o.get("scores", {})
            scores_with_weighted = {**scores, "综合加权": o.get("weighted_score", 0)}
            if template["condition"](scores_with_weighted):
                matched.append(o)

        if not matched:
            results.append({
                "pattern_id": pattern_id,
                "description": template["description"],
                "sample_count": 0,
                "last_updated": datetime.now().isoformat(timespec="seconds"),
            })
            continue

        n = len(matched)
        directional = [o for o in matched if o.get("direction") != "neutral"]
        nd = len(directional) or 1  # 防除零

        pattern = {
            "pattern_id": pattern_id,
            "description": template["description"],
            "sample_count": n,
            "win_rate_5d": round(sum(1 for o in directional if o.get("hit_5d")) / nd * 100, 1),
            "win_rate_10d": round(sum(1 for o in directional if o.get("hit_10d")) / nd * 100, 1),
            "win_rate_20d": round(sum(1 for o in directional if o.get("hit_20d")) / nd * 100, 1),
            "avg_return_5d": round(sum(o.get("return_5d", 0) for o in matched) / n, 2),
            "avg_return_10d": round(sum(o.get("return_10d", 0) for o in matched) / n, 2),
            "avg_return_20d": round(sum(o.get("return_20d", 0) for o in matched) / n, 2),
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "recent_examples": [
                {
                    "stock": o.get("stock_code", ""),
                    "name": o.get("stock_name", ""),
                    "date": o.get("report_date", ""),
                    "return_10d": o.get("return_10d", 0),
                }
                for o in sorted(matched, key=lambda x: x.get("report_date", ""), reverse=True)[:3]
            ],
        }
        results.append(pattern)

    _save_patterns(results)
    logger.info("[pattern_memory] rebuilt %d patterns from %d outcomes", len(results), len(outcomes))
    return results


def _save_patterns(patterns: list[dict]):
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    with open(PATTERNS_FILE, "w", encoding="utf-8") as f:
        for p in patterns:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")


def load_patterns() -> list[dict]:
    """加载所有模式统计。"""
    if not PATTERNS_FILE.exists():
        return []
    results = []
    for line in PATTERNS_FILE.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return results


def match_current(scores: dict) -> list[dict]:
    """给定当前分析的评分，返回匹配的模式及其历史统计。"""
    patterns = load_patterns()
    if not patterns:
        return []

    matched = []
    for pattern in patterns:
        pid = pattern.get("pattern_id", "")
        template = PATTERN_TEMPLATES.get(pid)
        if not template:
            continue
        if pattern.get("sample_count", 0) < 1:
            continue
        if template["condition"](scores):
            matched.append(pattern)

    return matched
