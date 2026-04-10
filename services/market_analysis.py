# -*- coding: utf-8 -*-
"""每日大盘深度分析 — 数据采集 + Opus 分析 + 按日缓存。

首次 analyze 时自动触发，当日后续分析复用缓存。
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
MARKET_DIR = BASE_DIR / "data" / "knowledge" / "market_daily"


# ── 公开入口 ────────────────────────────────────────────────────────

def get_or_run_market_analysis(analyst_model: str) -> str:
    """获取当日大盘分析 markdown。有缓存读缓存，无缓存触发 Opus 分析。

    Returns:
        格式化的大盘分析 markdown 文本，可直接拼入个股数据包。
        采集或分析失败时返回精简版 fallback。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    md_path = MARKET_DIR / f"{today}.md"

    # 有缓存直接返回
    if md_path.exists():
        logger.info("[market] 读取当日大盘分析缓存: %s", md_path.name)
        return md_path.read_text(encoding="utf-8")

    # 无缓存 → 采集数据 → AI 分析
    logger.info("[market] 当日首次分析，触发大盘深度分析 (%s)", analyst_model)
    try:
        raw_data = _collect_all_market_data()
        data_text = _format_raw_data(raw_data)
        analysis_md = _run_ai_analysis(data_text, analyst_model)

        # 清理 AI 输出中的标记
        from services.war_room import _strip_markers
        analysis_md = _strip_markers(analysis_md)

        # 加上标题头
        full_md = f"【每日大盘深度分析】{today}\n\n{analysis_md}"

        # 持久化
        _save_cache(today, raw_data, full_md)
        return full_md

    except Exception as exc:
        logger.error("[market] 大盘分析失败: %r，使用精简 fallback", exc)
        return _fallback_brief()


# ── 数据采集 ────────────────────────────────────────────────────────

def _collect_all_market_data() -> dict:
    """采集全部大盘原始数据。每项独立 try/except，不互相阻塞。"""
    data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "a_indices": {},
        "total_turnover_10d": [],
        "breadth": {},
        "sectors": {},
        "us_indices": {},
        "hk_indices": {},
        "regime": {},
    }

    # 1. A股四大指数（复用 macro_intel 的逻辑）
    try:
        data["a_indices"] = _collect_a_indices()
    except Exception as e:
        logger.warning("[market] A股指数采集失败: %s", e)

    # 2. 两市总成交额（近10日）
    try:
        data["total_turnover_10d"] = _collect_turnover()
    except Exception as e:
        logger.warning("[market] 成交额采集失败: %s", e)

    # 3. 市场广度（涨跌家数）
    try:
        data["breadth"] = _collect_breadth()
    except Exception as e:
        logger.warning("[market] 市场广度采集失败: %s", e)

    # 4. 板块轮动
    try:
        data["sectors"] = _collect_sectors()
    except Exception as e:
        logger.warning("[market] 板块数据采集失败: %s", e)

    # 5. 美股三大指数
    try:
        data["us_indices"] = _collect_us_indices()
    except Exception as e:
        logger.warning("[market] 美股指数采集失败: %s", e)

    # 6. 港股恒生
    try:
        data["hk_indices"] = _collect_hk_indices()
    except Exception as e:
        logger.warning("[market] 港股指数采集失败: %s", e)

    # 7. 当前 regime
    try:
        from knowledge.regime_detector import detect_current_regime
        regime = detect_current_regime()
        if regime:
            data["regime"] = {"regime": regime.get("regime"), "label": regime.get("regime_label")}
    except Exception as e:
        logger.warning("[market] regime 检测失败: %s", e)

    return data


def _col(df, *candidates):
    """按候选名查找 DataFrame 列，兼容中文编码差异。"""
    for c in candidates:
        if c in df.columns:
            return c
    # Fallback: 按列位置（get_price_df 固定顺序：日期/开盘/最高/最低/收盘/成交量/成交额/涨跌幅）
    return df.columns[0] if len(df.columns) > 0 else None


def _collect_a_indices() -> dict:
    """采集 A 股四大指数近20日行情。"""
    from data.tushare_client import get_price_df

    indices = {
        "000001.SH": "上证指数",
        "399001.SZ": "深证成指",
        "399006.SZ": "创业板指",
        "000688.SH": "科创50",
    }
    result = {}
    for ts_code, name in indices.items():
        try:
            df, err = get_price_df(ts_code, days=30)
            if err or df is None or df.empty or len(df.columns) < 7:
                continue

            # get_price_df 返回升序，列顺序固定：日期/开盘/最高/最低/收盘/成交量/成交额/涨跌幅
            cols = df.columns.tolist()
            c_date, c_open, c_high, c_low, c_close, c_vol, c_amt = cols[0], cols[1], cols[2], cols[3], cols[4], cols[5], cols[6]

            records = []
            for _, row in df.tail(10).iterrows():
                records.append({
                    "date": str(row[c_date]),
                    "open": round(float(row[c_open]), 2),
                    "close": round(float(row[c_close]), 2),
                    "high": round(float(row[c_high]), 2),
                    "low": round(float(row[c_low]), 2),
                    "volume": float(row[c_vol]),
                    "amount": float(row[c_amt]),
                })

            closes_all = df[c_close].astype(float).values
            closes_10 = [r["close"] for r in records]
            ma5 = sum(closes_10[-5:]) / min(5, len(closes_10)) if closes_10 else 0
            ma20 = float(closes_all[-20:].mean()) if len(closes_all) >= 20 else ma5
            ma60 = float(closes_all[-60:].mean()) if len(closes_all) >= 5 else ma20

            result[name] = {
                "ts_code": ts_code,
                "recent_10d": records,
                "ma5": round(ma5, 2),
                "ma20": round(ma20, 2),
                "ma60": round(ma60, 2),
            }
        except Exception as e:
            logger.debug("[market] index %s failed: %s", ts_code, e)

    return result


