# -*- coding: utf-8 -*-
"""轻量技术指标回测 — 量化锚点

给定当前技术指标组合，从个股历史K线中回溯同类信号出现的次数和后续收益，
为将领C（邓华·技术面）提供量化验证锚定。

设计原则：
  - 零外部依赖（仅用已有的 tushare_client + indicators）
  - 单股历史自回测（不跨股），避免幸存者偏差
  - 输出一行摘要，直接注入prompt
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def backtest_current_signals(
    ts_code: str,
    current_indicators: dict,
    lookback_days: int = 750,  # 约3年
    forward_days: tuple[int, ...] = (5, 10, 20),
) -> dict | None:
    """回测当前技术指标组合的历史表现。

    Args:
        ts_code: 股票代码（如 000001.SZ）
        current_indicators: compute_indicators() 的返回值
        lookback_days: 回溯天数
        forward_days: 检验的未来天数窗口

    Returns:
        {
            "signals": ["RSI超卖", "MACD金叉"],
            "match_count": 12,
            "results": {
                5:  {"avg_return": 2.1, "win_rate": 66.7, "median_return": 1.5},
                10: {"avg_return": 3.5, "win_rate": 58.3, "median_return": 2.8},
                20: {"avg_return": 5.2, "win_rate": 50.0, "median_return": 3.1},
            },
            "summary": "该指标组合历史出现12次，10日胜率58%，均收益+3.5%",
        }
        如果数据不足或无信号匹配，返回 None。
    """
    if not current_indicators or current_indicators.get("rsi_14") is None:
        return None

    # 获取历史K线
    try:
        from data.tushare_client import get_price_df
        df, err = get_price_df(ts_code, days=lookback_days)
        if err or df is None or len(df) < 120:
            return None
    except Exception as exc:
        logger.debug("[backtest_lite] get_price_df failed: %r", exc)
        return None

    # 确保按日期正序排列
    df = df.sort_values("trade_date").reset_index(drop=True)
    close = df["close"]

    # 识别当前活跃的信号组合
    active_signals = _identify_active_signals(current_indicators)
    if not active_signals:
        return None

    # 在历史中扫描同类信号出现的日期
    signal_dates = _scan_historical_signals(df, active_signals)
    if len(signal_dates) < 3:  # 样本太少无统计意义
        return None

    # 计算每个信号日期后的收益
    results = {}
    max_fwd = max(forward_days)

    for fwd in forward_days:
        returns = []
        for idx in signal_dates:
            if idx + fwd < len(df):
                entry_price = close.iloc[idx]
                exit_price = close.iloc[idx + fwd]
                ret = (exit_price - entry_price) / entry_price * 100
                returns.append(ret)

        if returns:
            arr = np.array(returns)
            results[fwd] = {
                "avg_return": round(float(np.mean(arr)), 1),
                "win_rate": round(float(np.sum(arr > 0) / len(arr) * 100), 1),
                "median_return": round(float(np.median(arr)), 1),
                "sample_count": len(returns),
            }

    if not results:
        return None

    # 生成摘要（以10日为主）
    ref = results.get(10, results.get(5, next(iter(results.values()))))
    ref_days = 10 if 10 in results else (5 if 5 in results else list(results.keys())[0])
    signal_names = [s["name"] for s in active_signals]
    summary = (
        f"该指标组合({'+'.join(signal_names)})历史出现{ref['sample_count']}次，"
        f"{ref_days}日胜率{ref['win_rate']:.0f}%，均收益{ref['avg_return']:+.1f}%"
    )

    return {
        "signals": signal_names,
        "match_count": len(signal_dates),
        "results": results,
        "summary": summary,
    }


def _identify_active_signals(indicators: dict) -> list[dict]:
    """从当前指标中识别活跃的技术信号。

    每个信号返回 {"name": 展示名, "check_fn": 历史检查函数名}
    """
    signals = []

    rsi = indicators.get("rsi_14")
    if rsi is not None:
        if rsi < 30:
            signals.append({"name": "RSI超卖", "type": "rsi_oversold"})
        elif rsi > 70:
            signals.append({"name": "RSI超买", "type": "rsi_overbought"})

    macd_signal = indicators.get("macd_signal", "")
    if "金叉" in str(macd_signal):
        signals.append({"name": "MACD金叉", "type": "macd_golden"})
    elif "死叉" in str(macd_signal):
        signals.append({"name": "MACD死叉", "type": "macd_death"})

    ma_score = indicators.get("ma_score")
    if ma_score is not None:
        if ma_score >= 4:
            signals.append({"name": "均线多头", "type": "ma_bullish"})
        elif ma_score <= 1:
            signals.append({"name": "均线空头", "type": "ma_bearish"})

    bb_pos = indicators.get("bollinger_position", "")
    if "下轨" in str(bb_pos) or indicators.get("bb_pct", 1) < 0.05:
        signals.append({"name": "布林下轨", "type": "bb_lower"})
    elif "上轨" in str(bb_pos) or indicators.get("bb_pct", 0) > 0.95:
        signals.append({"name": "布林上轨", "type": "bb_upper"})

    squeeze = indicators.get("squeeze")
    if squeeze:
        signals.append({"name": "布林挤压", "type": "bb_squeeze"})

    kdj_signal = indicators.get("kdj_signal", "")
    if "超卖" in str(kdj_signal):
        signals.append({"name": "KDJ超卖", "type": "kdj_oversold"})
    elif "超买" in str(kdj_signal):
        signals.append({"name": "KDJ超买", "type": "kdj_overbought"})

    # 最多保留3个最强信号（避免组合过于严格导致0匹配）
    return signals[:3]


def _scan_historical_signals(df: pd.DataFrame, active_signals: list[dict]) -> list[int]:
    """在历史K线中扫描满足信号组合的日期索引。

    使用宽松匹配：满足至少 ceil(n/2) 个信号即视为匹配（n为信号数）。
    """
    close = df["close"].values
    n = len(df)

    # 预计算历史指标序列
    rsi_series = _compute_rsi_series(close, 14) if any(s["type"].startswith("rsi") for s in active_signals) else None
    macd_dif, macd_dea = _compute_macd_series(close) if any(s["type"].startswith("macd") for s in active_signals) else (None, None)
    ma_scores = _compute_ma_score_series(close) if any(s["type"].startswith("ma_") for s in active_signals) else None
    bb_pct = _compute_bb_pct_series(close) if any(s["type"].startswith("bb_") for s in active_signals) else None

    min_match = max(1, (len(active_signals) + 1) // 2)  # ceil(n/2)
    matched_indices = []

    # 从第60天开始（确保指标有效）到倒数第5天（至少有5天的未来数据）
    for i in range(60, n - 5):
        match_count = 0
        for sig in active_signals:
            if _check_signal_at(sig["type"], i, rsi_series, macd_dif, macd_dea, ma_scores, bb_pct):
                match_count += 1

        if match_count >= min_match:
            # 避免连续日期重复计数（至少间隔5天）
            if not matched_indices or i - matched_indices[-1] >= 5:
                matched_indices.append(i)

    return matched_indices


def _check_signal_at(
    sig_type: str, idx: int,
    rsi: np.ndarray | None,
    macd_dif: np.ndarray | None, macd_dea: np.ndarray | None,
    ma_scores: np.ndarray | None,
    bb_pct: np.ndarray | None,
) -> bool:
    """检查某个信号在历史某天是否触发。"""
    if sig_type == "rsi_oversold" and rsi is not None:
        return rsi[idx] < 30
    if sig_type == "rsi_overbought" and rsi is not None:
        return rsi[idx] > 70
    if sig_type == "macd_golden" and macd_dif is not None and macd_dea is not None:
        return idx > 0 and macd_dif[idx] > macd_dea[idx] and macd_dif[idx - 1] <= macd_dea[idx - 1]
    if sig_type == "macd_death" and macd_dif is not None and macd_dea is not None:
        return idx > 0 and macd_dif[idx] < macd_dea[idx] and macd_dif[idx - 1] >= macd_dea[idx - 1]
    if sig_type == "ma_bullish" and ma_scores is not None:
        return ma_scores[idx] >= 4
    if sig_type == "ma_bearish" and ma_scores is not None:
        return ma_scores[idx] <= 1
    if sig_type == "bb_lower" and bb_pct is not None:
        return bb_pct[idx] < 0.05
    if sig_type == "bb_upper" and bb_pct is not None:
        return bb_pct[idx] > 0.95
    if sig_type == "bb_squeeze" and bb_pct is not None:
        # 布林带宽度收窄
        return bb_pct[idx] >= 0.4 and bb_pct[idx] <= 0.6  # 接近中轨且带宽窄
    if sig_type == "kdj_oversold" and rsi is not None:
        # 简化：用RSI<25近似KDJ超卖
        return rsi[idx] < 25
    if sig_type == "kdj_overbought" and rsi is not None:
        return rsi[idx] > 75
    return False


# ══════════════════════════════════════════════════════════════════
# 辅助：批量计算历史指标序列
# ══════════════════════════════════════════════════════════════════

def _compute_rsi_series(close: np.ndarray, period: int = 14) -> np.ndarray:
    """计算RSI序列。"""
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0).astype(float)
    loss = np.where(delta < 0, -delta, 0).astype(float)

    avg_gain = np.zeros_like(close, dtype=float)
    avg_loss = np.zeros_like(close, dtype=float)

    avg_gain[period] = np.mean(gain[1:period + 1])
    avg_loss[period] = np.mean(loss[1:period + 1])

    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period

    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi[:period] = 50.0  # 前period天填充中性值
    return rsi


def _compute_macd_series(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    """计算MACD DIF和DEA序列。"""
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    dif = ema_fast - ema_slow
    dea = _ema(dif, signal)
    return dif, dea


def _compute_ma_score_series(close: np.ndarray) -> np.ndarray:
    """计算均线排列评分序列（0-5）。"""
    periods = [5, 10, 20, 60, 120]
    mas = {}
    for p in periods:
        if len(close) >= p:
            ma = np.convolve(close, np.ones(p) / p, mode='full')[:len(close)]
            ma[:p - 1] = close[:p - 1]  # 填充
            mas[p] = ma

    scores = np.zeros(len(close), dtype=int)
    for i in range(max(periods), len(close)):
        s = 0
        if 5 in mas and 10 in mas and mas[5][i] > mas[10][i]:
            s += 1
        if 10 in mas and 20 in mas and mas[10][i] > mas[20][i]:
            s += 1
        if 20 in mas and 60 in mas and mas[20][i] > mas[60][i]:
            s += 1
        if 60 in mas and 120 in mas and mas[60][i] > mas[120][i]:
            s += 1
        if close[i] > mas.get(20, np.array([close[i]]))[i]:
            s += 1
        scores[i] = s
    return scores


def _compute_bb_pct_series(close: np.ndarray, period: int = 20, std_dev: float = 2.0) -> np.ndarray:
    """计算布林%B序列。"""
    ma = np.convolve(close, np.ones(period) / period, mode='full')[:len(close)]
    ma[:period - 1] = close[:period - 1]

    std = np.zeros_like(close, dtype=float)
    for i in range(period - 1, len(close)):
        std[i] = np.std(close[i - period + 1:i + 1])

    upper = ma + std_dev * std
    lower = ma - std_dev * std
    width = upper - lower
    pct = np.where(width > 0, (close - lower) / width, 0.5)
    pct[:period - 1] = 0.5
    return pct


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """指数移动平均。"""
    alpha = 2.0 / (period + 1)
    result = np.zeros_like(data, dtype=float)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def format_backtest_for_prompt(result: dict | None) -> str:
    """格式化回测结果为prompt注入文本。"""
    if not result:
        return ""

    lines = [f"【量化回测锚点】信号组合: {'+'.join(result['signals'])}"]

    for fwd, stats in sorted(result["results"].items()):
        lines.append(
            f"  {fwd}日: 胜率{stats['win_rate']:.0f}%"
            f"({stats['sample_count']}样本)"
            f" 均收益{stats['avg_return']:+.1f}%"
            f" 中位数{stats['median_return']:+.1f}%"
        )

    lines.append(f"⚠️ 历史回测仅供参考，不代表未来表现")
    return "\n".join(lines)
