"""数据获取 — 东财人气榜 + 雪球热门 + 成交额榜"""

import logging
import pandas as pd
from utils.cache_compat import compat_cache

logger = logging.getLogger(__name__)


@compat_cache(ttl=1800)
def get_hot_rank(top_n: int = 100) -> tuple[pd.DataFrame, str | None]:
    """东财人气榜"""
    try:
        import akshare as ak
        df = ak.stock_hot_rank_em()
        if df is None or df.empty:
            return pd.DataFrame(), "人气榜数据为空"
        df = df.head(top_n)
        df.columns = ["排名", "代码", "股票名称", "最新价", "涨跌额", "涨跌幅"]
        df["代码"] = df["代码"].str.replace(r"^(SH|SZ|BJ)", "", regex=True)
        return df, None
    except Exception as e:
        return pd.DataFrame(), f"人气榜获取失败：{e}"


@compat_cache(ttl=1800)
def get_xueqiu_hot(top_n: int = 50) -> tuple[pd.DataFrame, str | None]:
    """雪球关注热度 Top N"""
    try:
        import akshare as ak
        df = ak.stock_hot_follow_xq()
        if df is None or df.empty:
            return pd.DataFrame(), "雪球热门数据为空"
        df = df.head(top_n).copy()
        df.columns = df.columns[:4]  # 取前4列，不管名字
        df.columns = ["代码", "股票名称", "关注人数", "最新价"]
        df["代码"] = df["代码"].astype(str).str.replace(r"^(SH|SZ|BJ)", "", regex=True)
        df["排名"] = range(1, len(df) + 1)
        logger.info("[xueqiu] 获取雪球热门 %d 只", len(df))
        return df, None
    except Exception as e:
        return pd.DataFrame(), f"雪球热门获取失败：{e}"


@compat_cache(ttl=1800)
def get_volume_rank(top_n: int = 100) -> tuple[pd.DataFrame, str | None]:
    # 优先 Tushare
    try:
        from Stock_top10.top10.tushare_data import get_volume_rank_tushare, ts_ok
        if ts_ok():
            df, err = get_volume_rank_tushare(top_n)
            if err is None and not df.empty:
                return df, None
    except Exception:
        pass

    # 备选：东方财富 HTTP 接口
    try:
        return _get_volume_rank_eastmoney(top_n)
    except Exception:
        return _get_volume_rank_akshare(top_n)


def _get_volume_rank_eastmoney(top_n: int) -> tuple[pd.DataFrame, str | None]:
    import requests as req
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1, "pz": top_n, "po": 1,
        "np": 1, "fltt": 2, "invt": 2,
        "fid": "f6",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "fields": "f2,f3,f5,f6,f8,f9,f10,f12,f14,f20,f23,f62",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    resp = req.get(url, params=params, timeout=15)
    data = resp.json().get("data", {})
    items = data.get("diff", [])
    if not items:
        return pd.DataFrame(), "成交额榜数据为空"

    def _safe_num(v, default=0):
        if v is None or v == "-" or isinstance(v, str):
            return default
        return v

    rows = []
    for i, item in enumerate(items, 1):
        net_flow = _safe_num(item.get("f62", 0))
        mkt_cap = _safe_num(item.get("f20", 0))
        rows.append({
            "排名": i,
            "代码": item.get("f12", ""),
            "股票名称": item.get("f14", ""),
            "最新价": item.get("f2", 0),
            "涨跌幅": item.get("f3", 0),
            "成交额(亿)": round(_safe_num(item.get("f6", 0)) / 1e8, 2),
            "换手率": _safe_num(item.get("f8", 0)),
            "量比": _safe_num(item.get("f10", 0)),
            "市盈率": _safe_num(item.get("f9", 0)),
            "总市值(亿)": round(mkt_cap / 1e8, 1) if mkt_cap else 0,
            "主力净流入(万)": round(net_flow / 1e4, 2) if net_flow else 0,
        })
    return pd.DataFrame(rows), None


def _get_volume_rank_akshare(top_n: int) -> tuple[pd.DataFrame, str | None]:
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return pd.DataFrame(), "全量行情获取失败"
        df = df.sort_values("成交额", ascending=False).head(top_n).reset_index(drop=True)
        result = pd.DataFrame({
            "排名": range(1, len(df) + 1),
            "代码": df["代码"].values,
            "股票名称": df["名称"].values,
            "最新价": df["最新价"].values,
            "涨跌幅": df["涨跌幅"].values,
            "成交额(亿)": (df["成交额"] / 1e8).round(2).values,
        })
        return result, None
    except Exception as e:
        return pd.DataFrame(), f"成交额榜获取失败（备用方案也失败）：{e}"