def _collect_turnover() -> list[dict]:
    """采集两市近10日总成交额。从上证+深证成交额估算。"""
    from data.tushare_client import get_price_df

    turnover = []
    sh_data, sz_data = {}, {}

    for code, store in [("000001.SH", sh_data), ("399001.SZ", sz_data)]:
        try:
            df, _ = get_price_df(code, days=15)
            if df is not None and not df.empty and len(df.columns) >= 7:
                c_date = df.columns[0]  # 日期列
                c_amt = df.columns[6]   # 成交额列
                for _, row in df.tail(10).iterrows():
                    date = str(row[c_date])
                    amt = float(row[c_amt])
                    store[date] = amt
        except Exception:
            pass

    # 合并日期
    all_dates = sorted(set(list(sh_data.keys()) + list(sz_data.keys())))
    for d in all_dates[-10:]:
        total = sh_data.get(d, 0) + sz_data.get(d, 0)
        # tushare 成交额单位取决于数据源，通常为元或千元
        yi = total / 1e8 if total > 1e10 else total / 1e4  # 自适应单位
        turnover.append({"date": d, "total_amount_yi": round(yi, 0)})

    return turnover


def _collect_breadth() -> dict:
    """采集市场广度（涨跌停数据）。"""
    from data.macro_intel import _collect_market_breadth
    return _collect_market_breadth()


def _collect_sectors() -> dict:
    """采集板块轮动数据。"""
    try:
        import akshare as ak
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return {}

        # 按涨跌幅排序
        df["涨跌幅"] = df["涨跌幅"].astype(float)
        top5 = df.nlargest(5, "涨跌幅")[["板块名称", "涨跌幅"]].to_dict("records")
        bottom5 = df.nsmallest(5, "涨跌幅")[["板块名称", "涨跌幅"]].to_dict("records")
        return {"top5": top5, "bottom5": bottom5}
    except Exception as e:
        logger.debug("[market] sector data failed: %s", e)
        return {}


def _collect_us_indices() -> dict:
    """采集美股三大指数近5日行情。"""
    import akshare as ak

    symbols = {".DJI": "道琼斯", ".IXIC": "纳斯达克", ".INX": "标普500"}
    result = {}
    for symbol, name in symbols.items():
        try:
            df = ak.index_us_stock_sina(symbol=symbol)
            if df is not None and not df.empty:
                recent = df.tail(5)
                records = []
                for _, row in recent.iterrows():
                    records.append({
                        "date": str(row["date"]),
                        "close": round(float(row["close"]), 2),
                        "open": round(float(row["open"]), 2),
                        "high": round(float(row["high"]), 2),
                        "low": round(float(row["low"]), 2),
                    })
                # 计算涨跌幅
                if len(records) >= 2:
                    latest = records[-1]["close"]
                    prev = records[-2]["close"]
                    ret = round((latest - prev) / prev * 100, 2) if prev else 0
                else:
                    ret = 0
                result[name] = {"recent_5d": records, "latest_ret_pct": ret}
        except Exception as e:
            logger.debug("[market] US index %s failed: %s", symbol, e)

    return result


def _collect_hk_indices() -> dict:
    """采集港股恒生指数近5日行情。"""
    import akshare as ak

    result = {}
    for symbol, name in [("HSI", "恒生指数"), ("HSTECH", "恒生科技")]:
        try:
            df = ak.stock_hk_index_daily_sina(symbol=symbol)
            if df is not None and not df.empty:
                recent = df.tail(5)
                records = []
                for _, row in recent.iterrows():
                    records.append({
                        "date": str(row["date"]),
                        "close": round(float(row["close"]), 2),
                        "high": round(float(row["high"]), 2),
                        "low": round(float(row["low"]), 2),
                    })
                if len(records) >= 2:
                    latest = records[-1]["close"]
                    prev = records[-2]["close"]
                    ret = round((latest - prev) / prev * 100, 2) if prev else 0
                else:
                    ret = 0
                result[name] = {"recent_5d": records, "latest_ret_pct": ret}
        except Exception as e:
            logger.debug("[market] HK index %s failed: %s", symbol, e)

    return result


