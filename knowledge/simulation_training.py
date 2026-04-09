# -*- coding: utf-8 -*-
"""模拟训练 — AlphaGo 式自我对弈

用历史数据模拟分析 → 对比已知结果 → 生成教训 → 存入 case_memory。
一个晚上的模拟训练 = 几周的真实分析积累。

核心原则：
  - 时间锁定：只给 Claude 截止日期之前的数据，不泄露未来
  - 真实流程：和正式分析一样的 prompt 和评分体系
  - 即时判卷：我们已经知道未来走势，立刻评估对错
  - 教训入库：存入 case_memory，下次真实分析时可被 injector 注入

用法：
  python cli.py sim-train                  # 自动选题，训练 5 只
  python cli.py sim-train 10               # 训练 10 只
  python cli.py sim-train 5 semiconductor  # 针对半导体板块训练
"""

import json
import logging
import os
import random
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from knowledge.kb_config import BASE_DIR, DIRECTION_CN, KNOWLEDGE_DIR

logger = logging.getLogger(__name__)
SIM_LOG_FILE = KNOWLEDGE_DIR / "simulation_log.jsonl"

# 基准收益缓存（避免重复请求）
_market_return_cache: dict[str, float] = {}
_sector_return_cache: dict[str, float] = {}
_sector_name_cache: dict[str, str] = {}


def _calc_return_from_kline(df, exam_date: str) -> float | None:
    """从 K 线 DataFrame 计算 exam_date 起 10 个交易日的收益率。"""
    if df is None or len(df) < 10:
        return None
    exam_idx = None
    for i, row in df.iterrows():
        if str(row.get("trade_date", ""))[:8] <= exam_date:
            exam_idx = i
    if exam_idx is None:
        return None
    pos = df.index.get_loc(exam_idx)
    future_pos = min(pos + 10, len(df) - 1)
    exam_close = float(df.iloc[pos]["close"])
    future_close = float(df.iloc[future_pos]["close"])
    if exam_close <= 0:
        return None
    return round((future_close - exam_close) / exam_close * 100, 2)


def _get_market_return(exam_date: str) -> float:
    """获取上证指数从 exam_date 起 10 个交易日的收益率，作为大盘基准。"""
    if exam_date in _market_return_cache:
        return _market_return_cache[exam_date]
    try:
        df = _fetch_historical_kline("000001.SH", datetime.now().strftime("%Y%m%d"), days=60)
        ret = _calc_return_from_kline(df, exam_date)
        if ret is not None:
            _market_return_cache[exam_date] = ret
            return ret
    except Exception as exc:
        logger.debug("[sim] info fetch failed: %r", exc)
    return 0.0


def _get_sector_info(ts_code: str) -> str:
    """获取个股所属行业板块名称。"""
    if ts_code in _sector_name_cache:
        return _sector_name_cache[ts_code]

    sector = ""
    try:
        import akshare as ak
        code6 = ts_code.split(".")[0]
        df = ak.stock_individual_info_em(symbol=code6)
        if df is not None and not df.empty:
            info = dict(zip(df["item"], df["value"]))
            sector = info.get("行业", "")
    except Exception as exc:
        logger.debug("[sim] sector fetch failed: %r", exc)

    if not sector:
        # 从 stock_list.csv 尝试获取
        try:
            from data.tushare_client import load_stock_list
            sl, _ = load_stock_list()
            if sl is not None and not sl.empty:
                m = sl[sl["ts_code"] == ts_code]
                if not m.empty:
                    sector = str(m.iloc[0].get("industry", ""))
        except Exception as exc:
            logger.debug("[sim] market data sector failed: %r", exc)

    _sector_name_cache[ts_code] = sector
    return sector


def _get_sector_return(ts_code: str, exam_date: str) -> tuple[float, str]:
    """获取个股所属板块从 exam_date 起 10 个交易日的收益率。

    返回 (板块收益率, 板块名称)。失败返回 (0.0, "")。
    """
    sector = _get_sector_info(ts_code)
    if not sector:
        return 0.0, ""

    cache_key = f"{sector}_{exam_date}"
    if cache_key in _sector_return_cache:
        return _sector_return_cache[cache_key], sector

    try:
        import akshare as ak
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
        df = ak.stock_board_industry_hist_em(
            symbol=sector, period="日k",
            start_date=start, end_date=end, adjust=""
        )
        if df is not None and not df.empty:
            df = df.rename(columns={"日期": "trade_date", "收盘": "close"})
            df["trade_date"] = df["trade_date"].astype(str).str.replace("-", "")
            df = df.sort_values("trade_date").reset_index(drop=True)
            ret = _calc_return_from_kline(df, exam_date)
            if ret is not None:
                _sector_return_cache[cache_key] = ret
                return ret, sector
    except Exception as e:
        logger.debug("[sim] sector return failed for %s: %s", sector, e)

    return 0.0, sector