# ══════════════════════════════════════════════════════════════════════════════
# v3.0 新增数据源
# ══════════════════════════════════════════════════════════════════════════════

@compat_cache(ttl=1800)
def get_capital_anomaly(top_n: int = 50) -> tuple[pd.DataFrame, str | None]:
    """主力资金异动 Top N — 捕捉尚未上热搜但资金先手的黑马"""
    try:
        import akshare as ak
        df = ak.stock_individual_fund_flow_rank(indicator="今日")
        if df is None or df.empty:
            return pd.DataFrame(), "资金异动数据为空"
        flow_col = [c for c in df.columns if "主力净流入" in str(c) and "净占比" not in str(c)]
        if not flow_col:
            flow_col = [c for c in df.columns if "净额" in str(c)]
        if flow_col:
            df[flow_col[0]] = pd.to_numeric(df[flow_col[0]], errors="coerce")
            df = df.sort_values(flow_col[0], ascending=False).head(top_n)
        else:
            df = df.head(top_n)

        code_col = [c for c in df.columns if "代码" in str(c)][0]
        name_col = [c for c in df.columns if "名称" in str(c)][0]
        price_col = [c for c in df.columns if "最新价" in str(c)]

        result = pd.DataFrame({
            "代码": df[code_col].values,
            "股票名称": df[name_col].values,
            "最新价": df[price_col[0]].values if price_col else 0,
            "涨跌幅": pd.to_numeric(
                df["涨跌幅"] if "涨跌幅" in df.columns else 0,
                errors="coerce"
            ).values,
            "排名": range(1, len(df) + 1),
        })
        result["代码"] = result["代码"].astype(str).str.replace(r"^(SH|SZ|BJ)", "", regex=True)
        logger.info("[capital_anomaly] 获取主力资金异动 %d 只", len(result))
        return result, None
    except Exception as e:
        logger.debug("[capital_anomaly] 获取失败: %s", e)
        return pd.DataFrame(), f"资金异动获取失败：{e}"


@compat_cache(ttl=1800)
def get_limit_up_pool() -> tuple[pd.DataFrame, str | None]:
    """涨停池 — 捕捉涨停/连板强势股"""
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=pd.Timestamp.now().strftime("%Y%m%d"))
        if df is None or df.empty:
            return pd.DataFrame(), "涨停池数据为空"

        code_col = [c for c in df.columns if "代码" in str(c)][0]
        name_col = [c for c in df.columns if "名称" in str(c)][0]
        price_col = [c for c in df.columns if "最新价" in str(c)]
        lb_col = [c for c in df.columns if "连板" in str(c)]

        result = pd.DataFrame({
            "代码": df[code_col].values,
            "股票名称": df[name_col].values,
            "最新价": df[price_col[0]].values if price_col else 0,
            "涨跌幅": pd.to_numeric(
                df["涨跌幅"] if "涨跌幅" in df.columns else 0,
                errors="coerce"
            ).values,
            "连板天数": df[lb_col[0]].values if lb_col else 1,
            "排名": range(1, len(df) + 1),
        })
        result["代码"] = result["代码"].astype(str).str.replace(r"^(SH|SZ|BJ)", "", regex=True)
        logger.info("[limit_up_pool] 获取涨停池 %d 只", len(result))
        return result, None
    except Exception as e:
        logger.debug("[limit_up_pool] 获取失败: %s", e)
        return pd.DataFrame(), f"涨停池获取失败：{e}"


