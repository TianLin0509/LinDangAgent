"""模型绩效追踪 — 按模型、按评分段、按市场环境统计 AI 准确率

从 outcome 数据重新计算各维度的绩效，写入 scorecard.json。
"""

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from knowledge.kb_config import KNOWLEDGE_DIR

logger = logging.getLogger(__name__)
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


def get_best_model_for_context(regime: str = "", sector_tags: list[str] = None) -> str | None:
    """根据当前环境和板块，从 scorecard 推荐最优模型。

    返回模型名或 None（样本不足时不推荐）。
    """
    sc = load_scorecard()
    by_model = sc.get("by_model", {})
    by_regime = sc.get("by_regime", {})

    if not by_model:
        return None

    # 优先看当前 regime 下的模型表现
    if regime and regime in by_regime:
        regime_data = by_regime[regime]
        if regime_data.get("total", 0) >= 5:
            # 找该 regime 下各模型的表现（需要从 outcome 数据重算，这里用整体排名近似）
            pass

    # 用整体 10 日胜率排名
    candidates = [
        (model, stats["hit_rate_10d"], stats["total"])
        for model, stats in by_model.items()
        if stats.get("total", 0) >= 5
    ]

    if not candidates:
        return None

    # 按胜率降序，同胜率按样本量降序
    candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)
    best_model, best_rate, best_count = candidates[0]

    logger.info("[scorecard] recommended model: %s (%.1f%% hit rate, %d samples, regime=%s)",
                best_model, best_rate, best_count, regime or "any")
    return best_model


def get_calibration_advice(min_samples: int = 5) -> list[str]:
    """根据评分段实际胜率与预期胜率的偏差，生成校准建议。

    返回0-3条校准警示文本，供 injector 注入 prompt。
    """
    buckets = get_score_calibration()
    if not buckets:
        return []

    # 预期胜率基准（评分越高应该胜率越高）
    expected = {
        "high_8+": 65,
        "mid_high_7": 55,
        "mid_6": 45,
        "low_5-": 35,
    }

    advices = []
    for bucket_name, expect_rate in expected.items():
        stats = buckets.get(bucket_name, {})
        total = stats.get("total", 0)
        if total < min_samples:
            continue

        actual_rate = stats.get("hit_rate_10d", 50)
        gap = actual_rate - expect_rate

        if bucket_name == "high_8+" and gap < -15:
            advices.append(
                f"⚠️ 高分陷阱：评分≥80的股票实际10日胜率仅{actual_rate:.0f}%（{total}样本），"
                f"系统存在高分区过度乐观倾向，扣扳机前务必额外推演"
            )
        elif bucket_name == "low_5-" and gap > 15:
            advices.append(
                f"▸ 低分修正：评分<50的股票实际10日胜率达{actual_rate:.0f}%（{total}样本），"
                f"系统对低分区可能过度悲观，不宜一律回避"
            )
        elif bucket_name in ("mid_high_7", "mid_6") and abs(gap) > 20:
            direction = "偏高" if gap < 0 else "偏低"
            advices.append(
                f"▸ 中分段校准：评分{bucket_name.replace('mid_high_', '').replace('mid_', '')}0-{int(bucket_name[-1]) + 1}0段"
                f"实际胜率{actual_rate:.0f}%（{total}样本），评分{direction}"
            )

    return advices[:3]  # 最多3条