# ── 数据格式化 ──────────────────────────────────────────────────────

def _format_raw_data(data: dict) -> str:
    """将原始数据格式化为 AI 可读的文本。"""
    lines = [f"# 大盘原始数据（{data['date']}）\n"]

    # regime
    regime = data.get("regime", {})
    if regime:
        lines.append(f"当前市场环境判定：{regime.get('label', '未知')}\n")

    # A股指数
    a_idx = data.get("a_indices", {})
    if a_idx:
        lines.append("## A股主要指数（近10日行情）\n")
        for name, info in a_idx.items():
            lines.append(f"### {name}")
            lines.append(f"MA5={info['ma5']}, MA20={info['ma20']}, MA60={info['ma60']}")
            lines.append("日期 | 开盘 | 收盘 | 最高 | 最低 | 成交量 | 成交额")
            for r in info["recent_10d"]:
                lines.append(
                    f"{r['date']} | {r['open']:.2f} | {r['close']:.2f} | "
                    f"{r['high']:.2f} | {r['low']:.2f} | {r['volume']:.0f} | {r['amount']:.0f}"
                )
            lines.append("")

    # 成交额
    turnover = data.get("total_turnover_10d", [])
    if turnover:
        lines.append("## 两市总成交额（近10日，单位：亿元）")
        for t in turnover:
            lines.append(f"{t['date']}: {t['total_amount_yi']:.0f}亿")
        lines.append("")

    # 涨跌停
    breadth = data.get("breadth", {})
    if breadth:
        lines.append("## 市场广度")
        lines.append(f"涨停: {breadth.get('涨停', '?')}家, 跌停: {breadth.get('跌停', '?')}家")
        lines.append("")

    # 板块
    sectors = data.get("sectors", {})
    if sectors:
        lines.append("## 板块轮动")
        top5 = sectors.get("top5", [])
        if top5:
            lines.append("领涨Top5:")
            for s in top5:
                lines.append(f"  {s.get('板块名称', '?')}: {s.get('涨跌幅', 0):+.2f}%")
        bottom5 = sectors.get("bottom5", [])
        if bottom5:
            lines.append("领跌Top5:")
            for s in bottom5:
                lines.append(f"  {s.get('板块名称', '?')}: {s.get('涨跌幅', 0):+.2f}%")
        lines.append("")

    # 美股
    us = data.get("us_indices", {})
    if us:
        lines.append("## 美股三大指数（近5日）")
        for name, info in us.items():
            lines.append(f"### {name}（最新涨跌: {info['latest_ret_pct']:+.2f}%）")
            for r in info["recent_5d"]:
                lines.append(f"  {r['date']}: 收{r['close']:.2f} 开{r['open']:.2f} 高{r['high']:.2f} 低{r['low']:.2f}")
        lines.append("")

    # 港股
    hk = data.get("hk_indices", {})
    if hk:
        lines.append("## 港股指数（近5日）")
        for name, info in hk.items():
            lines.append(f"### {name}（最新涨跌: {info['latest_ret_pct']:+.2f}%）")
            for r in info["recent_5d"]:
                lines.append(f"  {r['date']}: 收{r['close']:.2f} 高{r['high']:.2f} 低{r['low']:.2f}")
        lines.append("")

    return "\n".join(lines)


# ── AI 分析 ─────────────────────────────────────────────────────────

def _run_ai_analysis(data_text: str, model_name: str) -> str:
    """调用 Opus 进行大盘深度分析。"""
    from ai.prompts_market import MARKET_ANALYSIS_SYSTEM
    from services.war_room import _call_single_model

    logger.info("[market] 调用 %s 进行大盘深度分析 (数据 %d 字)", model_name, len(data_text))
    result = _call_single_model(data_text, MARKET_ANALYSIS_SYSTEM, model_name, max_tokens=6000)
    return result


# ── 缓存 ───────────────────────────────────────────────────────────

def _save_cache(date: str, raw_data: dict, analysis_md: str):
    """持久化当日分析结果。"""
    MARKET_DIR.mkdir(parents=True, exist_ok=True)

    # JSON（结构化数据）
    json_path = MARKET_DIR / f"{date}.json"
    json_data = {
        "date": date,
        "timestamp": datetime.now().isoformat(),
        "raw_data": raw_data,
    }
    # raw_data 里的部分数据可能不是 JSON 可序列化的
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.warning("[market] JSON 缓存写入失败: %s", e)

    # Markdown（直接注入 prompt）
    md_path = MARKET_DIR / f"{date}.md"
    md_path.write_text(analysis_md, encoding="utf-8")
    logger.info("[market] 大盘分析已缓存: %s", md_path)


def _fallback_brief() -> str:
    """数据或 AI 分析失败时的精简 fallback。"""
    try:
        from data.macro_intel import get_macro_context
        full, brief = get_macro_context()
        return full if full else brief if brief else "（大盘数据采集失败，请以个股自身技术面为主要判断依据）"
    except Exception:
        return "（大盘数据采集失败，请以个股自身技术面为主要判断依据）"
