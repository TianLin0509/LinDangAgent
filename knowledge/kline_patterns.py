# -*- coding: utf-8 -*-
"""K线形态识别库 — 20+ 经典形态检测

从 DataFrame（含 OHLCV 数据）中识别当日及近期的 K 线形态，
包括单K线、双K线组合、三K线组合、量价组合、指标背离。

每个检测函数接收最近 N 根 K 线的数据，返回是否匹配。
detect_all_patterns() 是统一入口，返回当日所有匹配的形态列表。
"""

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── 形态定义 ─────────────────────────────────────────────────────

PATTERN_INFO = {
    # 单K线
    "hammer":           ("锤子线", "底部反转信号：长下影线(≥实体2倍)，小实体在上方"),
    "inverted_hammer":  ("倒锤子", "底部试探：长上影线，小实体在下方"),
    "doji":             ("十字星", "犹豫信号：开盘≈收盘，多空平衡"),
    "marubozu_bull":    ("大阳线", "强势信号：大实体阳线，几乎无影线"),
    "marubozu_bear":    ("大阴线", "恐慌信号：大实体阴线，几乎无影线"),

    # 双K线组合
    "bullish_engulf":   ("阳包阴", "底部反转：今日阳线完全包住昨日阴线"),
    "bearish_engulf":   ("阴包阳", "顶部反转：今日阴线完全包住昨日阳线"),
    "piercing_line":    ("刺透线", "底部反转：阳线深入前日阴线实体50%以上"),
    "dark_cloud":       ("乌云盖顶", "顶部压力：阴线深入前日阳线实体50%以上"),

    # 三K线组合
    "morning_star":     ("启明星", "底部三K反转：阴线+小实体+阳线"),
    "evening_star":     ("黄昏星", "顶部三K反转：阳线+小实体+阴线"),
    "three_soldiers":   ("红三兵", "连续上攻：三根依次走高的阳线"),
    "three_crows":      ("三只乌鸦", "连续下杀：三根依次走低的阴线"),

    # 量价组合
    "vol_breakout":     ("放量突破", "量价齐升突破近期高点"),
    "shrink_pullback":  ("缩量回踩", "缩量回调至均线支撑，健康回调"),
    "vol_top_diverge":  ("顶部量价背离", "价格新高但成交量萎缩"),
    "vol_bot_diverge":  ("底部量价背离", "价格新低但成交量萎缩"),

    # 指标背离
    "macd_bull_div":    ("MACD底背离", "价格新低但MACD不新低，反转信号"),
    "macd_bear_div":    ("MACD顶背离", "价格新高但MACD不新高，见顶信号"),
    "rsi_oversold":     ("RSI超卖", "RSI<30，超卖区域"),
    "rsi_overbought":   ("RSI超买", "RSI>70，超买区域"),
}


@dataclass
class PatternMatch:
    pattern_id: str
    name: str
    description: str
    strength: float  # 0-1，形态强度


# ── 辅助函数 ─────────────────────────────────────────────────────

def _body(row) -> float:
    """实体大小（正=阳线，负=阴线）"""
    return row["close"] - row["open"]


def _body_pct(row) -> float:
    """实体占比 = 实体/振幅"""
    rng = row["high"] - row["low"]
    return abs(_body(row)) / rng if rng > 0 else 0


def _upper_shadow(row) -> float:
    return row["high"] - max(row["open"], row["close"])


def _lower_shadow(row) -> float:
    return min(row["open"], row["close"]) - row["low"]


def _range(row) -> float:
    return row["high"] - row["low"]


def _is_bull(row) -> bool:
    return row["close"] > row["open"]


def _is_bear(row) -> bool:
    return row["close"] < row["open"]


# ── 单K线形态 ────────────────────────────────────────────────────

def _detect_hammer(df: pd.DataFrame) -> PatternMatch | None:
    """锤子线：长下影(≥实体2倍)，上影短，实体小，在底部区域更有意义"""
    r = df.iloc[-1]
    rng = _range(r)
    if rng <= 0:
        return None

    body = abs(_body(r))
    lower = _lower_shadow(r)
    upper = _upper_shadow(r)

    if lower >= body * 2 and upper < body * 0.5 and body / rng < 0.4:
        strength = min(lower / (body + 0.001) / 4, 1.0)
        return PatternMatch("hammer", *PATTERN_INFO["hammer"], strength)
    return None


def _detect_inverted_hammer(df: pd.DataFrame) -> PatternMatch | None:
    r = df.iloc[-1]
    rng = _range(r)
    if rng <= 0:
        return None

    body = abs(_body(r))
    upper = _upper_shadow(r)
    lower = _lower_shadow(r)

    if upper >= body * 2 and lower < body * 0.5 and body / rng < 0.4:
        strength = min(upper / (body + 0.001) / 4, 1.0)
        return PatternMatch("inverted_hammer", *PATTERN_INFO["inverted_hammer"], strength)
    return None


