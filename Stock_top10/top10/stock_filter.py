"""量化初筛 — 过滤不适合短线的标的（v3.0：量价背离过滤+市值软化）"""

import logging
import pandas as pd
from utils.cache_compat import compat_cache

logger = logging.getLogger(__name__)


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

    # 5. v3.0：市值不再硬过滤，改为在 signal.py 的估值分中软惩罚
    # （原30亿门槛已移除，由 compute_quant_score 的 total_mv_yi 参数处理）

    filtered = filtered.reset_index(drop=True)
    return filtered


def apply_volume_price_filter(df: pd.DataFrame) -> pd.DataFrame:
    """v3.0：量价背离过滤 — 放量下跌的标的直接剔除，不进Scout流程

    需要在数据增强（enrich_candidates）之后调用，依赖K线摘要中的技术指标。
    """
    if df.empty:
        return df

    from Stock_top10.top10.signal import check_volume_price_divergence

    keep_mask = []
    removed_names = []
    for _, row in df.iterrows():
        # 构造简易 technicals dict 用于检测
        technicals = {}
        vol_state = ""
        chg_3 = None

        # 从K线摘要中提取（如果已经计算过）
        kline_summary = str(row.get("K线摘要", ""))
        if "显著放量" in kline_summary:
            technicals["量能状态"] = "显著放量"
        elif "温和放量" in kline_summary:
            technicals["量能状态"] = "温和放量"

        # 直接读 enriched 列
        if "近3日涨幅" in row and pd.notna(row.get("近3日涨幅")):
            technicals["近3日涨幅"] = float(row["近3日涨幅"])
        elif "涨跌幅" in row and pd.notna(row.get("涨跌幅")):
            # 没有3日涨幅时用当日涨跌幅粗略替代
            technicals["近3日涨幅"] = float(row["涨跌幅"])

        if check_volume_price_divergence(technicals):
            keep_mask.append(False)
            removed_names.append(row.get("股票名称", "?"))
        else:
            keep_mask.append(True)

    if removed_names:
        logger.info("[volume_price_filter] 量价背离剔除 %d 只: %s",
                    len(removed_names), ", ".join(removed_names[:10]))

    return df[keep_mask].reset_index(drop=True)


def get_filter_summary(before: int, after: int) -> str:
    removed = before - after
    return f"初筛：{before} → {after}（过滤 {removed} 只：ST/退市、北交所、跌停、低价股）"
