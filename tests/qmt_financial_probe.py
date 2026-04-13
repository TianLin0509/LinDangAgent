"""
验证 download_financial_data → get_financial_data 链路是否可用。
这决定了 QMT 能否作为财务数据兜底/交叉验证源。
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from xtquant import xtdata

    sym = "000001.SZ"
    tables = ["Balance", "Income", "CashFlow", "PershareIndex",
              "CapitalStructure", "HolderNum", "TopTenHolder", "TopTenHolderFree"]

    print(f"======== QMT Financial Data Probe @ {sym} ========\n")

    # ── Step 1: 直接查询（无下载）——基线
    print("[1] 直接 get_financial_data（无下载）")
    t0 = time.time()
    try:
        raw = xtdata.get_financial_data([sym], table_list=tables)
        cost = int((time.time() - t0) * 1000)
        print(f"    cost={cost}ms, keys={list(raw.keys()) if raw else None}")
        if raw and sym in raw:
            for t in tables:
                sub = raw[sym].get(t) if isinstance(raw[sym], dict) else None
                rows = len(sub) if isinstance(sub, pd.DataFrame) else "N/A"
                print(f"    {t}: rows={rows}")
    except Exception as e:
        print(f"    EXCEPTION: {type(e).__name__}: {e}")

    # ── Step 2: download_financial_data
    print(f"\n[2] download_financial_data({sym}, {tables})")
    t0 = time.time()
    try:
        xtdata.download_financial_data([sym], table_list=tables)
        cost = int((time.time() - t0) * 1000)
        print(f"    cost={cost}ms")
    except Exception as e:
        print(f"    EXCEPTION: {type(e).__name__}: {e}")
        print("    可能国金 QMT 没订财务权限")
        return 1

    # ── Step 3: download 后再查询
    print(f"\n[3] 再次 get_financial_data（下载后）")
    t0 = time.time()
    try:
        raw = xtdata.get_financial_data([sym], table_list=tables)
        cost = int((time.time() - t0) * 1000)
        print(f"    cost={cost}ms, keys={list(raw.keys()) if raw else None}")
    except Exception as e:
        print(f"    EXCEPTION: {type(e).__name__}: {e}")
        return 1

    # ── Step 4: 每张表的 schema + 样本
    print(f"\n[4] 每张表 schema + 首尾样本")
    if not raw or sym not in raw:
        print("    返回仍为空 → QMT 财务数据不可用")
        return 1

    per_sym = raw[sym]
    if not isinstance(per_sym, dict):
        print(f"    返回类型异常: {type(per_sym).__name__}")
        return 1

    for t in tables:
        print(f"\n    === {t} ===")
        df = per_sym.get(t)
        if df is None:
            print(f"    缺失")
            continue
        if not isinstance(df, pd.DataFrame):
            print(f"    非 DataFrame: {type(df).__name__}")
            continue
        print(f"    rows={len(df)}, cols={list(df.columns)}")
        if df.empty:
            print(f"    表存在但为空")
            continue
        # 打印首尾各 1 行
        print(f"    首行: {df.iloc[0].to_dict()}")
        if len(df) > 1:
            print(f"    尾行: {df.iloc[-1].to_dict()}")
        # 历史深度
        date_cols = [c for c in df.columns if "date" in c.lower() or "time" in c.lower() or c in ("m_timetag", "m_anntime")]
        for dc in date_cols[:2]:
            try:
                print(f"    {dc} 范围: {df[dc].min()} → {df[dc].max()}")
            except Exception:
                pass

    # ── Step 5: 尝试有没有 download_financial_data2（批量）
    print(f"\n[5] download_financial_data2 是否存在？")
    if hasattr(xtdata, "download_financial_data2"):
        print("    存在（批量异步）")
    else:
        print("    不存在")

    # ── Step 6: 验证 Tushare 财务做对比（可选，确认 schema 对等）
    print(f"\n[6] 与 Tushare 对比（如果可用）")
    try:
        from data.tushare_client import get_financial
        ts_fin, ts_err = get_financial(sym)
        if ts_err is None:
            print(f"    Tushare 财务: len={len(ts_fin)} 字符")
            print(f"    前 300 字: {ts_fin[:300]}")
        else:
            print(f"    Tushare 失败: {ts_err}")
    except Exception as e:
        print(f"    Tushare 调用异常: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
