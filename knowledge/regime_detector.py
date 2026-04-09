"""市场环境识别 — 基于上证指数简单规则判断牛/熊/震荡/轮动

使用 MA20/MA60 位置关系和 20 日涨跌幅分类市场环境，
追踪各环境下 AI 分析的准确率。
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from knowledge.kb_config import (
    KNOWLEDGE_DIR, REGIME_LABELS, SH_INDEX_CODE,
    REGIME_NEAR_MA60_PCT, REGIME_RET_BULL, REGIME_RET_BEAR,
    REGIME_RET_ROTATION, REGIME_HYSTERESIS_DAYS,
)

logger = logging.getLogger(__name__)

REGIME_FILE = KNOWLEDGE_DIR / "regime_log.jsonl"
_lock = threading.Lock()


def detect_current_regime() -> dict:
    """检测当前市场环境并写入日志。返回 regime 信息 dict。"""
    from data.tushare_client import get_price_df

    try:
        price_df, err = get_price_df(SH_INDEX_CODE)
        if err or price_df is None or price_df.empty:
            logger.warning("[regime] failed to get SH index: %s", err)
            return _fallback_regime("data_unavailable")
    except Exception as exc:
        logger.warning("[regime] exception getting SH index: %r", exc)
        return _fallback_regime("data_unavailable")

    try:
        df = price_df.copy()
        closes = df["收盘"].astype(float).values

        if len(closes) < 60:
            return _fallback_regime("insufficient_data")

        latest = closes[-1]
        ma20 = closes[-20:].mean()
        ma60 = closes[-60:].mean()

        ret_20d = (latest - closes[-20]) / closes[-20] * 100

        # MA 位置
        above_ma60 = latest > ma60
        ma20_above_ma60 = ma20 > ma60
        near_ma60 = abs(latest - ma60) / ma60 * 100 < REGIME_NEAR_MA60_PCT

        # 分类
        if above_ma60 and ma20_above_ma60 and ret_20d > REGIME_RET_BULL:
            raw_regime = "bull"
        elif not above_ma60 and not ma20_above_ma60 and ret_20d < REGIME_RET_BEAR:
            raw_regime = "bear"
        elif near_ma60:
            raw_regime = "shock"
        elif above_ma60 and ret_20d < REGIME_RET_ROTATION:
            raw_regime = "rotation"
        else:
            raw_regime = "shock"

        # 滞后逻辑：只有连续 N 天相同环境才确认切换，避免分界线附近频繁切换
        regime = _apply_hysteresis(raw_regime)

        indicators = {
            "latest_close": round(float(latest), 2),
            "ma20": round(float(ma20), 2),
            "ma60": round(float(ma60), 2),
            "ret_20d": round(float(ret_20d), 2),
            "above_ma60": bool(above_ma60),
            "ma20_above_ma60": bool(ma20_above_ma60),
            "raw_regime": raw_regime,
        }

        result = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "regime": regime,
            "regime_label": REGIME_LABELS[regime],
            "indicators": indicators,
        }

        _save_regime(result)
        logger.info("[regime] detected: %s (%s), ret_20d=%.1f%%", regime, REGIME_LABELS[regime], ret_20d)
        return result

    except Exception as exc:
        logger.warning("[regime] classification error: %r", exc)
        return _fallback_regime("classification_error")


def _apply_hysteresis(raw_regime: str) -> str:
    """滞后逻辑：最近连续 N 天检测到同一环境才切换，否则保持上一次确认的环境。"""
    current = get_current_regime()
    if current is None:
        return raw_regime  # 首次检测，直接采用

    prev_regime = current.get("regime", "shock")
    if raw_regime == prev_regime:
        return raw_regime  # 与当前一致，无需切换

    # 检查最近 N 天的历史记录是否全部为 raw_regime
    history = get_regime_history(days=REGIME_HYSTERESIS_DAYS + 1)
    recent_regimes = [
        h.get("indicators", {}).get("raw_regime", h.get("regime", ""))
        for h in history[-REGIME_HYSTERESIS_DAYS:]
    ]
    if len(recent_regimes) >= REGIME_HYSTERESIS_DAYS and all(r == raw_regime for r in recent_regimes):
        logger.info("[regime] hysteresis passed: %s -> %s (confirmed after %d days)",
                     prev_regime, raw_regime, REGIME_HYSTERESIS_DAYS)
        return raw_regime

    logger.debug("[regime] hysteresis holding: raw=%s, keeping=%s", raw_regime, prev_regime)
    return prev_regime


def _fallback_regime(reason: str) -> dict:
    """数据不可用时返回默认震荡市。"""
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "regime": "shock",
        "regime_label": "震荡市",
        "indicators": {"fallback_reason": reason},
    }


def _save_regime(entry: dict):
    """追加到 regime_log.jsonl，同日覆盖（原子操作）。"""
    from knowledge.kb_io import upsert_jsonl_by_key
    upsert_jsonl_by_key(REGIME_FILE, entry, key_field="date", lock=_lock)


def get_current_regime() -> dict | None:
    """获取最近一次 regime 记录（不重新检测）。"""
    from knowledge.kb_io import read_jsonl_tail
    tail = read_jsonl_tail(REGIME_FILE, n=1)
    return tail[0] if tail else None


def get_regime_history(days: int = 90) -> list[dict]:
    """返回最近 N 天的 regime 日志。"""
    from knowledge.kb_io import read_jsonl_recent
    return read_jsonl_recent(REGIME_FILE, days=days, date_field="date")


def get_regime_accuracy(regime: str) -> dict:
    """计算特定市场环境下的 AI 准确率。需要 outcome 数据。"""
    from knowledge.outcome_tracker import load_outcomes

    outcomes = load_outcomes()
    regime_history = {e["date"]: e["regime"] for e in get_regime_history(days=365)}

    matched = []
    for o in outcomes:
        report_date = o.get("report_date", "")
        if regime_history.get(report_date) == regime:
            matched.append(o)

    if not matched:
        return {"regime": regime, "sample_count": 0}

    directional = [o for o in matched if o.get("direction") != "neutral"]
    if not directional:
        return {"regime": regime, "sample_count": len(matched), "directional_count": 0}

    hit_10 = sum(1 for o in directional if o.get("hit_10d"))
    return {
        "regime": regime,
        "regime_label": REGIME_LABELS.get(regime, regime),
        "sample_count": len(matched),
        "directional_count": len(directional),
        "hit_rate_10d": round(hit_10 / len(directional) * 100, 1),
    }
