# -*- coding: utf-8 -*-
"""宏观情报模块 — 大盘趋势 + 外围市场 + 市场情绪

为四野指挥部提供"战略全局"视角：
1. A股主要指数趋势（上证/深证/创业板/科创50）
2. 市场温度（涨跌家数/涨停跌停/成交额变化）
3. 北向资金趋势（近5日累计）

数据源：Tushare（优先）→ akshare（备用）
外围市场（美股/港股等）由将领联网搜索获取，不在此采集。
"""

import logging
import threading
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

# 主要指数代码
INDICES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
}

_cache_lock = threading.Lock()
_macro_cache: dict | None = None
_macro_cache_date: str = ""
_formatted_full: str = ""   # 完整版缓存（林彪用）
_formatted_brief: str = ""  # 精简版缓存（将领用）


def collect_macro_intel() -> dict:
    """采集宏观情报，返回结构化数据。每小时刷新一次缓存。"""
    global _macro_cache, _macro_cache_date

    cache_key = datetime.now().strftime("%Y-%m-%d %H:00")
    with _cache_lock:
        if _macro_cache and _macro_cache_date == cache_key:
            return _macro_cache

    result = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "indices": {},
        "market_breadth": {},
        "northbound_trend": "",
    }

    # 1. 主要指数趋势
    result["indices"] = _collect_index_trends()

    # 2. 市场广度（涨跌家数）
    result["market_breadth"] = _collect_market_breadth()

    # 3. 北向资金趋势
    result["northbound_trend"] = _collect_northbound_trend()

    with _cache_lock:
        _macro_cache = result
        _macro_cache_date = cache_key

    return result


def _collect_index_trends() -> dict:
    """采集主要指数的趋势数据。"""
    from data.tushare_client import get_price_df

    index_data = {}
    for ts_code, name in INDICES.items():
        try:
            df, err = get_price_df(ts_code, days=60)
            if err or df is None or df.empty:
                continue

            closes = df["收盘"].astype(float).values
            if len(closes) < 20:
                continue

            # get_price_df 返回升序（最早在前，最新在末尾）
            latest = closes[-1]
            close_prev = closes[-2] if len(closes) > 1 else latest
            close_5d = closes[-5] if len(closes) >= 5 else closes[0]
            close_20d = closes[-20] if len(closes) >= 20 else closes[0]

            ret_1d = (latest - close_prev) / close_prev * 100 if close_prev > 0 else 0
            ret_5d = (latest - close_5d) / close_5d * 100 if close_5d > 0 else 0
            ret_20d = (latest - close_20d) / close_20d * 100 if close_20d > 0 else 0

            # 均线位置（最近N天）
            ma5 = closes[-5:].mean()
            ma20 = closes[-20:].mean()

            if latest > ma5 > ma20:
                trend = "多头排列"
            elif latest < ma5 < ma20:
                trend = "空头排列"
            elif latest > ma20:
                trend = "站上20日均线"
            else:
                trend = "跌破20日均线"

            index_data[name] = {
                "latest": round(float(latest), 2),
                "ret_1d": round(float(ret_1d), 2),
                "ret_5d": round(float(ret_5d), 2),
                "ret_20d": round(float(ret_20d), 2),
                "trend": trend,
            }
        except Exception as exc:
            logger.debug("[macro] index %s failed: %r", ts_code, exc)

    return index_data


def _collect_market_breadth() -> dict:
    """采集市场广度数据（涨跌家数等）。"""
    try:
        from data.tushare_client import get_pro
        pro = get_pro()
        today_str = datetime.now().strftime("%Y%m%d")

        # Tushare 路线
        if pro is not None:
            try:
                df = pro.stk_limit(trade_date=today_str)
                if df is not None and not df.empty:
                    up_limit = len(df[df["limit"] == "U"])
                    down_limit = len(df[df["limit"] == "D"])
                    return {
                        "涨停": up_limit,
                        "跌停": down_limit,
                        "trade_date": today_str,
                    }
            except Exception:
                pass

        # akshare 兜底：涨停跌停统计
        try:
            import akshare as ak
            df_up = ak.stock_zt_pool_em(date=today_str)
            df_down = ak.stock_zt_pool_dtgc_em(date=today_str)
            return {
                "涨停": len(df_up) if df_up is not None else 0,
                "跌停": len(df_down) if df_down is not None else 0,
                "trade_date": today_str,
            }
        except Exception:
            pass
    except Exception as exc:
        logger.debug("[macro] market_breadth failed: %r", exc)

    return {}


def _collect_northbound_trend() -> str:
    """采集北向资金近期趋势。"""
    try:
        from data.tushare_client import get_northbound_flow
        result = get_northbound_flow("000001.SH")
        if isinstance(result, tuple):
            return result[0] if result[0] else ""
        if isinstance(result, str):
            return result[:300]
    except Exception as exc:
        logger.debug("[macro] northbound failed: %r", exc)
    return ""


