"""
📐 历史相似走势匹配算法 v3
五维 K 线特征：涨跌幅形态 + 成交量节奏 + 振幅 + 上影线 + 下影线
新增：全样本胜率统计 + 最大回撤/最大涨幅追踪
"""

import numpy as np
import pandas as pd
import os

from utils.cache_compat import compat_cache

HISTORY_DIR = os.path.join(os.path.dirname(__file__), "history")
# 兼容旧的单文件和新的拆分文件
HISTORY_FILE = os.path.join(HISTORY_DIR, "all_daily.parquet")

# ══════════════════════════════════════════════════════════════════════════════
# 五维权重配置
# ══════════════════════════════════════════════════════════════════════════════

WEIGHTS = {
    "pct_chg":      0.35,   # 涨跌幅形态
    "amplitude":    0.25,   # 振幅（实体大小）
    "vol_chg":      0.20,   # 成交量变化率
    "upper_shadow": 0.10,   # 上影线比例
    "lower_shadow": 0.10,   # 下影线比例
}


@compat_cache(ttl=86400, show_spinner=False)
def load_history() -> pd.DataFrame:
    """加载全市场历史日线数据（支持单文件或拆分文件）"""
    # 优先：拆分的 part 文件（适合 GitHub 部署）
    import glob
    parts = sorted(glob.glob(os.path.join(HISTORY_DIR, "all_daily_part*.parquet")))
    if parts:
        dfs = [pd.read_parquet(p) for p in parts]
        df = pd.concat(dfs, ignore_index=True)
        df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        return df
    # 兜底：单个大文件（本地开发用）
    if os.path.exists(HISTORY_FILE):
        df = pd.read_parquet(HISTORY_FILE)
        df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        return df
    return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
# 特征提取
# ══════════════════════════════════════════════════════════════════════════════

def _calc_amplitude(open_arr, high_arr, low_arr):
    """振幅 = (最高 - 最低) / 开盘，衡量K线实体大小"""
    return (high_arr - low_arr) / (open_arr + 1e-10)


def _calc_upper_shadow(open_arr, high_arr, close_arr, low_arr):
    """上影线比例 = (最高 - max(开,收)) / (最高 - 最低)"""
    body_top = np.maximum(open_arr, close_arr)
    hl_range = high_arr - low_arr + 1e-10
    return (high_arr - body_top) / hl_range


def _calc_lower_shadow(open_arr, high_arr, close_arr, low_arr):
    """下影线比例 = (min(开,收) - 最低) / (最高 - 最低)"""
    body_bottom = np.minimum(open_arr, close_arr)
    hl_range = high_arr - low_arr + 1e-10
    return (body_bottom - low_arr) / hl_range


def _calc_vol_change(vol_arr):
    """成交量变化率 = vol[i]/vol[i-1] - 1，第一天填0"""
    vol_chg = np.zeros(len(vol_arr))
    vol_chg[1:] = np.diff(vol_arr) / (vol_arr[:-1] + 1e-10)
    return np.clip(vol_chg, -5, 5)


def extract_features_from_target(df: pd.DataFrame, k_days: int) -> dict[str, np.ndarray] | None:
    """
    从目标股票 DataFrame 提取五维特征（中文列名）
    返回 {"pct_chg": array, "amplitude": array, ...} 或 None
    """
    if len(df) < k_days:
        return None

    recent = df.tail(k_days)
    o = recent["开盘"].values.astype(np.float64)
    h = recent["最高"].values.astype(np.float64)
    l = recent["最低"].values.astype(np.float64)
    c = recent["收盘"].values.astype(np.float64)
    v = recent["成交量"].values.astype(np.float64)

    return {
        "pct_chg":      recent["涨跌幅"].values.astype(np.float64),
        "amplitude":    _calc_amplitude(o, h, l),
        "vol_chg":      _calc_vol_change(v),
        "upper_shadow": _calc_upper_shadow(o, h, c, l),
        "lower_shadow": _calc_lower_shadow(o, h, c, l),
    }


