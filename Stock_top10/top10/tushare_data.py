"""Tushare 数据增强 — 批量获取 PE/PB/市值/行业/K线摘要"""

import logging
import pandas as pd
from utils.cache_compat import compat_cache
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from data.tushare_client import (
    get_pro, get_ts_error, to_ts_code,
    today as _today, ndays_ago as _ndays_ago,
    _retry_call as _retry,
)

logger = logging.getLogger(__name__)

_pro = get_pro()
_ts_err = get_ts_error()


def ts_ok() -> bool:
    return _pro is not None


def get_ts_status() -> str:
    return "Tushare 可用" if _pro else (_ts_err or "不可用")


@compat_cache(ttl=1800)
def _get_stock_industry() -> dict:
    if not _pro:
        return {}
    try:
        df = _retry(lambda: _pro.stock_basic(
            exchange="", list_status="L",
            fields="ts_code,symbol,name,industry"
        ))
        if df is not None and not df.empty:
            return dict(zip(df["symbol"], df["industry"]))
    except Exception as e:
        logger.debug("[_get_stock_industry] %s", e)
    return {}


@compat_cache(ttl=600)
def _get_daily_basic_batch() -> pd.DataFrame:
    if not _pro:
        return pd.DataFrame()
    for offset in range(5):
        trade_date = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            df = _retry(lambda td=trade_date: _pro.daily_basic(
                trade_date=td,
                fields="ts_code,close,pe_ttm,pb,total_mv,turnover_rate,volume_ratio"
            ))
            if df is not None and not df.empty:
                df["code6"] = df["ts_code"].str.split(".").str[0]
                return df
        except Exception as e:
            logger.debug("[_get_daily_basic_batch] offset=%d: %s", offset, e)
            continue
    return pd.DataFrame()


@compat_cache(ttl=600)
def _get_daily_batch() -> tuple[pd.DataFrame, str]:
    if not _pro:
        return pd.DataFrame(), ""
    for offset in range(5):
        trade_date = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            df = _retry(lambda td=trade_date: _pro.daily(trade_date=td))
            if df is not None and not df.empty:
                df["code6"] = df["ts_code"].str.split(".").str[0]
                return df, trade_date
        except Exception as e:
            logger.debug("[_get_daily_batch] offset=%d: %s", offset, e)
            continue
    return pd.DataFrame(), ""


@compat_cache(ttl=86400)
def _get_stock_names() -> dict:
    if not _pro:
        return {}
    try:
        df = _retry(lambda: _pro.stock_basic(
            exchange="", list_status="L", fields="symbol,name"
        ))
        if df is not None and not df.empty:
            return dict(zip(df["symbol"], df["name"]))
    except Exception as e:
        logger.debug("[_get_stock_names] %s", e)
    return {}


def _sv(v, d=2):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0
    return round(v, d)


def get_volume_rank_tushare(top_n: int) -> tuple[pd.DataFrame, str | None]:
    daily, td = _get_daily_batch()
    if daily.empty:
        return pd.DataFrame(), "Tushare 日线数据不可用"

    top = daily.sort_values("amount", ascending=False).head(top_n).reset_index(drop=True)
    names = _get_stock_names()
    basic = _get_daily_basic_batch()

    tr_map, vr_map, pe_map, mv_map = {}, {}, {}, {}
    if not basic.empty:
        bi = basic.set_index("code6")
        if "turnover_rate" in bi.columns: tr_map = bi["turnover_rate"].to_dict()
        if "volume_ratio" in bi.columns: vr_map = bi["volume_ratio"].to_dict()
        if "pe_ttm" in bi.columns: pe_map = bi["pe_ttm"].to_dict()
        if "total_mv" in bi.columns: mv_map = bi["total_mv"].to_dict()

    rows = []
    for i, (_, r) in enumerate(top.iterrows(), 1):
        code = r["code6"]
        amt = r.get("amount", 0) or 0
        mv = mv_map.get(code)
        rows.append({
            "排名": i, "代码": code,
            "股票名称": names.get(code, ""),
            "最新价": _sv(r.get("close")),
            "涨跌幅": _sv(r.get("pct_chg")),
            "成交额(亿)": round(amt / 1e5, 2),
            "换手率": _sv(tr_map.get(code)),
            "量比": _sv(vr_map.get(code)),
            "市盈率": _sv(pe_map.get(code)),
            "总市值(亿)": _sv(mv, 1) / 10000 if mv and pd.notna(mv) else 0,
            "主力净流入(万)": 0,
        })
    return pd.DataFrame(rows), None