def format_macro_for_prompt(macro: dict) -> str:
    """将宏观情报格式化为 prompt 可注入的文本。"""
    if not macro:
        return ""

    lines = ["【宏观战局概览】"]

    # 指数趋势
    indices = macro.get("indices", {})
    if indices:
        lines.append("▎A股主要指数：")
        for name, data in indices.items():
            lines.append(
                f"  {name}: {data['latest']} "
                f"日涨跌{data['ret_1d']:+.2f}% "
                f"5日{data['ret_5d']:+.2f}% "
                f"20日{data['ret_20d']:+.2f}% "
                f"趋势:{data['trend']}"
            )

        # 综合判断
        trends = [d.get("trend", "") for d in indices.values()]
        bullish_count = sum(1 for t in trends if "多头" in t or "站上" in t)
        bearish_count = sum(1 for t in trends if "空头" in t or "跌破" in t)
        if bullish_count >= 3:
            lines.append("  → 大盘整体偏强，主要指数多头排列")
        elif bearish_count >= 3:
            lines.append("  → 大盘整体偏弱，注意系统性风险")
        else:
            lines.append("  → 大盘分化，注意结构性机会")

    # 市场广度
    breadth = macro.get("market_breadth", {})
    if breadth and breadth.get("涨停") is not None:
        up = breadth.get("涨停", 0)
        down = breadth.get("跌停", 0)
        if up > 0 or down > 0:
            lines.append(f"▎市场情绪：涨停{up}家 跌停{down}家")
            if up > 50:
                lines.append("  → 市场做多情绪亢奋，注意追高风险")
            elif down > 30:
                lines.append("  → 恐慌情绪蔓延，非极端低估不宜入场")

    # 北向资金
    nb = macro.get("northbound_trend", "")
    if nb:
        lines.append(f"▎北向资金：{nb[:200]}")

    lines.append("")
    lines.append("▎外围市场：请将领结合联网搜索获取美股/港股/大宗商品最新动态，交叉验证A股走势。")

    return "\n".join(lines)


def format_macro_brief(macro: dict) -> str:
    """精简版宏观情报（~3行），供将领prompt注入，节省token。

    将领只需要知道"大盘偏强/偏弱/分化"和关键数字，
    详细数据在林彪的小本本中才给完整版。
    """
    if not macro:
        return ""

    indices = macro.get("indices", {})
    if not indices:
        return ""

    # 综合趋势判断
    trends = [d.get("trend", "") for d in indices.values()]
    bullish_count = sum(1 for t in trends if "多头" in t or "站上" in t)
    bearish_count = sum(1 for t in trends if "空头" in t or "跌破" in t)

    if bullish_count >= 3:
        overall = "大盘整体偏强（主要指数多头排列）"
    elif bearish_count >= 3:
        overall = "大盘整体偏弱（注意系统性风险）"
    else:
        overall = "大盘分化（注意结构性机会）"

    # 上证作为代表
    sh = indices.get("上证指数", {})
    sh_str = f"上证{sh['latest']} 日{sh['ret_1d']:+.1f}% 5日{sh['ret_5d']:+.1f}%" if sh else ""

    # 情绪
    breadth = macro.get("market_breadth", {})
    emotion = ""
    if breadth.get("涨停") is not None:
        up, down = breadth.get("涨停", 0), breadth.get("跌停", 0)
        if up > 50:
            emotion = "，情绪亢奋(涨停过多)"
        elif down > 30:
            emotion = "，恐慌蔓延"
        elif up > 0 or down > 0:
            emotion = f"，涨停{up}/跌停{down}"

    lines = [
        f"【宏观速览】{overall}",
        f"  {sh_str}{emotion}",
        "  请联网搜索美股/港股最新动态，结合宏观环境分析该股。",
    ]
    return "\n".join(lines)


def get_macro_context() -> tuple[str, str]:
    """一次采集，返回 (完整版, 精简版)。

    完整版：给林彪的小本本（~500字，含4大指数详细数据+北向+情绪）
    精简版：给将领的prompt（~3行，只含结论+关键数字+搜索指令）

    全局当日缓存，多只股票共享，只采集一次。
    """
    global _formatted_full, _formatted_brief

    today = datetime.now().strftime("%Y-%m-%d")

    with _cache_lock:
        if _macro_cache_date == today and _formatted_full:
            return _formatted_full, _formatted_brief

    macro = collect_macro_intel()

    full = format_macro_for_prompt(macro)
    brief = format_macro_brief(macro)

    with _cache_lock:
        _formatted_full = full
        _formatted_brief = brief

    return full, brief
