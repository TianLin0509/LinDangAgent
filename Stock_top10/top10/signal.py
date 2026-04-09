"""量化预评分 — 四维信号计算（技术面、资金面、基本面、动量）"""

import pandas as pd
import numpy as np


def compute_technicals(df: pd.DataFrame) -> dict:
    if df.empty or len(df) < 20:
        return {}

    close = df["收盘"].values.astype(float)
    high = df["最高"].values.astype(float)
    low = df["最低"].values.astype(float)
    vol = df["成交量"].values.astype(float)
    n = len(close)
    result = {}

    # ── 均线系统 ──
    for p in [5, 10, 20, 60]:
        if n >= p:
            result[f"MA{p}"] = round(float(pd.Series(close).rolling(p).mean().iloc[-1]), 2)

    ma5 = result.get("MA5")
    ma20 = result.get("MA20")
    ma60 = result.get("MA60")
    if ma5 and ma20 and ma60:
        if ma5 > ma20 > ma60:
            result["均线状态"] = "多头排列"
        elif ma5 < ma20 < ma60:
            result["均线状态"] = "空头排列"
        else:
            result["均线状态"] = "均线纠缠"

    # ── RSI(14) ──
    if n >= 15:
        delta = pd.Series(close).diff()
        gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
        loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
        if loss > 0:
            rsi = round(100 - 100 / (1 + gain / loss), 1)
        else:
            rsi = 100.0
        result["RSI14"] = rsi
        if rsi >= 80:
            result["RSI信号"] = "超买"
        elif rsi <= 20:
            result["RSI信号"] = "超卖"
        elif rsi >= 70:
            result["RSI信号"] = "偏强"
        elif rsi <= 30:
            result["RSI信号"] = "偏弱"
        else:
            result["RSI信号"] = "中性"

    # ── MACD (12,26,9) ──
    if n >= 35:
        s = pd.Series(close)
        ema12 = s.ewm(span=12).mean()
        ema26 = s.ewm(span=26).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9).mean()
        macd_bar = (dif - dea) * 2
        result["MACD_DIF"] = round(float(dif.iloc[-1]), 3)
        result["MACD_DEA"] = round(float(dea.iloc[-1]), 3)
        result["MACD柱"] = round(float(macd_bar.iloc[-1]), 3)
        if len(dif) >= 2:
            if dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2]:
                result["MACD信号"] = "金叉"
            elif dif.iloc[-1] < dea.iloc[-1] and dif.iloc[-2] >= dea.iloc[-2]:
                result["MACD信号"] = "死叉"
            elif dif.iloc[-1] > dea.iloc[-1]:
                result["MACD信号"] = "多头"
            else:
                result["MACD信号"] = "空头"

    # ── 布林带 (20,2) ──
    if n >= 20:
        ma20_val = pd.Series(close).rolling(20).mean().iloc[-1]
        std20 = pd.Series(close).rolling(20).std().iloc[-1]
        upper = ma20_val + 2 * std20
        lower = ma20_val - 2 * std20
        result["布林上轨"] = round(float(upper), 2)
        result["布林中轨"] = round(float(ma20_val), 2)
        result["布林下轨"] = round(float(lower), 2)
        boll_width = upper - lower
        if boll_width > 0:
            pos = (close[-1] - lower) / boll_width * 100
            result["布林位置"] = round(float(pos), 1)

    # ── 成交量分析 ──
    if n >= 20:
        vol_5 = float(np.mean(vol[-5:]))
        vol_20 = float(np.mean(vol[-20:]))
        result["5日均量"] = round(vol_5)
        result["20日均量"] = round(vol_20)
        if vol_20 > 0:
            ratio = vol_5 / vol_20
            result["量能比"] = round(ratio, 2)
            if ratio > 1.5:
                result["量能状态"] = "显著放量"
            elif ratio > 1.2:
                result["量能状态"] = "温和放量"
            elif ratio < 0.7:
                result["量能状态"] = "明显缩量"
            else:
                result["量能状态"] = "量能平稳"

    # ── 支撑/压力位 ──
    if n >= 20:
        recent_20_high = float(np.max(high[-20:]))
        recent_20_low = float(np.min(low[-20:]))
        result["20日最高"] = round(recent_20_high, 2)
        result["20日最低"] = round(recent_20_low, 2)
        if n >= 60:
            result["60日最高"] = round(float(np.max(high[-60:])), 2)
            result["60日最低"] = round(float(np.min(low[-60:])), 2)

        if recent_20_high > 0:
            dist_high = (close[-1] - recent_20_high) / recent_20_high * 100
            result["距20日高点"] = f"{dist_high:+.1f}%"
            if dist_high >= -1:
                result["价格位置"] = "创近期新高"
            elif dist_high >= -5:
                result["价格位置"] = "接近高位"
            elif dist_high <= -15:
                result["价格位置"] = "远离高位"

    # ── 涨跌幅统计 ──
    for days in [3, 5, 10, 20]:
        if n > days:
            chg = (close[-1] / close[-days - 1] - 1) * 100
            result[f"近{days}日涨幅"] = round(chg, 2)

    return result


