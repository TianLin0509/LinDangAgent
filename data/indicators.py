# -*- coding: utf-8 -*-
"""技术指标计算 — RSI, MACD, Bollinger, OBV, ATR, KDJ, MFI, 均线评分, 布林挤压"""

import pandas as pd
import numpy as np


def compute_indicators(df: pd.DataFrame) -> dict:
    """Compute full technical indicator suite from price DataFrame.

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
            "macd_hist_trend": "数据不足",
            "bb_upper": None,
            "bb_middle": None,
            "bb_lower": None,
            "bb_width_pct": None,
            "bb_position": "数据不足",
            "bb_squeeze": False,
            "obv_trend": "数据不足",
            "atr_14": None,
            "atr_stop": None,
            "kdj_k": None,
            "kdj_d": None,
            "kdj_j": None,
            "kdj_signal": "数据不足",
            "mfi_14": None,
            "mfi_signal": "数据不足",
            "ma_score": None,
            "ma_score_label": "数据不足",
            "summary": "历史数据不足，无法计算技术指标",
        }

    close = df["收盘"].astype(float)
    high = df["最高"].astype(float)
    low = df["最低"].astype(float)
    volume = df["成交量"].astype(float)
    open_ = df["开盘"].astype(float) if "开盘" in df.columns else close

    # ── RSI(14) ──────────────────────────────────────────────────────────────
    rsi_14 = _compute_rsi(close, 14)
    rsi_signal = _rsi_label(rsi_14)

    # ── MACD(12, 26, 9) ─────────────────────────────────────────────────────
    dif, dea, hist = _compute_macd(close, 12, 26, 9)
    macd_signal = _macd_label(dif, dea, df)
    macd_hist_trend = _macd_hist_trend(close)

    # ── Bollinger Bands(20, 2) ───────────────────────────────────────────────
    bb_upper, bb_middle, bb_lower = _compute_bollinger(close, 20, 2)
    bb_width_pct = (bb_upper - bb_lower) / bb_middle * 100 if bb_middle else 0
    bb_position = _bb_position_label(close.iloc[-1], bb_upper, bb_middle, bb_lower)
    bb_squeeze = _detect_bb_squeeze(close)

    # ── OBV ──────────────────────────────────────────────────────────────────
    obv_trend = _compute_obv_trend(close, volume)

    # ── ATR(14) ──────────────────────────────────────────────────────────────
    atr_14 = _compute_atr(high, low, close, 14)
    atr_stop = round(close.iloc[-1] - 2 * atr_14, 2) if atr_14 else None

    # ── KDJ(9, 3, 3) ────────────────────────────────────────────────────────
    kdj_k, kdj_d, kdj_j = _compute_kdj(high, low, close)
    kdj_signal = _kdj_label(kdj_k, kdj_d, kdj_j)

    # ── MFI(14) ──────────────────────────────────────────────────────────────
    mfi_14 = _compute_mfi(high, low, close, volume, 14)
    mfi_signal = _mfi_label(mfi_14)

    # ── 均线多头/空头评分 ────────────────────────────────────────────────────
    ma_score, ma_score_label = _compute_ma_score(close)

    # ── summary ──────────────────────────────────────────────────────────────
    summary_parts = [
        f"RSI(14)={rsi_14:.1f} {rsi_signal}",
        f"MACD{macd_signal} DIF={dif:.2f} DEA={dea:.2f} {macd_hist_trend}",
        f"布林带{bb_position} 带宽{bb_width_pct:.1f}%{'(挤压!)' if bb_squeeze else ''}",
        f"OBV:{obv_trend}",
        f"ATR(14)={atr_14:.2f} 2ATR止损={atr_stop}" if atr_14 else "",
        f"KDJ({kdj_k:.0f}/{kdj_d:.0f}/{kdj_j:.0f}) {kdj_signal}" if kdj_k else "",
        f"MFI(14)={mfi_14:.0f} {mfi_signal}" if mfi_14 else "",
        f"均线评分={ma_score}/5 {ma_score_label}" if ma_score is not None else "",
    ]

    return {
        "rsi_14": round(rsi_14, 1),
        "rsi_signal": rsi_signal,
        "macd_dif": round(dif, 2),
        "macd_dea": round(dea, 2),
        "macd_hist": round(hist, 2),
        "macd_signal": macd_signal,
        "macd_hist_trend": macd_hist_trend,
        "bb_upper": round(bb_upper, 2),
        "bb_middle": round(bb_middle, 2),
        "bb_lower": round(bb_lower, 2),
        "bb_width_pct": round(bb_width_pct, 1),
        "bb_position": bb_position,
        "bb_squeeze": bb_squeeze,
        "obv_trend": obv_trend,
        "atr_14": round(atr_14, 2) if atr_14 else None,
        "atr_stop": atr_stop,
        "kdj_k": round(kdj_k, 1) if kdj_k else None,
        "kdj_d": round(kdj_d, 1) if kdj_d else None,
        "kdj_j": round(kdj_j, 1) if kdj_j else None,
        "kdj_signal": kdj_signal,
        "mfi_14": round(mfi_14, 1) if mfi_14 else None,
        "mfi_signal": mfi_signal,
        "ma_score": ma_score,
        "ma_score_label": ma_score_label,
        "summary": " | ".join(p for p in summary_parts if p),
    }


# ══════════════════════════════════════════════════════════════════════════════
# RSI
# ══════════════════════════════════════════════════════════════════════════════

def _compute_rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
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
    hist = (dif - dea) * 2
    return float(dif.iloc[-1]), float(dea.iloc[-1]), float(hist.iloc[-1])


def _macd_label(dif: float, dea: float, df: pd.DataFrame) -> str:
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

        if prev_dif <= prev_dea and curr_dif > curr_dea:
            return "金叉(DIF上穿DEA)"
        if prev_dif >= prev_dea and curr_dif < curr_dea:
            return "死叉(DIF下穿DEA)"

    if dif > dea:
        return "DIF>DEA多头"
    return "DIF<DEA空头"


def _macd_hist_trend(close: pd.Series) -> str:
    """判断 MACD 柱状体动能趋势（连续放大/缩小）"""
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea) * 2

    if len(hist) < 5:
        return ""

    recent = hist.iloc[-5:].values
    diffs = np.diff(recent)

    if all(d > 0 for d in diffs[-3:]):
        if recent[-1] > 0:
            return "柱体连续放大(多头加速)"
        return "柱体缩小(空头减弱)"
    if all(d < 0 for d in diffs[-3:]):
        if recent[-1] < 0:
            return "柱体连续放大(空头加速)"
        return "柱体缩小(多头减弱)"
    return "柱体震荡"


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


def _detect_bb_squeeze(close: pd.Series, period: int = 20) -> bool:
    """布林带挤压检测：当前带宽是否处于近120日最窄20%"""
    std = close.rolling(period).std()
    middle = close.rolling(period).mean()
    width = (2 * std / middle * 100).dropna()
    if len(width) < 120:
        return False
    current = width.iloc[-1]
    threshold = width.iloc[-120:].quantile(0.20)
    return current <= threshold


# ══════════════════════════════════════════════════════════════════════════════
# OBV (On-Balance Volume)
# ══════════════════════════════════════════════════════════════════════════════

def _compute_obv_trend(close: pd.Series, volume: pd.Series) -> str:
    """计算 OBV 并判断近 20 日趋势"""
    if len(close) < 20:
        return "数据不足"
    direction = np.sign(close.diff().fillna(0))
    obv = (direction * volume).cumsum()
    obv_20 = obv.iloc[-20:]
    # 线性回归斜率判断趋势
    x = np.arange(len(obv_20))
    slope = np.polyfit(x, obv_20.values, 1)[0]
    obv_ma5 = obv.rolling(5).mean()
    if obv.iloc[-1] > obv_ma5.iloc[-1] and slope > 0:
        return "量价齐升(OBV上行)"
    if obv.iloc[-1] < obv_ma5.iloc[-1] and slope < 0:
        return "量价背离(OBV下行)"
    return "OBV震荡"


# ══════════════════════════════════════════════════════════════════════════════
# ATR (Average True Range)
# ══════════════════════════════════════════════════════════════════════════════

def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series,
                 period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return float(atr.iloc[-1])


# ══════════════════════════════════════════════════════════════════════════════
# KDJ (9, 3, 3)
# ══════════════════════════════════════════════════════════════════════════════

def _compute_kdj(high: pd.Series, low: pd.Series, close: pd.Series,
                 n: int = 9, m1: int = 3, m2: int = 3
                 ) -> tuple[float | None, float | None, float | None]:
    if len(close) < n + m1 + m2:
        return None, None, None
    low_n = low.rolling(n).min()
    high_n = high.rolling(n).max()
    rsv = (close - low_n) / (high_n - low_n + 1e-10) * 100
    k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
    d = k.ewm(alpha=1 / m2, adjust=False).mean()
    j = 3 * k - 2 * d
    return float(k.iloc[-1]), float(d.iloc[-1]), float(j.iloc[-1])


def _kdj_label(k, d, j) -> str:
    if k is None:
        return "数据不足"
    if j > 100:
        return "超买(J>100)"
    if j < 0:
        return "超卖(J<0)"
    if k > d and j > 50:
        return "金叉偏多"
    if k < d and j < 50:
        return "死叉偏空"
    return "中性震荡"


# ══════════════════════════════════════════════════════════════════════════════
# MFI (Money Flow Index)
# ══════════════════════════════════════════════════════════════════════════════

def _compute_mfi(high: pd.Series, low: pd.Series, close: pd.Series,
                 volume: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    typical = (high + low + close) / 3
    raw_mf = typical * volume
    direction = typical.diff()
    pos_mf = pd.Series(np.where(direction > 0, raw_mf, 0), index=close.index)
    neg_mf = pd.Series(np.where(direction < 0, raw_mf, 0), index=close.index)
    pos_sum = pos_mf.rolling(period).sum()
    neg_sum = neg_mf.rolling(period).sum()
    mfi = 100 - 100 / (1 + pos_sum / (neg_sum + 1e-10))
    return float(mfi.iloc[-1])


def _mfi_label(mfi) -> str:
    if mfi is None:
        return "数据不足"
    if mfi >= 80:
        return "资金超买"
    if mfi >= 60:
        return "资金流入"
    if mfi >= 40:
        return "资金中性"
    if mfi >= 20:
        return "资金流出"
    return "资金超卖"


# ══════════════════════════════════════════════════════════════════════════════
# 均线多头/空头评分 (0-5)
# ══════════════════════════════════════════════════════════════════════════════

def _compute_ma_score(close: pd.Series) -> tuple[int | None, str]:
    """均线多头排列评分：5=完美多头, 0=完美空头, 数据不足返回 None"""
    if len(close) < 120:
        return None, "数据不足"
    ma5 = close.rolling(5).mean().iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    ma120 = close.rolling(120).mean().iloc[-1]
    mas = [ma5, ma10, ma20, ma60, ma120]
    score = sum(1 for i in range(len(mas) - 1) if mas[i] > mas[i + 1])
    labels = {
        5: "完美多头排列",
        4: "多头排列(1项偏离)",
        3: "偏多排列",
        2: "偏空排列",
        1: "空头排列(1项偏离)",
        0: "完美空头排列",
    }
    return score, labels.get(score, "中性")


# ══════════════════════════════════════════════════════════════════════════════
# 格式化输出
# ══════════════════════════════════════════════════════════════════════════════

def format_indicators_section(indicators: dict) -> str:
    """将指标字典格式化为 prompt 中的技术指标段落"""
    if indicators.get("rsi_14") is None:
        return ""

    lines = [
        "## 技术指标",
        indicators['summary'],
        "",
        f"RSI(14): {indicators['rsi_14']}  信号: {indicators['rsi_signal']}",
        f"MACD: DIF={indicators['macd_dif']}  DEA={indicators['macd_dea']}  "
        f"柱状={indicators['macd_hist']}  信号: {indicators['macd_signal']}  "
        f"动能: {indicators['macd_hist_trend']}",
        f"布林带(20,2): 上轨={indicators['bb_upper']}  中轨={indicators['bb_middle']}  "
        f"下轨={indicators['bb_lower']}  带宽={indicators['bb_width_pct']}%  "
        f"位置: {indicators['bb_position']}"
        f"{'  *** 布林挤压(变盘前兆!) ***' if indicators.get('bb_squeeze') else ''}",
        f"OBV趋势: {indicators['obv_trend']}",
    ]

    if indicators.get("atr_14"):
        lines.append(
            f"ATR(14): {indicators['atr_14']}  "
            f"2ATR止损位: {indicators['atr_stop']}元  "
            f"(距现价{abs(indicators['atr_14'] * 2 / indicators['bb_middle'] * 100):.1f}%)"
            if indicators.get('bb_middle') else
            f"ATR(14): {indicators['atr_14']}  2ATR止损位: {indicators['atr_stop']}元"
        )

    if indicators.get("kdj_k") is not None:
        lines.append(
            f"KDJ(9,3,3): K={indicators['kdj_k']}  D={indicators['kdj_d']}  "
            f"J={indicators['kdj_j']}  信号: {indicators['kdj_signal']}"
        )

    if indicators.get("mfi_14") is not None:
        lines.append(f"MFI(14): {indicators['mfi_14']}  信号: {indicators['mfi_signal']}")

    if indicators.get("ma_score") is not None:
        lines.append(
            f"均线排列评分: {indicators['ma_score']}/5  "
            f"状态: {indicators['ma_score_label']}"
        )

    return "\n".join(lines)
