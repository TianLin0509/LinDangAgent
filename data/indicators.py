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

    # ── 量比（当日量 / 近5日均量）────────────────────────────────────────────
    volume_ratio, volume_ratio_label = _compute_volume_ratio(volume)

    # ── ADX 趋势强度 ─────────────────────────────────────────────────────────
    adx_14, adx_label = _compute_adx(high, low, close, 14)

    # ── 52周高低点位置 ───────────────────────────────────────────────────────
    week52_high, week52_low, week52_pos, week52_label = _compute_52week_position(close)

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
        f"量比={volume_ratio:.2f} {volume_ratio_label}" if volume_ratio else "",
        f"ADX(14)={adx_14:.1f} {adx_label}" if adx_14 else "",
        f"52周位置={week52_pos:.1f}% {week52_label}" if week52_pos is not None else "",
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
        "volume_ratio": round(volume_ratio, 2) if volume_ratio else None,
        "volume_ratio_label": volume_ratio_label,
        "adx_14": round(adx_14, 1) if adx_14 else None,
        "adx_label": adx_label,
        "week52_high": round(week52_high, 2) if week52_high else None,
        "week52_low": round(week52_low, 2) if week52_low else None,
        "week52_pos": round(week52_pos, 1) if week52_pos is not None else None,
        "week52_label": week52_label,
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
    last_loss = float(avg_loss.iloc[-1])
    if last_loss == 0:
        return 100.0  # 标准 RSI 定义：无下跌时 RSI=100
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    val = float(rsi.iloc[-1])
    return val if not (np.isnan(val) or np.isinf(val)) else 50.0


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
# 量比（当日量 / 近5日均量）
# ══════════════════════════════════════════════════════════════════════════════

def _compute_volume_ratio(volume: pd.Series) -> tuple[float | None, str]:
    """量比 = 当日成交量 / 近5日日均量（不含当日）"""
    if len(volume) < 6:
        return None, "数据不足"
    avg5 = volume.iloc[-6:-1].mean()
    if avg5 <= 0:
        return None, "数据异常"
    ratio = float(volume.iloc[-1]) / float(avg5)
    if ratio >= 3.0:
        label = "天量(≥3倍)"
    elif ratio >= 2.0:
        label = "放量(≥2倍)"
    elif ratio >= 1.5:
        label = "温和放量"
    elif ratio >= 0.8:
        label = "正常"
    elif ratio >= 0.5:
        label = "缩量"
    else:
        label = "地量(<0.5倍)"
    return ratio, label


# ══════════════════════════════════════════════════════════════════════════════
# ADX 趋势强度 (14)
# ══════════════════════════════════════════════════════════════════════════════

def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series,
                 period: int = 14) -> tuple[float | None, str]:
    """ADX = 趋势强度指标，不判断方向，只判断趋势是否成立。
    ADX < 20: 震荡无趋势  20-25: 趋势初现  >25: 趋势成立  >40: 强趋势
    """
    if len(close) < period * 2 + 1:
        return None, "数据不足"

    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    # True Range
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    dm_plus = np.where((high - prev_high) > (prev_low - low),
                       np.maximum(high - prev_high, 0), 0)
    dm_minus = np.where((prev_low - low) > (high - prev_high),
                        np.maximum(prev_low - low, 0), 0)

    dm_plus_s = pd.Series(dm_plus, index=close.index)
    dm_minus_s = pd.Series(dm_minus, index=close.index)

    # Wilder 平滑
    atr_w = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    di_plus = 100 * dm_plus_s.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr_w.replace(0, np.nan)
    di_minus = 100 * dm_minus_s.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr_w.replace(0, np.nan)

    dx = (100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)).fillna(0)
    adx = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    val = float(adx.iloc[-1])
    if val >= 40:
        label = "强趋势"
    elif val >= 25:
        label = "趋势成立"
    elif val >= 20:
        label = "趋势初现"
    else:
        label = "震荡无趋势"
    return val, label


# ══════════════════════════════════════════════════════════════════════════════
# 52周高低点位置
# ══════════════════════════════════════════════════════════════════════════════