def compute_quant_score(technicals: dict, pe: float = None,
                        pb: float = None, net_flow_wan: float = None,
                        volume_ratio: float = None,
                        turnover_rate: float = None,
                        industry_pe: float = None,
                        industry_pb: float = None,
                        total_mv_yi: float = None) -> dict:
    """四维量化预评分（v3.0：行业相对估值+否决机制+加速赶顶检测+小盘软惩罚）"""
    tech_score = 50
    capital_score = 50
    valuation_score = 50
    momentum_score = 50

    # ── 技术面分（含否决机制）──
    ma_state = technicals.get("均线状态", "")
    macd_sig = technicals.get("MACD信号", "")
    vol_state = technicals.get("量能状态", "")

    if ma_state == "多头排列":
        tech_score += 15
    elif ma_state == "空头排列":
        tech_score -= 15

    rsi = technicals.get("RSI14")
    if rsi is not None:
        if 50 <= rsi <= 70:
            tech_score += 5
        elif rsi > 80:
            tech_score -= 5
        elif rsi < 30:
            tech_score -= 3

    if macd_sig == "金叉":
        tech_score += 10
    elif macd_sig == "多头":
        tech_score += 5
    elif macd_sig == "死叉":
        tech_score -= 10
    elif macd_sig == "空头":
        tech_score -= 5

    boll_pos = technicals.get("布林位置")
    if boll_pos is not None:
        if 60 <= boll_pos <= 85:
            tech_score += 5
        elif boll_pos > 95:
            tech_score -= 5
        elif boll_pos < 10:
            tech_score -= 3

    if vol_state == "显著放量":
        tech_score += 8
    elif vol_state == "温和放量":
        tech_score += 4
    elif vol_state == "明显缩量":
        tech_score -= 5

    price_pos = technicals.get("价格位置", "")
    if price_pos == "创近期新高":
        tech_score += 8
    elif price_pos == "远离高位":
        tech_score -= 5

    # ★ 否决机制：空头排列+MACD死叉/空头 → 锁死上限40
    if ma_state == "空头排列" and macd_sig in ("死叉", "空头"):
        tech_score = min(tech_score, 40)
    # ★ 否决机制：空头排列+缩量 → 锁死上限45
    if ma_state == "空头排列" and vol_state == "明显缩量":
        tech_score = min(tech_score, 45)

    # ── 资金面分 ──
    if net_flow_wan is not None:
        if net_flow_wan > 5000:
            capital_score += 15
        elif net_flow_wan > 1000:
            capital_score += 10
        elif net_flow_wan > 0:
            capital_score += 5
        elif net_flow_wan < -5000:
            capital_score -= 15
        elif net_flow_wan < -1000:
            capital_score -= 10
        elif net_flow_wan < 0:
            capital_score -= 5

    if volume_ratio is not None:
        if volume_ratio > 2.0:
            capital_score += 10
        elif volume_ratio > 1.5:
            capital_score += 6
        elif volume_ratio > 1.0:
            capital_score += 3
        elif volume_ratio < 0.5:
            capital_score -= 5

    if turnover_rate is not None:
        if 3 <= turnover_rate <= 15:
            capital_score += 5
        elif turnover_rate > 25:
            capital_score -= 5

    # ── 估值面分（v3.0：行业相对值优先，绝对值兜底）──
    _pe_scored = False
    if pe is not None and pe > 0 and industry_pe is not None and industry_pe > 0:
        # 相对行业中位数的偏离度打分
        pe_ratio = pe / industry_pe
        if pe_ratio < 0.5:
            valuation_score += 15   # 远低于行业
        elif pe_ratio < 0.8:
            valuation_score += 8    # 低于行业
        elif pe_ratio < 1.2:
            valuation_score += 3    # 行业中位附近
        elif pe_ratio < 2.0:
            valuation_score -= 5    # 高于行业
        else:
            valuation_score -= 12   # 远高于行业
        _pe_scored = True

    if not _pe_scored and pe is not None and pe > 0:
        # 绝对值兜底（无行业基准时）
        if pe < 15:
            valuation_score += 15
        elif pe < 25:
            valuation_score += 8
        elif pe < 40:
            valuation_score += 0
        elif pe < 80:
            valuation_score -= 8
        else:
            valuation_score -= 15

    _pb_scored = False
    if pb is not None and pb > 0 and industry_pb is not None and industry_pb > 0:
        pb_ratio = pb / industry_pb
        if pb_ratio < 0.6:
            valuation_score += 8
        elif pb_ratio < 1.0:
            valuation_score += 3
        elif pb_ratio > 2.5:
            valuation_score -= 8
        elif pb_ratio > 1.5:
            valuation_score -= 3
        _pb_scored = True

    if not _pb_scored and pb is not None and pb > 0:
        if pb < 1.5:
            valuation_score += 8
        elif pb < 3:
            valuation_score += 3
        elif pb > 8:
            valuation_score -= 8
        elif pb > 5:
            valuation_score -= 3

    # ★ 小盘股软惩罚（替代硬过滤）
    if total_mv_yi is not None and total_mv_yi > 0:
        if total_mv_yi < 20:
            valuation_score -= 10   # 极小盘，波动风险高
        elif total_mv_yi < 30:
            valuation_score -= 5    # 偏小盘

    # ── 动量分（v3.0：增加加速赶顶检测）──
    chg_3 = technicals.get("近3日涨幅")
    chg_5 = technicals.get("近5日涨幅")
    chg_10 = technicals.get("近10日涨幅")
    chg_20 = technicals.get("近20日涨幅")

    if chg_3 is not None:
        if 2 <= chg_3 <= 15:
            momentum_score += 8
        elif chg_3 > 20:
            momentum_score -= 5

    if chg_5 is not None:
        if 3 <= chg_5 <= 20:
            momentum_score += 5
        elif chg_5 < -10:
            momentum_score -= 8

    if chg_20 is not None:
        if 5 <= chg_20 <= 30:
            momentum_score += 8
        elif chg_20 > 40:
            momentum_score -= 5
        elif chg_20 < -15:
            momentum_score -= 10

    # ★ 加速赶顶检测：近5日涨幅占近20日涨幅 >60%，说明末段加速
    if chg_5 is not None and chg_20 is not None and chg_20 > 10:
        if chg_5 / chg_20 > 0.6:
            momentum_score -= 8  # 涨幅集中在最近几天，赶顶风险

    vol_ratio = technicals.get("量能比")
    if vol_ratio and chg_5 is not None:
        if vol_ratio > 1.2 and chg_5 > 0:
            momentum_score += 8
        elif vol_ratio < 0.8 and chg_5 > 5:
            momentum_score -= 3

    tech_score = max(0, min(100, tech_score))
    capital_score = max(0, min(100, capital_score))
    valuation_score = max(0, min(100, valuation_score))
    momentum_score = max(0, min(100, momentum_score))

    avg = round((tech_score + capital_score + valuation_score + momentum_score) / 4)

    if all(s >= 65 for s in [tech_score, capital_score, valuation_score, momentum_score]):
        signal = "四维共振"
    elif avg >= 70:
        signal = "综合偏强"
    elif avg >= 55:
        signal = "中性偏多"
    elif avg >= 40:
        signal = "偏弱观望"
    else:
        signal = "条件不足"

    return {
        "技术面分": tech_score,
        "资金面分": capital_score,
        "估值面分": valuation_score,
        "动量分": momentum_score,
        "量化总分": avg,
        "量化信号": signal,
    }


