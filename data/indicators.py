"""技术指标计算 — RSI(14), MACD(12,26,9), Bollinger Bands(20,2)"""

import pandas as pd
import numpy as np


def compute_indicators(df: pd.DataFrame) -> dict:
    """Compute RSI(14), MACD(12,26,9), Bollinger Bands(20,2) from price DataFrame.

    df has columns: 日期, 开盘, 最高, 最低, 收盘, 成交量, 涨跌幅
    Returns dict with human-readable summary strings + raw values.
    """
    if df is None or df.empty or len(df) < 26:
        return {
            "rsi_14": None,
            "rsi_signal": "数据不足",
            "macd_dif": None,
            "macd_dea": None,
            "macd_hist": None,
            "macd_signal": "数据不足",
            "bb_upper": None,
            "bb_middle": None,
            "bb_lower": None,
            "bb_width_pct": None,
            "bb_position": "数据不足",
            "summary": "历史数据不足，无法计算技术指标",
        }

    close = df["收盘"].astype(float)

    # ── RSI(14) ──────────────────────────────────────────────────────────────
    rsi_14 = _compute_rsi(close, 14)
    rsi_signal = _rsi_label(rsi_14)

    # ── MACD(12, 26, 9) ─────────────────────────────────────────────────────
    dif, dea, hist = _compute_macd(close, 12, 26, 9)
    macd_signal = _macd_label(dif, dea, df)

    # ── Bollinger Bands(20, 2) ───────────────────────────────────────────────
    bb_upper, bb_middle, bb_lower = _compute_bollinger(close, 20, 2)
    bb_width_pct = (bb_upper - bb_lower) / bb_middle * 100 if bb_middle else 0
    bb_position = _bb_position_label(close.iloc[-1], bb_upper, bb_middle, bb_lower)

    # ── summary ──────────────────────────────────────────────────────────────
    summary_parts = [
        f"RSI(14)={rsi_14:.1f} {rsi_signal}",
        f"MACD{macd_signal} DIF={dif:.2f} DEA={dea:.2f}",
        f"布林带{bb_position} 带宽{bb_width_pct:.1f}%",
    ]

    return {
        "rsi_14": round(rsi_14, 1),
        "rsi_signal": rsi_signal,
        "macd_dif": round(dif, 2),
        "macd_dea": round(dea, 2),
        "macd_hist": round(hist, 2),
        "macd_signal": macd_signal,
        "bb_upper": round(bb_upper, 2),
        "bb_middle": round(bb_middle, 2),
        "bb_lower": round(bb_lower, 2),
        "bb_width_pct": round(bb_width_pct, 1),
        "bb_position": bb_position,
        "summary": " | ".join(summary_parts),
    }


# ══════════════════════════════════════════════════════════════════════════════
# RSI
# ══════════════════════════════════════════════════════════════════════════════

def _compute_rsi(close: pd.Series, period: int = 14) -> float:
    """标准 RSI：先用 SMA 初始化，再用 Wilder 平滑"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder 平滑 (equivalent to EMA with alpha=1/period)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return float(rsi.iloc[-1])


def _rsi_label(rsi: float) -> str:
    if rsi >= 70:
        return "超买"
    if rsi >= 55:
        return "中性偏强"
    if rsi >= 45:
        return "中性"
    if rsi >= 30:
        return "中性偏弱"
    return "超卖"


# ══════════════════════════════════════════════════════════════════════════════
# MACD
# ══════════════════════════════════════════════════════════════════════════════

def _compute_macd(close: pd.Series, fast: int = 12, slow: int = 26,
                  signal: int = 9) -> tuple[float, float, float]:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2  # MACD 柱状图
    return float(dif.iloc[-1]), float(dea.iloc[-1]), float(hist.iloc[-1])


def _macd_label(dif: float, dea: float, df: pd.DataFrame) -> str:
    """判断 MACD 金叉/死叉/多头/空头"""
    close = df["收盘"].astype(float)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif_series = ema12 - ema26
    dea_series = dif_series.ewm(span=9, adjust=False).mean()

    if len(dif_series) >= 2:
        prev_dif = float(dif_series.iloc[-2])
        prev_dea = float(dea_series.iloc[-2])
        curr_dif = float(dif_series.iloc[-1])
        curr_dea = float(dea_series.iloc[-1])

        # 金叉：前一日 DIF <= DEA，当日 DIF > DEA
        if prev_dif <= prev_dea and curr_dif > curr_dea:
            return "金叉（DIF上穿DEA）"
        # 死叉：前一日 DIF >= DEA，当日 DIF < DEA
        if prev_dif >= prev_dea and curr_dif < curr_dea:
            return "死叉（DIF下穿DEA）"

    if dif > dea:
        return "DIF>DEA多头"
    return "DIF<DEA空头"


# ══════════════════════════════════════════════════════════════════════════════
# Bollinger Bands
# ══════════════════════════════════════════════════════════════════════════════

def _compute_bollinger(close: pd.Series, period: int = 20,
                       num_std: int = 2) -> tuple[float, float, float]:
    middle = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return float(upper.iloc[-1]), float(middle.iloc[-1]), float(lower.iloc[-1])


def _bb_position_label(price: float, upper: float, middle: float,
                       lower: float) -> str:
    band_width = upper - lower
    if band_width <= 0:
        return "数据异常"

    if price > upper:
        return "上轨之上"
    if price > upper - band_width * 0.1:
        return "上轨附近"
    if price > middle + band_width * 0.05:
        return "中轨上方"
    if price > middle - band_width * 0.05:
        return "中轨附近"
    if price > lower + band_width * 0.1:
        return "中轨下方"
    if price > lower:
        return "下轨附近"
    return "下轨之下"


def format_indicators_section(indicators: dict) -> str:
    """将指标字典格式化为 prompt 中的技术指标段落"""
    if indicators.get("rsi_14") is None:
        return ""

    return f"""## 技术指标
{indicators['summary']}

RSI(14): {indicators['rsi_14']}  信号: {indicators['rsi_signal']}
MACD: DIF={indicators['macd_dif']}  DEA={indicators['macd_dea']}  柱状={indicators['macd_hist']}  信号: {indicators['macd_signal']}
布林带(20,2): 上轨={indicators['bb_upper']}  中轨={indicators['bb_middle']}  下轨={indicators['bb_lower']}  带宽={indicators['bb_width_pct']}%  位置: {indicators['bb_position']}"""