def _compute_52week_position(close: pd.Series) -> tuple[float | None, float | None, float | None, str]:
    """计算当前价在52周（约250交易日）高低点区间内的位置百分比。
    返回 (52周高点, 52周低点, 位置%, 标签)
    位置% = (当前价 - 52周低) / (52周高 - 52周低) * 100
    """
    n = min(250, len(close))
    if n < 20:
        return None, None, None, "数据不足"

    window = close.iloc[-n:]
    high52 = float(window.max())
    low52 = float(window.min())
    current = float(close.iloc[-1])

    rng = high52 - low52
    if rng <= 0:
        return high52, low52, 100.0, "价格无波动"

    pos = (current - low52) / rng * 100

    if pos >= 90:
        label = "逼近52周高点(高位风险区)"
    elif pos >= 70:
        label = "52周高位区间"
    elif pos >= 40:
        label = "52周中位区间"
    elif pos >= 20:
        label = "52周低位区间"
    else:
        label = "逼近52周低点(低位机会区)"
    return high52, low52, pos, label


# ══════════════════════════════════════════════════════════════════════════════
# 格式化输出
# ══════════════════════════════════════════════════════════════════════════════

def format_indicators_section(indicators: dict) -> str:
    """将指标字典格式化为 prompt 中的技术指标段落"""
    if not indicators or indicators.get("rsi_14") is None:
        return "（技术指标数据不足，无法计算）"

    def _s(val, fmt="{}", default="N/A"):
        """安全格式化：None/NaN → default"""
        if val is None:
            return default
        try:
            import math
            if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                return default
        except (TypeError, ValueError):
            pass
        try:
            return fmt.format(val)
        except (TypeError, ValueError):
            return default

    lines = [
        "## 技术指标",
        indicators.get('summary', ''),
        "",
        f"RSI(14): {_s(indicators.get('rsi_14'))}  信号: {_s(indicators.get('rsi_signal'))}",
        f"MACD: DIF={_s(indicators.get('macd_dif'))}  DEA={_s(indicators.get('macd_dea'))}  "
        f"柱状={_s(indicators.get('macd_hist'))}  信号: {_s(indicators.get('macd_signal'))}  "
        f"动能: {_s(indicators.get('macd_hist_trend'))}",
        f"布林带(20,2): 上轨={_s(indicators.get('bb_upper'))}  中轨={_s(indicators.get('bb_middle'))}  "
        f"下轨={_s(indicators.get('bb_lower'))}  带宽={_s(indicators.get('bb_width_pct'))}%  "
        f"位置: {_s(indicators.get('bb_position'))}"
        f"{'  *** 布林挤压(变盘前兆!) ***' if indicators.get('bb_squeeze') else ''}",
        f"OBV趋势: {_s(indicators.get('obv_trend'))}",
    ]

    atr_14 = indicators.get("atr_14")
    atr_stop = indicators.get("atr_stop")
    bb_middle = indicators.get("bb_middle")
    if atr_14:
        if bb_middle and bb_middle > 0:
            pct = abs(atr_14 * 2 / bb_middle * 100)
            lines.append(f"ATR(14): {_s(atr_14)}  2ATR止损位: {_s(atr_stop)}元  (距现价{pct:.1f}%)")
        else:
            lines.append(f"ATR(14): {_s(atr_14)}  2ATR止损位: {_s(atr_stop)}元")

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

    if indicators.get("volume_ratio") is not None:
        lines.append(
            f"量比: {indicators['volume_ratio']}  "
            f"信号: {indicators['volume_ratio_label']}"
        )

    if indicators.get("adx_14") is not None:
        lines.append(
            f"ADX(14): {indicators['adx_14']}  "
            f"趋势强度: {indicators['adx_label']}"
        )

    if indicators.get("week52_pos") is not None:
        lines.append(
            f"52周位置: {indicators['week52_pos']}%  "
            f"（高点{indicators['week52_high']} / 低点{indicators['week52_low']}）  "
            f"状态: {indicators['week52_label']}"
        )

    return "\n".join(lines)