def detect_kline_pattern(technicals: dict) -> str:
    """识别K线形态标签，供Scout prompt注入"""
    patterns = []
    ma_state = technicals.get("均线状态", "")
    macd_sig = technicals.get("MACD信号", "")
    vol_state = technicals.get("量能状态", "")
    price_pos = technicals.get("价格位置", "")
    chg_3 = technicals.get("近3日涨幅", 0) or 0
    chg_5 = technicals.get("近5日涨幅", 0) or 0
    chg_20 = technicals.get("近20日涨幅", 0) or 0
    rsi = technicals.get("RSI14", 50)
    boll_pos = technicals.get("布林位置", 50)

    # 放量突破
    if vol_state in ("显著放量", "温和放量") and price_pos == "创近期新高" and macd_sig in ("金叉", "多头"):
        patterns.append("放量突破新高")
    # 缩量回踩
    elif vol_state == "明显缩量" and ma_state == "多头排列" and -5 < chg_5 < 0:
        patterns.append("缩量回踩均线")
    # 底部反转
    elif ma_state != "多头排列" and macd_sig == "金叉" and boll_pos < 30:
        patterns.append("底部金叉反转")
    # 高位滞涨
    elif vol_state in ("显著放量", "温和放量") and abs(chg_3) < 1 and chg_20 > 20:
        patterns.append("高位放量滞涨")
    # 加速赶顶
    elif chg_5 > 15 and chg_20 > 25:
        patterns.append("加速赶顶")
    # 弱势反弹
    elif ma_state == "空头排列" and chg_3 > 2:
        patterns.append("弱势反弹")
    # 均线金叉
    elif ma_state == "多头排列" and macd_sig in ("金叉", "多头"):
        patterns.append("均线多头+MACD共振")

    return "、".join(patterns) if patterns else "无明显形态"


