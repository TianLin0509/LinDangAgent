"""模型绩效追踪 — 按模型、按评分段、按市场环境统计 AI 准确率

从 outcome 数据重新计算各维度的绩效，写入 scorecard.json。
"""

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge"
SCORECARD_FILE = KNOWLEDGE_DIR / "scorecard.json"


def rebuild_scorecard(outcomes: list[dict] | None = None) -> dict:
    """从 outcome 数据重建绩效卡。"""
    if outcomes is None:
        from knowledge.outcome_tracker import load_outcomes
        outcomes = load_outcomes()

    if not outcomes:
        scorecard = {"last_updated": datetime.now().isoformat(timespec="seconds"), "sample_count": 0}
        _save(scorecard)
        return scorecard

    directional = [o for o in outcomes if o.get("direction") != "neutral"]

    scorecard = {
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "sample_count": len(outcomes),
        "directional_count": len(directional),
        "overall": _calc_overall(directional),
        "by_score_bucket": _calc_by_score_bucket(directional),
        "by_model": _calc_by_model(outcomes),
        "by_regime": _calc_by_regime(outcomes),
    }

    _save(scorecard)
    logger.info("[scorecard] rebuilt: %d samples, overall_10d=%.1f%%",
                len(directional), scorecard["overall"].get("hit_rate_10d", 0))
    return scorecard


def _calc_overall(directional: list[dict]) -> dict:
    """整体准确率。"""
    if not directional:
        return {}
    n = len(directional)
    return {
        "total": n,
        "hit_rate_5d": _pct(sum(1 for o in directional if o.get("hit_5d")), n),
        "hit_rate_10d": _pct(sum(1 for o in directional if o.get("hit_10d")), n),
        "hit_rate_20d": _pct(sum(1 for o in directional if o.get("hit_20d")), n),
        "avg_return_10d": round(sum(o.get("return_10d", 0) for o in directional) / n, 2),
    }


def _calc_by_score_bucket(directional: list[dict]) -> dict:
    """按综合加权评分段统计。"""
    buckets = {
        "high_8+": lambda s: s >= 8,
        "mid_high_7": lambda s: 7 <= s < 8,
        "mid_6": lambda s: 6 <= s < 7,
        "low_5-": lambda s: s < 6,
    }
    result = {}
    for name, cond in buckets.items():
        group = [o for o in directional if cond(o.get("weighted_score", 5))]
        if not group:
            result[name] = {"total": 0}
            continue
        n = len(group)
        result[name] = {
            "total": n,
            "hit_rate_10d": _pct(sum(1 for o in group if o.get("hit_10d")), n),
            "avg_return_10d": round(sum(o.get("return_10d", 0) for o in group) / n, 2),
        }
    return result


def _calc_by_model(outcomes: list[dict]) -> dict:
    """按 AI 模型统计（如果 outcome 中有 model 字段）。"""
    by_model: dict[str, list] = defaultdict(list)
    for o in outcomes:
        model = o.get("model", "unknown")
        if model and o.get("direction") != "neutral":
            by_model[model].append(o)

    result = {}
    for model, group in by_model.items():
        n = len(group)
        result[model] = {
            "total": n,
            "hit_rate_10d": _pct(sum(1 for o in group if o.get("hit_10d")), n),
            "avg_return_10d": round(sum(o.get("return_10d", 0) for o in group) / n, 2),
        }
    return result


def _calc_by_regime(outcomes: list[dict]) -> dict:
    """按市场环境统计。"""
    from knowledge.regime_detector import get_regime_history

    regime_map = {e["date"]: e["regime"] for e in get_regime_history(days=365)}
    by_regime: dict[str, list] = defaultdict(list)

    for o in outcomes:
        if o.get("direction") == "neutral":
            continue
        regime = regime_map.get(o.get("report_date", ""), "unknown")
        by_regime[regime].append(o)

    result = {}
    for regime, group in by_regime.items():
        n = len(group)
        result[regime] = {
            "total": n,
            "hit_rate_10d": _pct(sum(1 for o in group if o.get("hit_10d")), n),
            "avg_return_10d": round(sum(o.get("return_10d", 0) for o in group) / n, 2),
        }
    return result


def _pct(count: int, total: int) -> float:
    return round(count / total * 100, 1) if total else 0


def _save(scorecard: dict):
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    SCORECARD_FILE.write_text(
        json.dumps(scorecard, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_scorecard() -> dict:
    """加载最新绩效卡。"""
    if not SCORECARD_FILE.exists():
        return {"sample_count": 0}
    try:
        return json.loads(SCORECARD_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"sample_count": 0}


def get_model_ranking() -> list[tuple[str, float]]:
    """返回各模型按 10 日胜率排名（降序）。"""
    sc = load_scorecard()
    by_model = sc.get("by_model", {})
    ranked = [
        (model, stats.get("hit_rate_10d", 0))
        for model, stats in by_model.items()
        if stats.get("total", 0) >= 3  # 最少 3 个样本
    ]
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


def get_score_calibration() -> dict:
    """返回各评分段的实际胜率，用于校准 AI 评分。"""
    sc = load_scorecard()
    return sc.get("by_score_bucket", {})