def get_all_volume_data_tushare() -> pd.DataFrame:
    daily, td = _get_daily_batch()
    if daily.empty:
        return pd.DataFrame()

    daily = daily.sort_values("amount", ascending=False).reset_index(drop=True)
    basic = _get_daily_basic_batch()
    basic_idx = basic.set_index("code6") if not basic.empty else pd.DataFrame()

    result = pd.DataFrame({
        "代码": daily["code6"].values,
        "成交额排名_all": range(1, len(daily) + 1),
        "成交额(亿)_all": (daily["amount"].fillna(0) / 1e5).round(2).values,
    })

    if not basic_idx.empty:
        for src, dst in [("turnover_rate", "换手率_all"), ("volume_ratio", "量比_all")]:
            if src in basic_idx.columns:
                result[dst] = result["代码"].map(basic_idx[src].to_dict()).fillna(0).round(2)
            else:
                result[dst] = 0
    else:
        result["换手率_all"] = 0
        result["量比_all"] = 0

    result["主力净流入(万)_all"] = 0
    return result


def _get_kline_data(code6: str) -> pd.DataFrame:
    if not _pro:
        return pd.DataFrame()
    ts_code = to_ts_code(code6)
    try:
        df = _retry(lambda: _pro.daily(
            ts_code=ts_code,
            start_date=_ndays_ago(90),
            end_date=_today()
        ))
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.sort_values("trade_date").reset_index(drop=True)
        df = df.rename(columns={
            "trade_date": "日期", "open": "开盘", "high": "最高",
            "low": "最低", "close": "收盘", "vol": "成交量",
            "pct_chg": "涨跌幅", "amount": "成交额",
        })
        return df
    except Exception as e:
        logger.debug("[_get_kline_data] %s: %s", code6, e)
        return pd.DataFrame()


def enrich_candidates(df: pd.DataFrame, progress_callback=None) -> pd.DataFrame:
    if df.empty or not _pro:
        return df

    enriched = df.copy()
    codes = enriched["代码"].tolist()

    # 1. 批量：行业
    industry_map = _get_stock_industry()
    enriched["行业"] = enriched["代码"].map(industry_map)

    # 2. 批量：PE/PB/市值/换手率/量比
    basic_df = _get_daily_basic_batch()
    if not basic_df.empty:
        basic_indexed = basic_df.set_index("code6")
        for src_col, dst_col in [
            ("pe_ttm", "PE"),
            ("pb", "PB"),
            ("total_mv", "总市值(亿)"),
            ("turnover_rate", "换手率_ts"),
            ("volume_ratio", "量比_ts"),
        ]:
            if src_col in basic_indexed.columns:
                mapping = basic_indexed[src_col].to_dict()
                enriched[dst_col] = enriched["代码"].map(mapping)
        if "总市值(亿)" in enriched.columns:
            enriched["总市值(亿)"] = (enriched["总市值(亿)"] / 10000).round(1)
        if "换手率_ts" in enriched.columns:
            enriched["换手率"] = enriched["换手率_ts"].combine_first(
                enriched.get("换手率", pd.Series(dtype=float))
            )
            enriched.drop(columns=["换手率_ts"], inplace=True)
        if "量比_ts" in enriched.columns:
            enriched["量比"] = enriched["量比_ts"].combine_first(
                enriched.get("量比", pd.Series(dtype=float))
            )
            enriched.drop(columns=["量比_ts"], inplace=True)

    # 3. 行业PE/PB基准
    if progress_callback:
        progress_callback("正在计算行业估值基准...")
    industry_benchmarks = _get_industry_benchmarks(basic_df)
    if industry_benchmarks and "行业" in enriched.columns:
        enriched["行业PE均值"] = enriched["行业"].map(
            {k: v.get("pe_mean") for k, v in industry_benchmarks.items()})
        enriched["行业PB均值"] = enriched["行业"].map(
            {k: v.get("pb_mean") for k, v in industry_benchmarks.items()})

    # 4. 并行获取K线数据 + 技术指标
    if progress_callback:
        progress_callback("正在获取K线数据并计算技术指标...")

    from Stock_top10.top10.signal import compute_technicals, compute_quant_score, format_technicals_text

    kline_results = {}
    kline_dfs = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_get_kline_data, code): code for code in codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                kdf = future.result()
                kline_dfs[code] = kdf
                if not kdf.empty:
                    technicals = compute_technicals(kdf)
                    kline_results[code] = {
                        "summary": format_technicals_text(technicals) + "\n\n近5日行情：\n" +
                                   kdf.tail(5)[["日期", "收盘", "涨跌幅", "成交量"]].to_string(index=False),
                        "technicals": technicals,
                    }
                else:
                    kline_results[code] = {"summary": "", "technicals": {}}
            except Exception as e:
                logger.debug("[enrich_candidates] K线获取失败 %s: %s", code, e)
                kline_results[code] = {"summary": "", "technicals": {}}

    enriched["K线摘要"] = enriched["代码"].map(
        {k: v["summary"] for k, v in kline_results.items()})

    # 5. 量化预评分
    if progress_callback:
        progress_callback("正在计算量化预评分...")

    quant_scores = {}
    for _, row in enriched.iterrows():
        code = row["代码"]
        tech = kline_results.get(code, {}).get("technicals", {})
        qs = compute_quant_score(
            tech,
            pe=row.get("PE"),
            pb=row.get("PB"),
            net_flow_wan=row.get("主力净流入(万)"),
            volume_ratio=row.get("量比"),
            turnover_rate=row.get("换手率"),
        )
        quant_scores[code] = qs

    enriched["量化总分"] = enriched["代码"].map(
        {k: v["量化总分"] for k, v in quant_scores.items()})
    enriched["量化信号"] = enriched["代码"].map(
        {k: v["量化信号"] for k, v in quant_scores.items()})
    enriched["技术面分"] = enriched["代码"].map(
        {k: v["技术面分"] for k, v in quant_scores.items()})
    enriched["资金面分"] = enriched["代码"].map(
        {k: v["资金面分"] for k, v in quant_scores.items()})
    enriched["估值面分"] = enriched["代码"].map(
        {k: v["估值面分"] for k, v in quant_scores.items()})
    enriched["动量分"] = enriched["代码"].map(
        {k: v["动量分"] for k, v in quant_scores.items()})

    return enriched