_PROXY = "http://127.0.0.1:7890"


def _clear_proxy():
    """确保代理指向 Clash（Clash 规则会让国内域名走 DIRECT）。
    不能清除代理——Windows 系统代理无法通过环境变量绕过。
    """
    os.environ["HTTP_PROXY"] = _PROXY
    os.environ["HTTPS_PROXY"] = _PROXY
    os.environ.pop("NO_PROXY", None)


def _restore_proxy():
    """恢复代理（和 _clear_proxy 相同，因为都走 Clash）"""
    os.environ["HTTP_PROXY"] = _PROXY
    os.environ["HTTPS_PROXY"] = _PROXY
    os.environ.pop("NO_PROXY", None)

# 模拟分析的 system prompt（和正式一样的评分框架，但告诉 Claude 这是模拟训练）
def _fetch_historical_kline(ts_code: str, end_date: str, days: int = 120) -> pd.DataFrame | None:
    """获取截止到 end_date 的历史 K 线。多源 fallback。

    优先 Tushare → akshare → 新浪财经 API。
    返回 DataFrame 含 open/high/low/close/vol/pct_chg 列。
    """
    import requests as _req
    # 国内数据源不走代理，用 session 级别控制而非修改全局 os.environ
    _session = _req.Session()
    _session.trust_env = False  # 忽略系统代理

    end_dt = datetime.strptime(end_date, "%Y%m%d")
    start_date = (end_dt - timedelta(days=days)).strftime("%Y%m%d")

    # 尝试 Tushare
    try:
        from data.tushare_client import _get_pro
        pro = _get_pro()
        if pro:
            df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            if df is not None and len(df) >= 30:
                df = df.sort_values("trade_date").reset_index(drop=True)
                return df
    except Exception as exc:
        logger.warning("[sim] kline data fetch failed: %r", exc)

    # 尝试新浪财经 API（最可靠的 fallback）
    try:
        import json, requests
        code = ts_code.split(".")[0]
        market = "sh" if ts_code.endswith(".SH") else "sz"
        symbol = f"{market}{code}"

        s = requests.Session()
        # 走 Clash 代理（Clash 规则让 sina.cn 走 DIRECT）
        url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var/CN_MarketDataService.getKLineData"
        r = s.get(url, params={"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(days)},
                  timeout=15, headers={"Referer": "https://finance.sina.com.cn"})

        text = r.text
        data = json.loads(text[text.index("(") + 1:text.rindex(")")])
        if not data:
            return None

        rows = []
        for d in data:
            day_str = d["day"].replace("-", "")
            if day_str > end_date:
                continue  # 时间锁定：不要未来数据！
            rows.append({
                "trade_date": day_str,
                "open": float(d["open"]),
                "high": float(d["high"]),
                "low": float(d["low"]),
                "close": float(d["close"]),
                "vol": float(d["volume"]),
                "pct_chg": 0,  # 新浪不直接给涨跌幅，后面计算
            })

        if not rows:
            return None

        df = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
        # 计算涨跌幅
        df["pct_chg"] = df["close"].pct_change() * 100
        df["pct_chg"] = df["pct_chg"].fillna(0)
        return df if len(df) >= 30 else None

    except Exception as exc:
        logger.warning("[sim] sina kline fetch failed: %r", exc)

    # 尝试 akshare（东方财富）
    try:
        from data.fallback import ak_get_price_df
        df_ak, err = ak_get_price_df(ts_code, days + 30)
        if err is None and df_ak is not None and len(df_ak) >= 30:
            df_ak["日期"] = df_ak["日期"].astype(str)
            df_ak = df_ak[df_ak["日期"] <= end_date]
            df_ak = df_ak.rename(columns={
                "日期": "trade_date", "开盘": "open", "最高": "high",
                "最低": "low", "收盘": "close", "成交量": "vol", "涨跌幅": "pct_chg",
            })
            if len(df_ak) >= 30:
                return df_ak.sort_values("trade_date").reset_index(drop=True)
    except Exception as exc:
        logger.debug("[sim] akshare kline fetch failed: %r", exc)

    # 尝试 baostock（最终兜底，用 end_date 计算精确时间范围）
    try:
        import baostock as bs
        bs_code = ts_code.split(".")[1].lower() + "." + ts_code.split(".")[0]
        bs_start = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=days)).strftime("%Y-%m-%d")
        bs_end = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

        lg = bs.login()
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount,pctChg",
                start_date=bs_start, end_date=bs_end,
                frequency="d", adjustflag="2",
            )
            data = []
            while rs.error_code == "0" and rs.next():
                data.append(rs.get_row_data())
            if data and len(data) >= 30:
                import pandas as _pd
                df_bs = _pd.DataFrame(data, columns=rs.fields)
                df_bs = df_bs.rename(columns={
                    "date": "trade_date", "open": "open", "high": "high",
                    "low": "low", "close": "close", "volume": "vol", "pctChg": "pct_chg",
                })
                for c in ["open", "high", "low", "close", "vol", "pct_chg"]:
                    df_bs[c] = _pd.to_numeric(df_bs[c], errors="coerce")
                df_bs["trade_date"] = df_bs["trade_date"].str.replace("-", "")
                return df_bs.sort_values("trade_date").reset_index(drop=True)
        finally:
            bs.logout()
    except Exception as exc:
        logger.warning("[sim] baostock kline fetch failed: %r", exc)

    return None