def _detect_doji(df: pd.DataFrame) -> PatternMatch | None:
    r = df.iloc[-1]
    rng = _range(r)
    if rng <= 0:
        return None

    body = abs(_body(r))
    if body / rng < 0.1:  # 实体不到振幅10%
        strength = 1.0 - body / rng * 10  # 实体越小越强
        return PatternMatch("doji", *PATTERN_INFO["doji"], max(strength, 0.3))
    return None


def _detect_marubozu_bull(df: pd.DataFrame) -> PatternMatch | None:
    r = df.iloc[-1]
    rng = _range(r)
    if rng <= 0 or not _is_bull(r):
        return None

    body = _body(r)
    if body / rng > 0.85 and body / r["open"] > 0.03:  # 实体占振幅85%+，涨幅>3%
        strength = min(body / rng, 1.0)
        return PatternMatch("marubozu_bull", *PATTERN_INFO["marubozu_bull"], strength)
    return None


def _detect_marubozu_bear(df: pd.DataFrame) -> PatternMatch | None:
    r = df.iloc[-1]
    rng = _range(r)
    if rng <= 0 or not _is_bear(r):
        return None

    body = abs(_body(r))
    if body / rng > 0.85 and body / r["open"] > 0.03:
        strength = min(body / rng, 1.0)
        return PatternMatch("marubozu_bear", *PATTERN_INFO["marubozu_bear"], strength)
    return None


# ── 双K线组合 ────────────────────────────────────────────────────

def _detect_bullish_engulf(df: pd.DataFrame) -> PatternMatch | None:
    if len(df) < 2:
        return None
    prev, curr = df.iloc[-2], df.iloc[-1]

    if (_is_bear(prev) and _is_bull(curr)
            and curr["close"] > prev["open"] and curr["open"] < prev["close"]):
        strength = abs(_body(curr)) / (abs(_body(prev)) + 0.001)
        return PatternMatch("bullish_engulf", *PATTERN_INFO["bullish_engulf"], min(strength / 2, 1.0))
    return None


def _detect_bearish_engulf(df: pd.DataFrame) -> PatternMatch | None:
    if len(df) < 2:
        return None
    prev, curr = df.iloc[-2], df.iloc[-1]

    if (_is_bull(prev) and _is_bear(curr)
            and curr["open"] > prev["close"] and curr["close"] < prev["open"]):
        strength = abs(_body(curr)) / (abs(_body(prev)) + 0.001)
        return PatternMatch("bearish_engulf", *PATTERN_INFO["bearish_engulf"], min(strength / 2, 1.0))
    return None


def _detect_piercing_line(df: pd.DataFrame) -> PatternMatch | None:
    if len(df) < 2:
        return None
    prev, curr = df.iloc[-2], df.iloc[-1]

    if (_is_bear(prev) and _is_bull(curr)
            and curr["open"] < prev["low"]
            and curr["close"] > (prev["open"] + prev["close"]) / 2):
        mid = (prev["open"] + prev["close"]) / 2
        penetration = (curr["close"] - mid) / (abs(_body(prev)) + 0.001)
        return PatternMatch("piercing_line", *PATTERN_INFO["piercing_line"], min(penetration, 1.0))
    return None


def _detect_dark_cloud(df: pd.DataFrame) -> PatternMatch | None:
    if len(df) < 2:
        return None
    prev, curr = df.iloc[-2], df.iloc[-1]

    if (_is_bull(prev) and _is_bear(curr)
            and curr["open"] > prev["high"]
            and curr["close"] < (prev["open"] + prev["close"]) / 2):
        mid = (prev["open"] + prev["close"]) / 2
        penetration = (mid - curr["close"]) / (abs(_body(prev)) + 0.001)
        return PatternMatch("dark_cloud", *PATTERN_INFO["dark_cloud"], min(penetration, 1.0))
    return None


# ── 三K线组合 ────────────────────────────────────────────────────

def _detect_morning_star(df: pd.DataFrame) -> PatternMatch | None:
    if len(df) < 3:
        return None
    d1, d2, d3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    if (_is_bear(d1) and abs(_body(d1)) / (_range(d1) + 0.001) > 0.5
            and _body_pct(d2) < 0.3  # 中间小实体
            and _is_bull(d3) and d3["close"] > (d1["open"] + d1["close"]) / 2):
        return PatternMatch("morning_star", *PATTERN_INFO["morning_star"], 0.8)
    return None


def _detect_evening_star(df: pd.DataFrame) -> PatternMatch | None:
    if len(df) < 3:
        return None
    d1, d2, d3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    if (_is_bull(d1) and abs(_body(d1)) / (_range(d1) + 0.001) > 0.5
            and _body_pct(d2) < 0.3
            and _is_bear(d3) and d3["close"] < (d1["open"] + d1["close"]) / 2):
        return PatternMatch("evening_star", *PATTERN_INFO["evening_star"], 0.8)
    return None


