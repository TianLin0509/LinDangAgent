"""量化初筛 — 过滤不适合短线的标的"""

import pandas as pd
from core.cache_compat import compat_cache


@compat_cache(ttl=86400)
def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    filtered = df.copy()

    # 1. 排除 ST / *ST / 退市股
    mask_st = filtered["股票名称"].str.contains(r"ST|退市", case=False, na=False)
    filtered = filtered[~mask_st]

    # 2. 排除北交所 (代码8开头)
    mask_bj = filtered["代码"].str.startswith("8")
    filtered = filtered[~mask_bj]

    # 3. 排除跌停股
    if "涨跌幅" in filtered.columns:
        filtered["涨跌幅"] = pd.to_numeric(filtered["涨跌幅"], errors="coerce")
        is_20pct = (filtered["代码"].str.startswith("688") |
                    filtered["代码"].str.startswith("300"))
        mask_down = ((is_20pct & (filtered["涨跌幅"] < -19.5)) |
                     (~is_20pct & (filtered["涨跌幅"] < -9.5)))
        filtered = filtered[~mask_down]

    # 4. 排除价格过低 (< 2元)
    if "最新价" in filtered.columns:
        filtered["最新价"] = pd.to_numeric(filtered["最新价"], errors="coerce")
        mask_low = filtered["最新价"] < 2
        filtered = filtered[~mask_low]

    filtered = filtered.reset_index(drop=True)
    return filtered


def get_filter_summary(before: int, after: int) -> str:
    removed = before - after
    return f"初筛：{before} → {after}（过滤 {removed} 只：ST/退市、北交所、跌停、低价股）"
