"""市场环境识别 — 基于上证指数简单规则判断牛/熊/震荡/轮动

使用 MA20/MA60 位置关系和 20 日涨跌幅分类市场环境，
追踪各环境下 AI 分析的准确率。
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge"
REGIME_FILE = KNOWLEDGE_DIR / "regime_log.jsonl"
_lock = threading.Lock()

# 上证指数代码
SH_INDEX_CODE = "000001.SH"

REGIME_LABELS = {
    "bull": "牛市",
    "bear": "熊市",
    "shock": "震荡市",
    "rotation": "轮动市",
}


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
        near_ma60 = abs(latest - ma60) / ma60 * 100 < 3  # ±3%

        # 分类
        if above_ma60 and ma20_above_ma60 and ret_20d > 5:
            regime = "bull"
        elif not above_ma60 and not ma20_above_ma60 and ret_20d < -5:
            regime = "bear"
        elif near_ma60:
            regime = "shock"
        elif above_ma60 and ret_20d < 2:
            regime = "rotation"
        else:
            regime = "shock"

        indicators = {
            "latest_close": round(latest, 2),
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
            "ret_20d": round(ret_20d, 2),
            "above_ma60": above_ma60,
            "ma20_above_ma60": ma20_above_ma60,
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


def _fallback_regime(reason: str) -> dict:
    """数据不可用时返回默认震荡市。"""
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "regime": "shock",
        "regime_label": "震荡市",
        "indicators": {"fallback_reason": reason},
    }


def _save_regime(entry: dict):
    """追加到 regime_log.jsonl，同日覆盖。"""
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    today = entry["date"]

    with _lock:
        lines = []
        if REGIME_FILE.exists():
            for line in REGIME_FILE.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                try:
                    existing = json.loads(line)
                    if existing.get("date") == today:
                        continue  # 覆盖同日记录
                    lines.append(line)
                except json.JSONDecodeError:
                    continue
        lines.append(json.dumps(entry, ensure_ascii=False))
        REGIME_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_current_regime() -> dict | None:
    """获取最近一次 regime 记录（不重新检测）。"""
    if not REGIME_FILE.exists():
        return None
    lines = REGIME_FILE.read_text(encoding="utf-8").strip().split("\n")
    for line in reversed(lines):
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def get_regime_history(days: int = 90) -> list[dict]:
    """返回最近 N 天的 regime 日志。"""
    if not REGIME_FILE.exists():
        return []
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    results = []
    for line in REGIME_FILE.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("date", "") >= cutoff:
                results.append(entry)
        except json.JSONDecodeError:
            continue
    return results


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