SIM_SYSTEM = """你是林铛，A股投研AI分析师。你正在进行模拟训练——用历史数据练习分析能力。

请基于以下数据进行深度分析，给出四维评分和操作建议。

【评分框架（百分制 0-100）】
- 基本面（15%权重）：财务健康、盈利能力、成长性
- 预期差（35%权重）：催化剂、市场未反映的信息
- 资金面（30%权重）：主力资金、北向资金、融资动向
- 技术面（20%权重）：K线形态、均线趋势、量价关系

【输出格式】
## 核心判断
（2-3句话概括看多/看空理由）

## 四维评分
- 基本面: X/100 -- 理由
- 预期差: X/100 -- 理由
- 资金面: X/100 -- 理由
- 技术面: X/100 -- 理由

## 方向判断
看多/看空/中性 + 置信度

## 关键风险
1-3个最大风险点

<<<SCORES>>>
基本面: X/100
预期差: X/100
资金面: X/100
技术面: X/100
<<<END_SCORES>>>
"""


# ══════════════════════════════════════════════════════════════════
# 选题：从历史数据中选出有代表性的"考题"
# ══════════════════════════════════════════════════════════════════

def _select_exam_stocks(count: int = 5, sector_focus: str = "",
                        lookback_days: int = 30) -> list[dict]:
    """从近 N 天历史中选出有代表性的股票+日期组合。

    选题策略：
      - 大涨股（10日涨>10%）：测试能否提前识别
      - 大跌股（10日跌>8%）：测试能否提前规避
      - 震荡股（10日波动<3%）：测试评分校准

    返回 [{ts_code, stock_name, exam_date, category, actual_return_10d}, ...]
    """
    _clear_proxy()  # 国内数据源不走代理

    # 选一个考试日期（15-30天前，确保有10天后续数据）
    exam_offset = random.randint(15, lookback_days)
    exam_date = (datetime.now() - timedelta(days=exam_offset)).strftime("%Y%m%d")

    # 尝试从热门股票池中选题（不依赖全市场行情 API）
    # 用一组代表性股票直接获取历史数据
    stock_pool = [
        ("600519.SH", "贵州茅台"), ("000858.SZ", "五粮液"), ("601318.SH", "中国平安"),
        ("600036.SH", "招商银行"), ("000333.SZ", "美的集团"), ("002714.SZ", "牧原股份"),
        ("300750.SZ", "宁德时代"), ("603986.SH", "兆易创新"), ("002475.SZ", "立讯精密"),
        ("600809.SH", "山西汾酒"), ("000725.SZ", "京东方A"), ("601012.SH", "隆基绿能"),
        ("002049.SZ", "紫光国微"), ("300059.SZ", "东方财富"), ("600276.SH", "恒瑞医药"),
        ("002415.SZ", "海康威视"), ("601899.SH", "紫金矿业"), ("300760.SZ", "迈瑞医疗"),
        ("000568.SZ", "泸州老窖"), ("603259.SH", "药明康德"), ("002594.SZ", "比亚迪"),
        ("600900.SH", "长江电力"), ("601888.SH", "中国中免"), ("300274.SZ", "阳光电源"),
        ("688981.SH", "中芯国际"), ("002371.SZ", "北方华创"), ("300498.SZ", "温氏股份"),
        ("601166.SH", "兴业银行"), ("000001.SZ", "平安银行"), ("600030.SH", "中信证券"),
    ]
    # 弱项强化：把历史失败率高的板块股票排在前面
    try:
        stats = get_simulation_stats()
        weak = [w["sector"] for w in stats.get("weak_sectors", [])]
        if weak:
            # 标记弱项板块的股票
            weak_stocks = []
            other_stocks = []
            for s in stock_pool:
                sector = _get_sector_info(s[0])
                if sector and any(w in sector for w in weak):
                    weak_stocks.append(s)
                else:
                    other_stocks.append(s)
            random.shuffle(weak_stocks)
            random.shuffle(other_stocks)
            # 弱项股票优先，占 50% 配额
            stock_pool = weak_stocks + other_stocks
            if weak_stocks:
                logger.info("[sim] 弱项强化: %d只弱项板块股票优先 (%s)",
                            len(weak_stocks), ", ".join(weak[:3]))
    except Exception as exc:
        logger.debug("[sim] weak stock identification failed: %r", exc)

    merged_rows = []
    for ts_code, name in stock_pool[:20]:  # 取20只够了
        try:
            df = _fetch_historical_kline(ts_code, datetime.now().strftime("%Y%m%d"), days=60)
            if df is None or len(df) < 15:
                continue

            # 找 exam_date 对应的行（最近的交易日）
            exam_idx = None
            for i, row in df.iterrows():
                if str(row.get("trade_date", ""))[:8] <= exam_date:
                    exam_idx = i

            if exam_idx is None:
                continue

            pos = df.index.get_loc(exam_idx)
            future_pos = pos + 10  # 约10个交易日后
            if future_pos >= len(df):
                future_pos = len(df) - 1

            exam_close = df.iloc[pos]["close"]
            future_close = df.iloc[future_pos]["close"]
            ret_10d = (future_close - exam_close) / exam_close * 100

            actual_exam_date = str(df.iloc[pos].get("trade_date", exam_date))[:8]

            merged_rows.append({
                "ts_code": ts_code,
                "stock_name": name,
                "close": exam_close,
                "close_future": future_close,
                "return_10d": ret_10d,
                "exam_date": actual_exam_date,
            })
        except Exception as exc:
            logger.debug("[sim] training case failed: %r", exc)
            continue

    if not merged_rows:
        logger.warning("[sim] no stocks available for simulation")
        return []

    merged = pd.DataFrame(merged_rows)

    # 按板块过滤（如果指定了板块）
    if sector_focus:
        try:
            from data.tushare_client import load_stock_list
            stock_basic, _ = load_stock_list()
            if stock_basic is not None and not stock_basic.empty:
                sector_stocks = stock_basic[stock_basic["name"].str.contains(sector_focus, na=False) |
                                            stock_basic["industry"].str.contains(sector_focus, na=False)]
                if not sector_stocks.empty:
                    merged = merged[merged["ts_code"].isin(sector_stocks["ts_code"])]
        except Exception as exc:
            logger.debug("[sim] sector filter failed: %r", exc)

    # 分类选题（预先过滤，避免重复计算）
    candidates = []

    rise_df = merged[merged["return_10d"] > 10]
    fall_df = merged[merged["return_10d"] < -8]
    flat_df = merged[(merged["return_10d"] > -3) & (merged["return_10d"] < 3)]

    # 大涨股
    n_rise = min(count // 2 + 1, len(rise_df))
    if n_rise > 0:
        for _, row in rise_df.sample(n=n_rise).iterrows():
            candidates.append({
                "ts_code": row["ts_code"], "stock_name": row.get("stock_name", row["ts_code"]),
                "exam_date": row.get("exam_date", exam_date),
                "close_on_exam": row["close"],
                "actual_return_10d": round(row["return_10d"], 2), "category": "big_rise",
            })

    # 大跌股
    n_fall = min(count // 3 + 1, len(fall_df))
    if n_fall > 0:
        for _, row in fall_df.sample(n=n_fall).iterrows():
            candidates.append({
                "ts_code": row["ts_code"], "stock_name": row.get("stock_name", row["ts_code"]),
                "exam_date": row.get("exam_date", exam_date),
                "close_on_exam": row["close"],
                "actual_return_10d": round(row["return_10d"], 2), "category": "big_fall",
            })

    # 震荡股
    n_flat = min(count // 3, len(flat_df))
    if n_flat > 0:
        for _, row in flat_df.sample(n=n_flat).iterrows():
            candidates.append({
                "ts_code": row["ts_code"], "stock_name": row.get("stock_name", row["ts_code"]),
                "exam_date": row.get("exam_date", exam_date),
                "close_on_exam": row["close"],
                "actual_return_10d": round(row["return_10d"], 2), "category": "flat",
            })

    random.shuffle(candidates)
    return candidates[:count]


# ══════════════════════════════════════════════════════════════════
# 模拟分析：时间锁定 + Claude 分析 + 判卷
# ══════════════════════════════════════════════════════════════════

def _run_single_simulation(exam: dict) -> dict | None:
    """对单只股票执行一次模拟分析。

    1. 获取截止到 exam_date 的历史数据
    2. 构建 prompt（不泄露未来数据）
    3. 调用 Claude 分析
    4. 解析评分
    5. 对比实际结果 → 判卷
    6. 生成反思教训
    7. 存入 case_memory
    """
    from ai.client import call_ai, call_ai_stream, get_ai_client
    from data.tushare_client import _get_pro
    from data.indicators import compute_indicators, format_indicators_section
    from services.analysis_service import parse_scores

    ts_code = exam["ts_code"]
    stock_name = exam["stock_name"]
    exam_date = exam["exam_date"]
    actual_return = exam["actual_return_10d"]
    category = exam["category"]

    logger.info("[sim] analyzing %s (%s) as of %s (actual 10d: %+.1f%%)",
                stock_name, ts_code, exam_date, actual_return)

    # 1. 获取截止到 exam_date 的历史 K 线（时间锁定！）
    _clear_proxy()  # 国内数据源不走代理
    df = _fetch_historical_kline(ts_code, exam_date, days=120)
    if df is None or len(df) < 30:
        return None

    # 2. 计算技术指标（截止到 exam_date）
    # compute_indicators 期望中文列名，做映射
    df_cn = df.rename(columns={
        "open": "开盘", "high": "最高", "low": "最低",
        "close": "收盘", "vol": "成交量", "pct_chg": "涨跌幅",
    })
    indicators = compute_indicators(df_cn)
    ind_section = format_indicators_section(indicators)

    # K线形态识别
    pattern_text = ""
    try:
        from knowledge.kline_patterns import detect_all_patterns, classify_position, classify_volume_state
        patterns = detect_all_patterns(df)
        if patterns:
            names = [p.name for p in patterns[:4]]
            pos = classify_position(df)
            vol = classify_volume_state(df)
            pattern_text = f"K线形态: {', '.join(names)} | 位置: {pos} | 量能: {vol}"
    except Exception as exc:
        logger.debug("[sim] kline pattern analysis failed: %r", exc)

    # 价格快照
    last = df.iloc[-1]
    ma5 = df["close"].iloc[-5:].mean() if len(df) >= 5 else last["close"]
    ma20 = df["close"].iloc[-20:].mean() if len(df) >= 20 else last["close"]
    ma60 = df["close"].iloc[-60:].mean() if len(df) >= 60 else last["close"]
    price_snap = (
        f"收盘: {last['close']:.2f} | 涨跌幅: {last.get('pct_chg', 0):.2f}%\n"
        f"MA5: {ma5:.2f} | MA20: {ma20:.2f} | MA60: {ma60:.2f}\n"
        f"近5日涨跌: {((last['close'] - df.iloc[-6]['close']) / df.iloc[-6]['close'] * 100) if len(df) > 5 else 0:.1f}%\n"
        f"近20日涨跌: {((last['close'] - df.iloc[-21]['close']) / df.iloc[-21]['close'] * 100) if len(df) > 20 else 0:.1f}%"
    )

    # 3. 构建 prompt（时间锁定，不含未来数据）
    exam_date_fmt = f"{exam_date[:4]}-{exam_date[4:6]}-{exam_date[6:]}"
    user_prompt = f"""【模拟训练】请分析以下股票（数据截止到 {exam_date_fmt}）

【标的】{stock_name}（{ts_code}）
【日期】{exam_date_fmt}

【价格快照】
{price_snap}

【技术指标】
{ind_section}

{f'【K线形态】{chr(10)}{pattern_text}' if pattern_text else ''}

请给出四维评分和方向判断。"""

    # 4. 调用 Claude Sonnet 分析
    # 恢复代理（Claude API 走国外需要代理）
    _restore_proxy()

    model_name = "⚡ Claude Sonnet（MAX）"
    client, cfg, err = get_ai_client(model_name)
    if err and not cfg:
        logger.warning("[sim] model unavailable: %s", err)
        return None

    cfg_no_search = {**cfg, "supports_search": False}

    # CLI provider（Claude/Gemini/Codex）用 call_ai_stream，API provider 用 call_ai
    if cfg.get("provider") in ("gemini_cli", "codex_cli", "claude_cli"):
        try:
            stream = call_ai_stream(client, cfg_no_search, user_prompt, system=SIM_SYSTEM, max_tokens=2000)
            for _ in stream:
                pass
            analysis = stream.full_text
            call_err = None if analysis else "empty response"
        except Exception as exc:
            analysis = ""
            call_err = f"AI 调用异常：{exc}"
    else:
        analysis, call_err = call_ai(client, cfg_no_search, user_prompt, system=SIM_SYSTEM, max_tokens=2000)

    if call_err or not analysis:
        logger.warning("[sim] analysis failed for %s: %s", stock_name, call_err)
        return None

    # 5. 解析评分
    scores = parse_scores(analysis)
    if not scores:
        logger.warning("[sim] failed to parse scores for %s", stock_name)
        return None

    weighted = scores.get("综合加权", 50)
    direction = "bullish" if weighted >= 55 else ("bearish" if weighted <= 45 else "neutral")

    # 6. 获取同期大盘+板块基准收益，三级超额判卷
    market_return = _get_market_return(exam_date)
    sector_return, sector_name = _get_sector_return(ts_code, exam_date)

    # 超额收益 = 个股 - max(大盘, 板块)，扣除最强的β
    benchmark_return = max(market_return, sector_return) if sector_return != 0 else market_return
    excess_return = actual_return - benchmark_return

    # 归因分解
    market_drag = market_return  # 大盘贡献
    sector_drag = sector_return - market_return if sector_return != 0 else 0  # 板块额外贡献（扣除大盘）
    stock_alpha = actual_return - sector_return if sector_return != 0 else actual_return - market_return  # 个股α

    # 判卷：用扣除板块β后的超额收益
    hit = False
    if direction == "bullish" and excess_return > 0:
        hit = True  # 看多且跑赢基准
    elif direction == "bearish" and excess_return < 0:
        hit = True  # 看空且跑输基准
    elif direction == "neutral" and abs(excess_return) < 3:
        hit = True  # 中性且与基准同步

    outcome = "win" if hit else "loss"

    # 7. 生成反思教训（含大盘+板块+个股α三级归因）
    dir_cn = DIRECTION_CN.get(direction, "中性")
    lesson = ""
    try:
        # 构建归因上下文
        ctx_parts = []
        ctx_parts.append(f"同期大盘(上证){market_return:+.1f}%")
        if sector_name and sector_return != 0:
            ctx_parts.append(f"板块({sector_name}){sector_return:+.1f}%")
            ctx_parts.append(f"个股α{stock_alpha:+.1f}%")
        ctx_parts.append(f"超额收益{excess_return:+.1f}%")
        market_ctx = "，".join(ctx_parts) + "。"

        reflection_prompt = (
            f"我在 {exam_date_fmt} 模拟分析了 {stock_name}，给了{weighted}分{dir_cn}。"
            f"实际10日收益{actual_return:+.1f}%。{market_ctx}"
            f"{'判断正确（跑赢基准方向对了）' if hit else '判断失误'}。"
            f"用2句话总结教训，区分三个层面：大盘系统性因素、板块行业因素、个股自身α。"
        )
        if cfg.get("provider") in ("gemini_cli", "codex_cli", "claude_cli"):
            stream = call_ai_stream(client, cfg_no_search, reflection_prompt,
                system="你是林铛，简洁务实地总结模拟训练教训。只输出2句话。", max_tokens=200)
            for _ in stream:
                pass
            lesson_text = stream.full_text
        else:
            lesson_text, _ = call_ai(client, cfg_no_search, reflection_prompt,
                system="你是林铛，简洁务实地总结模拟训练教训。只输出2句话。", max_tokens=200)
        if lesson_text:
            lesson = lesson_text.strip()
    except Exception as exc:
        logger.debug("[sim] lesson generation failed: %r", exc)

    # 8. 存入 case_memory
    try:
        from knowledge.case_memory import CaseCard, store_case, classify_outcome, extract_sector_tags
        from knowledge.case_memory import build_situation_summary

        sector_tags = extract_sector_tags(stock_name)
        regime = "shock"  # 模拟训练默认用 shock
        try:
            from knowledge.regime_detector import get_regime_history
            for entry in get_regime_history(days=60):
                if entry["date"] == exam_date_fmt or entry["date"].replace("-", "") == exam_date:
                    regime = entry["regime"]
                    break
        except Exception as exc:
            logger.debug("[sim] case store failed: %r", exc)

        regime_label = {"bull": "牛市", "bear": "熊市", "shock": "震荡市", "rotation": "轮动市"}.get(regime, "震荡市")

        case = CaseCard(
            case_id=f"sim_{uuid.uuid4().hex[:6]}",
            report_date=exam_date_fmt,
            stock_code=ts_code,
            stock_name=stock_name,
            source="simulation",  # 标记为模拟训练
            regime=regime,
            regime_label=regime_label,
            sector_tags=sector_tags,
            score_fundamental=scores.get("基本面", 50),
            score_expectation=scores.get("预期差", 50),
            score_capital=scores.get("资金面", 50),
            score_technical=scores.get("技术面", 50),
            score_weighted=weighted,
            direction=direction,
            reasoning_summary=analysis[:200],
            return_5d=None,  # 模拟训练无精确5日数据，不伪造
            return_10d=actual_return,
            return_20d=None,  # 模拟训练无精确20日数据，不伪造
            hit_10d=hit,
            outcome_type=outcome,
            lesson=lesson,
            lesson_generated_at=datetime.now().isoformat(timespec="seconds") if lesson else None,
            situation_summary=f"模拟训练 {exam_date_fmt} {stock_name} {weighted}分{dir_cn} 实际{actual_return:+.1f}%",
        )
        store_case(case)
        logger.info("[sim] case stored: %s %s %d分%s → %+.1f%% %s",
                    stock_name, exam_date_fmt, weighted, dir_cn, actual_return,
                    "✅" if hit else "❌")
    except Exception as exc:
        logger.warning("[sim] case storage failed: %r", exc)

    # 9. 高质量教训→智慧库自动反哺（去重后写入）
    if lesson and len(lesson) > 50 and outcome in ("win", "loss"):
        try:
            from knowledge.wisdom import add_wisdom, get_all_wisdom
            # 提取核心教训（取第一句话作为智慧）
            wisdom_text = lesson.split("。")[0] + "。" if "。" in lesson else lesson[:100]
            # 简单去重：与已有智慧文本相似度>60%则跳过
            existing = get_all_wisdom()
            is_dup = False
            for ew in existing:
                common = sum(1 for c in wisdom_text if c in ew["wisdom"])
                ratio = common / max(len(wisdom_text), 1)
                if ratio > 0.6:
                    is_dup = True
                    break
            if is_dup:
                logger.debug("[sim] lesson duplicate, skipping wisdom add")
            else:
                cat = "risk" if any(k in lesson for k in ["风险", "止损", "回撤", "低估", "高估"]) else \
                      "timing" if any(k in lesson for k in ["买入", "卖出", "突破", "形态", "趋势"]) else \
                      "psychology" if any(k in lesson for k in ["心态", "恐惧", "贪婪", "纪律"]) else \
                      "general"
                add_wisdom(
                    source_type="experience",
                    source_name=f"模拟训练复盘·{stock_name}",
                    category=cat,
                    wisdom=wisdom_text,
                    context=f"{exam_date_fmt} {stock_name} {weighted}分{dir_cn}→{actual_return:+.1f}%",
                    tags=[sector_name] if sector_name else [],
                )
                logger.info("[sim] lesson → wisdom: %s", wisdom_text[:40])
        except Exception as exc:
            logger.debug("[sim] wisdom auto-add failed: %r", exc)

    result = {
        "stock_name": stock_name,
        "ts_code": ts_code,
        "exam_date": exam_date_fmt,
        "category": category,
        "scores": scores,
        "direction": dir_cn,
        "weighted": weighted,
        "actual_return_10d": actual_return,
        "market_return_10d": market_return,
        "sector_name": sector_name,
        "sector_return_10d": sector_return,
        "stock_alpha": round(stock_alpha, 2),
        "excess_return": round(excess_return, 2),
        "hit": hit,
        "outcome": outcome,
        "lesson": lesson[:150] if lesson else "",
    }

    # 记录到 simulation_log
    try:
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        with open(SIM_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({**result, "timestamp": datetime.now().isoformat()}, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("[sim] result log write failed: %r", exc)

    return result


# ══════════════════════════════════════════════════════════════════
# 主入口：批量模拟训练
# ══════════════════════════════════════════════════════════════════

def run_simulation_training(count: int = 5, sector_focus: str = "",
                            delay_between: int = 30) -> dict:
    """执行批量模拟训练。

    count: 训练几只股票
    sector_focus: 针对特定板块训练（可选）
    delay_between: 每只之间间隔秒数（避免限流）

    返回训练摘要。
    """
    logger.info("[sim] starting simulation training: count=%d, sector=%s", count, sector_focus or "all")

    # 选题
    exams = _select_exam_stocks(count=count, sector_focus=sector_focus)
    if not exams:
        return {"status": "no_exams", "message": "无法获取历史数据，请检查 Tushare 连接"}

    results = []
    hits = 0
    total = 0

    for i, exam in enumerate(exams):
        logger.info("[sim] training %d/%d: %s (%s)", i + 1, len(exams), exam["stock_name"], exam["exam_date"])

        result = _run_single_simulation(exam)
        if result:
            results.append(result)
            total += 1
            if result["hit"]:
                hits += 1

        # 间隔避免限流
        if i < len(exams) - 1 and delay_between > 0:
            logger.info("[sim] waiting %ds before next...", delay_between)
            time.sleep(delay_between)

    hit_rate = hits / total * 100 if total > 0 else 0

    summary = {
        "status": "ok",
        "total_trained": total,
        "hit_rate": round(hit_rate, 1),
        "hits": hits,
        "misses": total - hits,
        "results": results,
    }

    logger.info("[sim] training complete: %d/%d hit (%.1f%%)", hits, total, hit_rate)
    return summary


def get_simulation_stats() -> dict:
    """获取模拟训练累计统计，含分方向、分板块明细。"""
    if not SIM_LOG_FILE.exists():
        return {"total": 0}

    entries = []
    with open(SIM_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not entries:
        return {"total": 0}

    total = len(entries)
    hits = sum(1 for e in entries if e.get("hit"))

    # 分类别统计（big_rise/big_fall/flat）
    by_category = {}
    for e in entries:
        cat = e.get("category", "unknown")
        by_category.setdefault(cat, {"total": 0, "hits": 0})
        by_category[cat]["total"] += 1
        if e.get("hit"):
            by_category[cat]["hits"] += 1
    for cat_data in by_category.values():
        cat_data["hit_rate"] = round(cat_data["hits"] / cat_data["total"] * 100, 1) if cat_data["total"] > 0 else 0

    # 分方向统计（看多/看空/中性）
    by_direction = {}
    for e in entries:
        d = e.get("direction", "未知")
        by_direction.setdefault(d, {"total": 0, "hits": 0})
        by_direction[d]["total"] += 1
        if e.get("hit"):
            by_direction[d]["hits"] += 1
    for d_data in by_direction.values():
        d_data["hit_rate"] = round(d_data["hits"] / d_data["total"] * 100, 1) if d_data["total"] > 0 else 0

    # 分板块统计（从 sector_name 字段）
    by_sector = {}
    for e in entries:
        sector = e.get("sector_name", "") or "未知"
        by_sector.setdefault(sector, {"total": 0, "hits": 0})
        by_sector[sector]["total"] += 1
        if e.get("hit"):
            by_sector[sector]["hits"] += 1
    for s_data in by_sector.values():
        s_data["hit_rate"] = round(s_data["hits"] / s_data["total"] * 100, 1) if s_data["total"] > 0 else 0
    # 按失败率排序找弱项
    weak_sectors = sorted(
        [(k, v) for k, v in by_sector.items() if v["total"] >= 3],
        key=lambda x: x[1]["hit_rate"]
    )[:5]

    return {
        "total": total,
        "hit_rate": round(hits / total * 100, 1) if total > 0 else 0,
        "by_category": by_category,
        "by_direction": by_direction,
        "by_sector": {k: v for k, v in by_sector.items() if v["total"] >= 2},
        "weak_sectors": [{"sector": k, **v} for k, v in weak_sectors],
        "recent": entries[-5:],
    }
