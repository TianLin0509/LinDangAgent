"""
发现 QMT get_instrument_detail 返回的 InstrumentStatus 值及其含义。
目标：找出"退市"对应的 InstrumentStatus 码，用于 stock_gate 硬拦截判定。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xtquant import xtdata

# 一批不同状态的股票
TEST_SYMBOLS = {
    "normal": ["000001.SZ", "600036.SH", "300750.SZ"],
    "st_or_delisted": ["400010.BJ", "000033.SZ", "600087.SH", "600832.SH",
                       "600832.SH", "000583.SZ", "300372.SZ"],
    "etf": ["510300.SH", "159915.SZ"],
    "index": ["000300.SH", "000905.SH"],
}


def main():
    print(f"{'symbol':<15s} {'name':<22s} {'Status':>7s} {'IsTrading':>10s}  类型")
    print("-" * 80)

    for category, syms in TEST_SYMBOLS.items():
        for sym in syms:
            try:
                d = xtdata.get_instrument_detail(sym, iscomplete=True)
            except Exception as e:
                print(f"{sym:<15s} ERROR {type(e).__name__}: {str(e)[:40]}")
                continue

            if d is None:
                print(f"{sym:<15s} {'(detail=None)':<22s} {'N/A':>7s} {'N/A':>10s}  {category}")
                continue
            name = d.get("InstrumentName", "") or ""
            status = d.get("InstrumentStatus", "")
            trading = d.get("IsTrading", "")
            print(f"{sym:<15s} {name:<22s} {str(status):>7s} {str(trading):>10s}  {category}")

    # 扫 ST 股（涨跌停 5%）
    print("\nST 扫描（从沪深A股池前 500 里找 UpStop/Pre<0.06）:")
    a_stocks = xtdata.get_stock_list_in_sector("沪深A股")[:500]
    try:
        details = xtdata.get_instrument_detail_list(a_stocks, iscomplete=True)
    except Exception as e:
        print(f"  批量查询失败: {e}")
        return

    st_found = 0
    for sym, d in details.items():
        if not d:
            continue
        pre = d.get("PreClose", 0)
        up = d.get("UpStopPrice", 0)
        if pre > 0 and (up - pre) / pre < 0.06:
            name = d.get("InstrumentName", "") or ""
            status = d.get("InstrumentStatus", "")
            print(f"  {sym:<15s} {name:<22s} Status={status}  pre={pre:.2f} up={up:.2f}")
            st_found += 1
            if st_found >= 15:
                break
    print(f"\n总共扫到 {st_found} 只 ST 股（UpStop/Pre<0.06）")


if __name__ == "__main__":
    main()