def merge_candidates(hot_df: pd.DataFrame, vol_df: pd.DataFrame,
                     xq_df: pd.DataFrame = None,
                     capital_df: pd.DataFrame = None,
                     zt_df: pd.DataFrame = None) -> pd.DataFrame:
    """合并东财人气榜 + 成交额榜 + 雪球热门 + 资金异动 + 涨停池，去重后返回"""
    if xq_df is None:
        xq_df = pd.DataFrame()
    if capital_df is None:
        capital_df = pd.DataFrame()
    if zt_df is None:
        zt_df = pd.DataFrame()

    # 收集所有来源
    parts = []
    seen_codes = set()

    # 1) 东财人气榜
    if not hot_df.empty:
        h = hot_df[["代码", "股票名称", "最新价", "涨跌幅"]].copy()
        h["人气排名"] = hot_df["排名"]
        h["来源"] = "东财人气"
        parts.append(h)
        seen_codes.update(h["代码"].tolist())

    # 2) 雪球热门
    if not xq_df.empty:
        x = xq_df[["代码", "股票名称", "最新价"]].copy()
        x["涨跌幅"] = 0.0
        x["雪球排名"] = xq_df["排名"]
        # 已有的标记为多榜
        already = x["代码"].isin(seen_codes)
        new_only = x[~already].copy()
        if not new_only.empty:
            new_only["来源"] = "雪球热门"
            parts.append(new_only)
            seen_codes.update(new_only["代码"].tolist())
        # 给已有的补上雪球排名
        if already.any():
            xq_rank_map = dict(zip(xq_df["代码"], xq_df["排名"]))
            for p in parts:
                mask = p["代码"].isin(xq_rank_map)
                if mask.any():
                    p.loc[mask, "雪球排名"] = p.loc[mask, "代码"].map(xq_rank_map)

    # 3) v3.0：主力资金异动
    if not capital_df.empty:
        ca = capital_df[["代码", "股票名称", "最新价", "涨跌幅"]].copy()
        ca["来源"] = "资金异动"
        already = ca["代码"].isin(seen_codes)
        new_only = ca[~already].copy()
        if not new_only.empty:
            parts.append(new_only)
            seen_codes.update(new_only["代码"].tolist())
        # 已有的标记多源
        for p in parts:
            mask = p["代码"].isin(ca["代码"]) & (p["代码"].isin(seen_codes - set(new_only["代码"].tolist())))
            if mask.any():
                p.loc[mask, "来源"] = p.loc[mask, "来源"].astype(str) + "+资金"

    # 4) v3.0：涨停池
    if not zt_df.empty:
        zt = zt_df[["代码", "股票名称", "最新价", "涨跌幅"]].copy()
        if "连板天数" in zt_df.columns:
            zt["连板天数"] = zt_df["连板天数"]
        zt["来源"] = "涨停池"
        already = zt["代码"].isin(seen_codes)
        new_only = zt[~already].copy()
        if not new_only.empty:
            parts.append(new_only)
            seen_codes.update(new_only["代码"].tolist())

    # 5) 成交额榜
    if not vol_df.empty:
        v = vol_df[["代码", "股票名称", "最新价", "涨跌幅"]].copy()
        v["成交额排名"] = vol_df["排名"]
        if "成交额(亿)" in vol_df.columns:
            v["成交额(亿)"] = vol_df["成交额(亿)"]
        for col in ["换手率", "量比", "市盈率", "总市值(亿)", "主力净流入(万)"]:
            if col in vol_df.columns:
                v[col] = vol_df[col]

        already = v["代码"].isin(seen_codes)
        new_only = v[~already].copy()
        if not new_only.empty:
            new_only["来源"] = "成交额榜"
            parts.append(new_only)

        # 给已有的补上成交额数据
        v_indexed = v.set_index("代码")
        for p in parts:
            for col in ["成交额排名", "成交额(亿)", "换手率", "量比", "市盈率",
                        "总市值(亿)", "主力净流入(万)"]:
                if col in v_indexed.columns and col not in p.columns:
                    p[col] = None
                if col in v_indexed.columns:
                    mask = p["代码"].isin(v_indexed.index) & (p[col].isna() if col in p.columns else True)
                    if mask.any():
                        p.loc[mask, col] = p.loc[mask, "代码"].map(v_indexed[col].to_dict())

    if not parts:
        return pd.DataFrame()

    merged = pd.concat(parts, ignore_index=True)

    # 标记多榜命中
    for _, row in merged.iterrows():
        sources = []
        if pd.notna(row.get("人气排名")):
            sources.append("东财")
        if pd.notna(row.get("雪球排名")):
            sources.append("雪球")
        if pd.notna(row.get("成交额排名")):
            sources.append("成交额")
        if len(sources) > 1:
            merged.loc[merged["代码"] == row["代码"], "来源"] = "+".join(sources)

    merged = _fill_volume_data(merged)

    for col in ["最新价", "涨跌幅", "成交额(亿)", "换手率", "量比",
                "市盈率", "总市值(亿)", "主力净流入(万)"]:
        if col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors="coerce").round(2)

    for col in ["人气排名", "成交额排名", "雪球排名"]:
        if col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors="coerce")

    # ── 风险过滤 ──────────────────────────────────────────────────────
    merged = _apply_risk_filters(merged)

    return merged.reset_index(drop=True)