def check_volume_price_divergence(technicals: dict) -> bool:
    """检测量价背离：近3日放量但价格重心下移 → 应剔除"""
    vol_state = technicals.get("量能状态", "")
    chg_3 = technicals.get("近3日涨幅")
    if vol_state in ("显著放量", "温和放量") and chg_3 is not None and chg_3 < -2:
        return True  # 放量下跌 = 量价背离
    return False


def format_technicals_text(technicals: dict) -> str:
    if not technicals:
        return ""

    lines = []
    ma_state = technicals.get("均线状态", "")
    ma_parts = []
    for k in ["MA5", "MA10", "MA20", "MA60"]:
        if k in technicals:
            ma_parts.append(f"{k}={technicals[k]}")
    if ma_parts:
        lines.append(f"均线: {', '.join(ma_parts)} → {ma_state}")

    if "RSI14" in technicals:
        lines.append(f"RSI(14): {technicals['RSI14']} ({technicals.get('RSI信号', '')})")

    if "MACD_DIF" in technicals:
        lines.append(f"MACD: DIF={technicals['MACD_DIF']}, DEA={technicals['MACD_DEA']}, "
                      f"柱={technicals['MACD柱']} → {technicals.get('MACD信号', '')}")

    if "布林上轨" in technicals:
        lines.append(f"布林带: 上={technicals['布林上轨']}, 中={technicals['布林中轨']}, "
                      f"下={technicals['布林下轨']}, 位置={technicals.get('布林位置', '')}%")

    if "量能比" in technicals:
        lines.append(f"量能: 5日/20日均量比={technicals['量能比']} → {technicals.get('量能状态', '')}")

    if "20日最高" in technicals:
        pos_info = f"20日区间: {technicals['20日最低']}~{technicals['20日最高']}"
        if "距20日高点" in technicals:
            pos_info += f", 距高点{technicals['距20日高点']}"
        if "价格位置" in technicals:
            pos_info += f" ({technicals['价格位置']})"
        lines.append(pos_info)
    if "60日最高" in technicals:
        lines.append(f"60日区间: {technicals['60日最低']}~{technicals['60日最高']}")

    chg_parts = []
    for d in [3, 5, 10, 20]:
        k = f"近{d}日涨幅"
        if k in technicals:
            chg_parts.append(f"{d}日:{technicals[k]:+.2f}%")
    if chg_parts:
        lines.append(f"涨幅: {', '.join(chg_parts)}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# v3.0 新增：RPS 相对强度 + 市场环境动态调参
# ══════════════════════════════════════════════════════════════════════════════

def compute_rps(chg_20: float, all_market_chg_20: list) -> int:
    """计算个股20日涨幅在全市场中的百分位排名 (0-100)

    RPS=90 表示跑赢了90%的股票。
    all_market_chg_20: 全A的20日涨幅列表（由调用方传入）
    """
    if not all_market_chg_20 or chg_20 is None:
        return 50  # 默认中位
    below = sum(1 for x in all_market_chg_20 if x < chg_20)
    return round(below / len(all_market_chg_20) * 100)


def adjust_scout_threshold(market_sentiment: str = "中性") -> int:
    """根据市场环境动态调整 Scout 截断门槛

    market_sentiment: "强势" / "中性" / "弱势" / "极弱"
    返回：Scout 分数门槛（低于此分的不进入 Top20）
    """
    thresholds = {
        "强势": 55,   # 牛市放宽，给更多机会
        "中性": 60,   # 默认
        "弱势": 68,   # 熊市收紧，只留最强
        "极弱": 75,   # 极端行情只留确定性最高的
    }
    return thresholds.get(market_sentiment, 60)


def detect_market_sentiment(index_chg_pct: float = None,
                            advance_ratio: float = None) -> str:
    """根据大盘涨跌幅和涨跌比判断市场情绪

    index_chg_pct: 上证指数当日涨跌幅
    advance_ratio: 上涨家数 / 总家数（0-1）
    """
    if index_chg_pct is None:
        return "中性"

    if index_chg_pct > 1.5 or (advance_ratio and advance_ratio > 0.7):
        return "强势"
    elif index_chg_pct > 0 or (advance_ratio and advance_ratio > 0.5):
        return "中性"
    elif index_chg_pct > -1.5:
        return "弱势"
    else:
        return "极弱"