def _detect_three_soldiers(df: pd.DataFrame) -> PatternMatch | None:
    if len(df) < 3:
        return None
    d1, d2, d3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    if (all(_is_bull(d) for d in [d1, d2, d3])
            and d2["close"] > d1["close"] and d3["close"] > d2["close"]
            and d2["open"] > d1["open"] and d3["open"] > d2["open"]):
        return PatternMatch("three_soldiers", *PATTERN_INFO["three_soldiers"], 0.75)
    return None


def _detect_three_crows(df: pd.DataFrame) -> PatternMatch | None:
    if len(df) < 3:
        return None
    d1, d2, d3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    if (all(_is_bear(d) for d in [d1, d2, d3])
            and d2["close"] < d1["close"] and d3["close"] < d2["close"]
            and d2["open"] < d1["open"] and d3["open"] < d2["open"]):
        return PatternMatch("three_crows", *PATTERN_INFO["three_crows"], 0.75)
    return None


# ── 量价组合 ─────────────────────────────────────────────────────

def _detect_vol_breakout(df: pd.DataFrame) -> PatternMatch | None:
    """放量突破：今日收盘创20日新高 + 量比>1.5"""
    if len(df) < 20:
        return None
    curr = df.iloc[-1]
    high_20 = df["high"].iloc[-21:-1].max()
    vol_ma5 = df["vol"].iloc[-6:-1].mean()

    if (curr["close"] > high_20 and vol_ma5 > 0
            and curr["vol"] / vol_ma5 > 1.5 and _is_bull(curr)):
        vol_ratio = curr["vol"] / vol_ma5
        strength = min(vol_ratio / 3, 1.0)
        return PatternMatch("vol_breakout", *PATTERN_INFO["vol_breakout"], strength)
    return None


def _detect_shrink_pullback(df: pd.DataFrame) -> PatternMatch | None:
    """缩量回踩：近3日下跌但量萎缩，且在MA20附近"""
    if len(df) < 20:
        return None
    curr = df.iloc[-1]
    ma20 = df["close"].iloc[-20:].mean()
    vol_ma5 = df["vol"].iloc[-6:-1].mean()

    # 近3日有下跌 + 量萎缩 + 靠近MA20
    ret_3 = (curr["close"] - df.iloc[-4]["close"]) / df.iloc[-4]["close"]
    vol_shrink = vol_ma5 > 0 and curr["vol"] / vol_ma5 < 0.7
    near_ma20 = abs(curr["close"] - ma20) / ma20 < 0.03

    if ret_3 < -0.01 and vol_shrink and near_ma20:
        return PatternMatch("shrink_pullback", *PATTERN_INFO["shrink_pullback"], 0.7)
    return None


def _detect_vol_top_diverge(df: pd.DataFrame) -> PatternMatch | None:
    """顶部量价背离：近10日价格创新高但量没创新高"""
    if len(df) < 20:
        return None

    price_10 = df["close"].iloc[-10:]
    vol_10 = df["vol"].iloc[-10:]
    price_prev10 = df["close"].iloc[-20:-10]
    vol_prev10 = df["vol"].iloc[-20:-10]

    if (price_10.max() > price_prev10.max()
            and vol_10.max() < vol_prev10.max() * 0.8):
        return PatternMatch("vol_top_diverge", *PATTERN_INFO["vol_top_diverge"], 0.65)
    return None


def _detect_vol_bot_diverge(df: pd.DataFrame) -> PatternMatch | None:
    """底部量价背离：近10日价格创新低但量没创新低"""
    if len(df) < 20:
        return None

    price_10 = df["close"].iloc[-10:]
    vol_10 = df["vol"].iloc[-10:]
    price_prev10 = df["close"].iloc[-20:-10]
    vol_prev10 = df["vol"].iloc[-20:-10]

    if (price_10.min() < price_prev10.min()
            and vol_10.min() > vol_prev10.min() * 1.2):
        return PatternMatch("vol_bot_diverge", *PATTERN_INFO["vol_bot_diverge"], 0.65)
    return None


# ── 指标背离 ─────────────────────────────────────────────────────

def _compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss + 1e-10)
    return 100 - 100 / (1 + rs)


def _compute_macd(closes: pd.Series) -> tuple[pd.Series, pd.Series]:
    ema12 = closes.ewm(span=12).mean()
    ema26 = closes.ewm(span=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9).mean()
    return dif, dea