def _apply_risk_filters(df: pd.DataFrame) -> pd.DataFrame:
    """过滤不适合推荐的高风险标的"""
    if df.empty:
        return df

    before = len(df)

    # 1. 市值过滤：v3.0 软化 — 仅排除 <10亿的极微盘，10-30亿由 signal.py 估值分软惩罚
    if "总市值(亿)" in df.columns:
        df = df[~((df["总市值(亿)"].notna()) & (df["总市值(亿)"] < 10))]

    # 2. 流动性过滤：排除成交额 < 1 亿的低流动性标的
    if "成交额(亿)" in df.columns:
        df = df[~((df["成交额(亿)"].notna()) & (df["成交额(亿)"] < 1))]

    # 3. 股价过滤：排除 < 2 元的低价股（ST/退市风险）
    if "最新价" in df.columns:
        df = df[~((df["最新价"].notna()) & (df["最新价"] < 2))]

    # 4. 连续暴涨过滤：涨幅 > 15% 的标的标记高追风险
    #    （不删除，但追加风险标记供后续评分参考）
    if "涨跌幅" in df.columns:
        df["_追高风险"] = (df["涨跌幅"].fillna(0) > 15)

    # 5. 代码过滤：排除 ST/*ST（代码以 ST 开头的名称）
    if "股票名称" in df.columns:
        df = df[~df["股票名称"].astype(str).str.contains(r"^(\*?ST|S )", regex=True, na=False)]

    # 6. 板块集中度限制：同行业最多保留 3 只（需要行业数据，若无则跳过）
    # 此项需要外部行业数据，暂不实现硬过滤，留给 AI 评分阶段处理

    filtered = before - len(df)
    if filtered > 0:
        logger.info("[risk_filter] 过滤了 %d 只高风险标的（剩余 %d 只）", filtered, len(df))

    return df


@compat_cache(ttl=1800)
def _get_all_volume_data() -> pd.DataFrame:
    try:
        from Stock_top10.top10.tushare_data import get_all_volume_data_tushare, ts_ok
        if ts_ok():
            df = get_all_volume_data_tushare()
            if not df.empty:
                return df
    except Exception:
        pass
    return _get_all_volume_data_eastmoney()


def _get_all_volume_data_eastmoney() -> pd.DataFrame:
    try:
        import requests as req
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1, "pz": 5000, "po": 1,
            "np": 1, "fltt": 2, "invt": 2,
            "fid": "f6",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f2,f3,f6,f8,f10,f12,f62",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        resp = req.get(url, params=params, timeout=15)
        items = resp.json().get("data", {}).get("diff", [])
        if not items:
            return pd.DataFrame()

        def _safe(v):
            if v is None or v == "-" or isinstance(v, str):
                return 0
            return v

        rows = []
        for i, item in enumerate(items, 1):
            code = item.get("f12", "")
            if not code:
                continue
            f6 = _safe(item.get("f6", 0))
            f62 = _safe(item.get("f62", 0))
            rows.append({
                "代码": code,
                "成交额排名_all": i,
                "成交额(亿)_all": round(f6 / 1e8, 2) if f6 else 0,
                "换手率_all": round(_safe(item.get("f8", 0)), 2),
                "量比_all": round(_safe(item.get("f10", 0)), 2),
                "主力净流入(万)_all": round(f62 / 1e4, 2) if f62 else 0,
            })
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def _fill_volume_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    all_vol = _get_all_volume_data()
    if all_vol.empty:
        return df

    vol_indexed = all_vol.set_index("代码")

    if "成交额排名" not in df.columns:
        df["成交额排名"] = None
    mask_no_rank = df["成交额排名"].isna()
    if mask_no_rank.any():
        df.loc[mask_no_rank, "成交额排名"] = df.loc[mask_no_rank, "代码"].map(
            vol_indexed["成交额排名_all"].to_dict()
        )

    for col, src_col in [("成交额(亿)", "成交额(亿)_all"),
                          ("换手率", "换手率_all"),
                          ("量比", "量比_all"),
                          ("主力净流入(万)", "主力净流入(万)_all")]:
        if col not in df.columns:
            df[col] = None
        mask = df[col].isna() | (df[col] == 0)
        if mask.any():
            df.loc[mask, col] = df.loc[mask, "代码"].map(
                vol_indexed[src_col].to_dict()
            )

    return df