def extract_all_features_for_stock(grp: pd.DataFrame) -> dict[str, np.ndarray]:
    """
    从单只股票完整日线提取五维特征序列（英文列名，历史数据）
    返回 {"pct_chg": array(N,), "amplitude": array(N,), ...}
    """
    o = grp["open"].values.astype(np.float64)
    h = grp["high"].values.astype(np.float64)
    l = grp["low"].values.astype(np.float64)
    c = grp["close"].values.astype(np.float64)
    v = grp["vol"].values.astype(np.float64)

    return {
        "pct_chg":      grp["pct_chg"].values.astype(np.float64),
        "amplitude":    _calc_amplitude(o, h, l),
        "vol_chg":      _calc_vol_change(v),
        "upper_shadow": _calc_upper_shadow(o, h, c, l),
        "lower_shadow": _calc_lower_shadow(o, h, c, l),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 相似度计算
# ══════════════════════════════════════════════════════════════════════════════

def _pearson_batch(target: np.ndarray, windows: np.ndarray) -> np.ndarray:
    """
    批量皮尔逊相关：target(K,) vs windows(N, K) → corrs(N,)
    """
    K = len(target)
    if K < 3:
        return np.zeros(len(windows))

    t_mean = target.mean()
    t_std  = target.std()
    if t_std < 1e-10:
        return np.zeros(len(windows))

    w_mean = windows.mean(axis=1, keepdims=True)
    w_std  = windows.std(axis=1)

    valid = w_std > 1e-10
    corrs = np.zeros(len(windows))

    if valid.any():
        t_norm = target - t_mean
        w_norm = windows[valid] - w_mean[valid]
        cov = (w_norm * t_norm).sum(axis=1) / K
        corrs[valid] = cov / (t_std * w_std[valid])

    return corrs


def _weighted_similarity(target_feats: dict, stock_feats: dict, k_days: int) -> np.ndarray:
    """
    计算五维加权相似度
    target_feats: {"pct_chg": (K,), ...}
    stock_feats:  {"pct_chg": (N,), ...}
    返回: (n_windows,) 相似度数组
    """
    n = len(stock_feats["pct_chg"])
    n_windows = n - k_days + 1
    if n_windows <= 0:
        return np.array([])

    total_sim = np.zeros(n_windows)

    for feat_name, weight in WEIGHTS.items():
        target_seq = target_feats[feat_name]
        full_seq   = stock_feats[feat_name]
        # 滑窗
        windows = np.lib.stride_tricks.sliding_window_view(full_seq, k_days)
        corr = _pearson_batch(target_seq, windows)
        total_sim += weight * corr

    return total_sim


# ══════════════════════════════════════════════════════════════════════════════
# 主搜索函数
# ══════════════════════════════════════════════════════════════════════════════

def find_similar(
    target_df: pd.DataFrame,
    k_days: int = 5,
    top_n: int = 3,
    context_days: int = 10,
    exclude_code: str = "",
    exclude_recent_days: int = 60,
    progress_callback=None,
) -> list[dict]:
    """
    全市场搜索与 target_df 最近 k_days 走势最相似的 Top N 案例

    五维特征匹配：涨跌幅 + 振幅 + 成交量节奏 + 上影线 + 下影线

    参数:
        target_df:   目标股票日线 DataFrame（中文列名：开盘/最高/最低/收盘/成交量/涨跌幅）
        k_days:      匹配窗口天数
        top_n:       返回前 N 个匹配
        context_days: 匹配段前后各展示多少天
        exclude_code: 排除的股票代码（避免自匹配）
        exclude_recent_days: 排除最近 N 天的数据

    返回:
        [{"ts_code", "similarity", "match_start_date", "match_end_date",
          "subsequent_return", "context_df", "feature_detail"}, ...]
    """
    history = load_history()
    if history.empty:
        return []

    # ── 加载股票名称映射 ────────────────────────────────────────────────
    try:
        from data.tushare_client import load_stock_list
        sl, _ = load_stock_list()
        name_map = dict(zip(sl["ts_code"], sl["name"])) if not sl.empty else {}
    except Exception:
        name_map = {}

    # ── 提取目标五维特征 ──────────────────────────────────────────────────
    target_feats = extract_features_from_target(target_df, k_days)
    if target_feats is None:
        return []

    # ── 排除日期阈值 ──────────────────────────────────────────────────────
    if exclude_recent_days > 0:
        cutoff_date = pd.to_datetime(target_df["日期"].max()) - pd.Timedelta(days=exclude_recent_days)
        cutoff_val = float(cutoff_date.strftime("%Y%m%d"))
    else:
        cutoff_val = 99999999.0

    all_candidates = []

    # ── 预过滤：排除 pct_chg 标准差差异 > 2x 的股票（Phase 3.2）──────
    target_pct_std = target_feats["pct_chg"].std()

    # ── 按股票分组搜索 ────────────────────────────────────────────────────
    groups = list(history.groupby("ts_code"))
    total_stocks = len(groups)
    for idx_stock, (code, group) in enumerate(groups):
        if progress_callback and idx_stock % 200 == 0:
            progress_callback(idx_stock, total_stocks)
        if code == exclude_code:
            continue

        grp = group.sort_values("trade_date").reset_index(drop=True)

        if len(grp) < k_days + context_days:
            continue

        # 预过滤：pct_chg 标准差差异太大的直接跳过
        stock_pct_std = grp["pct_chg"].std()
        if target_pct_std > 0 and stock_pct_std > 0:
            ratio = max(stock_pct_std, target_pct_std) / min(stock_pct_std, target_pct_std)
            if ratio > 2.0:
                continue

        dates = grp["trade_date"].values

        # 提取该股票的五维特征
        stock_feats = extract_all_features_for_stock(grp)

        # 计算五维加权相似度
        similarity = _weighted_similarity(target_feats, stock_feats, k_days)

        if len(similarity) == 0:
            continue

        # 排除最近数据
        window_end_dates = dates[k_days - 1:]
        date_mask = window_end_dates < cutoff_val

        if not date_mask.any():
            continue

        similarity[~date_mask] = -999

        # 找本股票内最佳匹配
        best_idx = int(np.argmax(similarity))
        best_sim = similarity[best_idx]

        if best_sim < 0.45:
            continue

        # ── 提取上下文 K 线 ───────────────────────────────────────────────
        match_start = best_idx
        match_end   = best_idx + k_days - 1

        ctx_start = max(0, match_start - context_days)
        ctx_end   = min(len(grp) - 1, match_end + context_days)

        ctx_df = grp.iloc[ctx_start:ctx_end + 1].copy()
        ctx_df["is_match"] = False
        match_slice = slice(match_start - ctx_start, match_end - ctx_start + 1)
        ctx_df.iloc[match_slice, ctx_df.columns.get_loc("is_match")] = True

        # 后续涨跌幅 + 最大回撤 + 最大涨幅
        if match_end + 1 < len(grp):
            future_end = min(match_end + context_days, len(grp) - 1)
            match_close  = grp.iloc[match_end]["close"]
            future_close = grp.iloc[future_end]["close"]
            subsequent_ret = (future_close / match_close - 1) * 100

            # 后续期间内每日收盘价序列
            future_closes = grp.iloc[match_end:future_end + 1]["close"].values.astype(np.float64)
            future_returns = (future_closes / match_close - 1) * 100
            max_drawdown = round(float(future_returns.min()), 2)
            max_gain = round(float(future_returns.max()), 2)
        else:
            subsequent_ret = None
            max_drawdown = None
            max_gain = None

        # ── 五维分项得分（展示用）──────────────────────────────────────────
        detail = {}
        for feat_name, weight in WEIGHTS.items():
            t_seq = target_feats[feat_name]
            s_seq = stock_feats[feat_name]
            s_window = s_seq[match_start:match_end + 1]
            if len(s_window) == len(t_seq):
                t_std = t_seq.std()
                s_std = s_window.std()
                if t_std > 1e-10 and s_std > 1e-10:
                    corr_val = np.corrcoef(t_seq, s_window)[0, 1]
                else:
                    corr_val = 0.0
                detail[feat_name] = round(corr_val * 100, 1)
            else:
                detail[feat_name] = 0.0

        # ── 聚合：K线匹配度 & 成交量匹配度 ─────────────────────────────
        # K线匹配度 = 涨跌幅 + 振幅 + 上影线 + 下影线 的加权平均
        kline_w = {"pct_chg": 0.35, "amplitude": 0.25, "upper_shadow": 0.20, "lower_shadow": 0.20}
        kline_sim = sum(detail.get(k, 0) * w for k, w in kline_w.items())
        # 成交量匹配度 = vol_chg 的相关性
        vol_sim = detail.get("vol_chg", 0.0)

        # 股票名称
        stock_name = name_map.get(code, "")

        all_candidates.append({
            "ts_code":           code,
            "stock_name":        stock_name,
            "similarity":        round(best_sim * 100, 1),
            "kline_similarity":  round(kline_sim, 1),
            "vol_similarity":    round(vol_sim, 1),
            "match_start_date":  grp.iloc[match_start]["trade_date"],
            "match_end_date":    grp.iloc[match_end]["trade_date"],
            "subsequent_return": round(subsequent_ret, 2) if subsequent_ret is not None else None,
            "max_drawdown":      max_drawdown,
            "max_gain":          max_gain,
            "context_df":        ctx_df,
            "feature_detail":    detail,
        })

    if progress_callback:
        progress_callback(total_stocks, total_stocks)

    # ── 排序取 Top N（去重：同一只股票只保留最佳）─────────────────────────
    all_candidates.sort(key=lambda x: x["similarity"], reverse=True)

    seen_codes = set()
    results = []
    for c in all_candidates:
        if c["ts_code"] not in seen_codes:
            seen_codes.add(c["ts_code"])
            results.append(c)
        if len(results) >= top_n:
            break

    # ── 全样本胜率统计（基于所有超过阈值的匹配）──────────────────────
    all_returns = [c["subsequent_return"] for c in all_candidates
                   if c["subsequent_return"] is not None]
    if all_returns:
        returns_arr = np.array(all_returns)
        match_stats = {
            "total_matches": len(all_candidates),
            "win_rate_10d": round(float((returns_arr > 0).sum() / len(returns_arr) * 100), 1),
            "avg_return_10d": round(float(returns_arr.mean()), 2),
            "median_return_10d": round(float(np.median(returns_arr)), 2),
        }
    else:
        match_stats = {
            "total_matches": len(all_candidates),
            "win_rate_10d": 0.0,
            "avg_return_10d": 0.0,
            "median_return_10d": 0.0,
        }

    # 将统计信息附加到每个结果
    for r in results:
        r["match_stats"] = match_stats

    return results