def _detect_macd_bull_div(df: pd.DataFrame) -> PatternMatch | None:
    """MACD底背离：价格创20日新低但DIF没创新低"""
    if len(df) < 30:
        return None

    dif, _ = _compute_macd(df["close"])
    if len(dif) < 20:
        return None

    price_10 = df["close"].iloc[-10:]
    price_prev10 = df["close"].iloc[-20:-10]
    dif_10 = dif.iloc[-10:]
    dif_prev10 = dif.iloc[-20:-10]

    if (price_10.min() < price_prev10.min()
            and dif_10.min() > dif_prev10.min()):
        return PatternMatch("macd_bull_div", *PATTERN_INFO["macd_bull_div"], 0.7)
    return None


def _detect_macd_bear_div(df: pd.DataFrame) -> PatternMatch | None:
    """MACD顶背离：价格创20日新高但DIF没创新高"""
    if len(df) < 30:
        return None

    dif, _ = _compute_macd(df["close"])
    if len(dif) < 20:
        return None

    price_10 = df["close"].iloc[-10:]
    price_prev10 = df["close"].iloc[-20:-10]
    dif_10 = dif.iloc[-10:]
    dif_prev10 = dif.iloc[-20:-10]

    if (price_10.max() > price_prev10.max()
            and dif_10.max() < dif_prev10.max()):
        return PatternMatch("macd_bear_div", *PATTERN_INFO["macd_bear_div"], 0.7)
    return None


def _detect_rsi_oversold(df: pd.DataFrame) -> PatternMatch | None:
    if len(df) < 20:
        return None
    rsi = _compute_rsi(df["close"])
    if rsi.iloc[-1] < 30:
        strength = (30 - rsi.iloc[-1]) / 30
        return PatternMatch("rsi_oversold", *PATTERN_INFO["rsi_oversold"], min(strength, 1.0))
    return None


def _detect_rsi_overbought(df: pd.DataFrame) -> PatternMatch | None:
    if len(df) < 20:
        return None
    rsi = _compute_rsi(df["close"])
    if rsi.iloc[-1] > 70:
        strength = (rsi.iloc[-1] - 70) / 30
        return PatternMatch("rsi_overbought", *PATTERN_INFO["rsi_overbought"], min(strength, 1.0))
    return None


# ── 统一入口 ─────────────────────────────────────────────────────

ALL_DETECTORS = [
    _detect_hammer, _detect_inverted_hammer, _detect_doji,
    _detect_marubozu_bull, _detect_marubozu_bear,
    _detect_bullish_engulf, _detect_bearish_engulf,
    _detect_piercing_line, _detect_dark_cloud,
    _detect_morning_star, _detect_evening_star,
    _detect_three_soldiers, _detect_three_crows,
    _detect_vol_breakout, _detect_shrink_pullback,
    _detect_vol_top_diverge, _detect_vol_bot_diverge,
    _detect_macd_bull_div, _detect_macd_bear_div,
    _detect_rsi_oversold, _detect_rsi_overbought,
]


def detect_all_patterns(df: pd.DataFrame) -> list[PatternMatch]:
    """检测 DataFrame 中最后一根K线的所有匹配形态。

    df 需要包含: open, high, low, close, vol 列，至少30行。
    返回匹配的 PatternMatch 列表，按 strength 降序。
    """
    if df is None or len(df) < 3:
        return []

    # 确保列名小写
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    # 兼容 volume/vol
    if "volume" in df.columns and "vol" not in df.columns:
        df["vol"] = df["volume"]

    matches = []
    for detector in ALL_DETECTORS:
        try:
            result = detector(df)
            if result:
                matches.append(result)
        except Exception as exc:
            logger.debug("[kline_patterns] %s failed: %r", detector.__name__, exc)

    matches.sort(key=lambda m: m.strength, reverse=True)
    return matches


def classify_position(df: pd.DataFrame) -> str:
    """判断当前价格在近期区间的位置：底部/中部/顶部"""
    if len(df) < 20:
        return "未知"

    curr = df.iloc[-1]["close"]
    high_20 = df["high"].iloc[-20:].max()
    low_20 = df["low"].iloc[-20:].min()
    rng = high_20 - low_20

    if rng <= 0:
        return "中部"

    position = (curr - low_20) / rng
    if position < 0.3:
        return "底部"
    elif position > 0.7:
        return "顶部"
    return "中部"


def classify_volume_state(df: pd.DataFrame) -> str:
    """判断当前量能状态：放量/缩量/平量"""
    if len(df) < 6:
        return "未知"

    curr_vol = df.iloc[-1]["vol"] if "vol" in df.columns else df.iloc[-1].get("volume", 0)
    vol_ma5 = df["vol"].iloc[-6:-1].mean() if "vol" in df.columns else 0

    if vol_ma5 <= 0:
        return "未知"

    ratio = curr_vol / vol_ma5
    if ratio > 1.5:
        return "放量"
    elif ratio < 0.7:
        return "缩量"
    return "平量"