@compat_cache(ttl=1800)
def _get_industry_benchmarks(basic_df: pd.DataFrame = None) -> dict:
    if basic_df is None or basic_df.empty:
        return {}
    try:
        industry_map = _get_stock_industry()
        if not industry_map:
            return {}
        basic_df = basic_df.copy()
        basic_df["industry"] = basic_df["code6"].map(industry_map)
        basic_df = basic_df.dropna(subset=["industry"])

        result = {}
        for industry, group in basic_df.groupby("industry"):
            pe_vals = group["pe_ttm"].dropna()
            pb_vals = group["pb"].dropna()
            pe_vals = pe_vals[(pe_vals > 0) & (pe_vals < 300)]
            pb_vals = pb_vals[(pb_vals > 0) & (pb_vals < 50)]
            if len(pe_vals) >= 5:
                result[industry] = {
                    "pe_mean": round(float(pe_vals.median()), 1),
                    "pb_mean": round(float(pb_vals.median()), 2) if len(pb_vals) >= 5 else None,
                }
        return result
    except Exception as e:
        logger.debug("[_get_industry_benchmarks] %s", e)
        return {}


@compat_cache(ttl=1800)
def get_sector_rotation() -> dict:
    result = {"概念板块": [], "行业板块": []}
    try:
        import akshare as ak
        _EXCLUDE_KEYWORDS = [
            "连板", "打板", "竞价", "涨停", "跌停", "首板", "二板", "三板",
            "昨日", "炸板", "地天板", "天地板", "反包", "断板", "空间板",
        ]
        df_concept = ak.stock_board_concept_name_em()
        if df_concept is not None and not df_concept.empty:
            if "涨跌幅" in df_concept.columns and "板块名称" in df_concept.columns:
                df_concept["涨跌幅"] = pd.to_numeric(df_concept["涨跌幅"], errors="coerce")
                mask = df_concept["板块名称"].apply(
                    lambda x: not any(kw in str(x) for kw in _EXCLUDE_KEYWORDS)
                )
                df_filtered = df_concept[mask]
                top5 = df_filtered.nlargest(5, "涨跌幅")
                for _, row in top5.iterrows():
                    name = row.get("板块名称", "")
                    chg = row.get("涨跌幅", 0)
                    result["概念板块"].append(f"{name}({chg:+.2f}%)")

        df_industry = ak.stock_board_industry_name_em()
        if df_industry is not None and not df_industry.empty:
            if "涨跌幅" in df_industry.columns:
                df_industry["涨跌幅"] = pd.to_numeric(df_industry["涨跌幅"], errors="coerce")
                top5 = df_industry.nlargest(5, "涨跌幅")
                for _, row in top5.iterrows():
                    name = row.get("板块名称", "")
                    chg = row.get("涨跌幅", 0)
                    result["行业板块"].append(f"{name}({chg:+.2f}%)")
    except Exception as e:
        logger.debug("[get_sector_rotation] %s", e)
    return result
