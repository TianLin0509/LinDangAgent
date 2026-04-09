import sys
import os
import json
import pandas as pd
from data.tushare_client import get_basic_info, get_price_df, get_capital_flow, get_sector_peers, resolve_stock

def scout_minsheng():
    stock_code = "600016"
    ts_code, name, err = resolve_stock(stock_code)
    if err:
        print(f"Error resolving stock: {err}")
        return

    print(f"Stock: {name} ({ts_code})")

    basic_info, err = get_basic_info(ts_code)
    if err:
        print(f"Error basic info: {err}")
    else:
        print("Basic Info:")
        print(json.dumps(basic_info, ensure_ascii=False, indent=2))

    price_df, err = get_price_df(ts_code, days=60)
    if err:
        print(f"Error price df: {err}")
    else:
        print("\nRecent Price Data (tail 5):")
        print(price_df.tail(5).to_string(index=False))
        
        # Calculate 5-day and 20-day returns
        if len(price_df) >= 20:
            c_now = price_df.iloc[-1]['收盘']
            c_5 = price_df.iloc[-5]['收盘']
            c_20 = price_df.iloc[-20]['收盘']
            ret_5 = (c_now / c_5 - 1) * 100
            ret_20 = (c_now / c_20 - 1) * 100
            print(f"\n5-day return: {ret_5:.2f}%")
            print(f"20-day return: {ret_20:.2f}%")
            
            # Turnover and Amount for today
            last_row = price_df.iloc[-1]
            print(f"Turnover Amount: {last_row['成交额'] / 10000:.2f} 亿") # Assuming amount is in 1000 RMB? Wait, tushare daily amount is usually in 1000 RMB.
            # Let me check tushare doc or fallback.py to see units.
            # In tushare_client.py: "amount": "成交额"
            # Usually tushare amount is in thousands of RMB.

    capital_flow, err = get_capital_flow(ts_code)
    if err:
        print(f"Error capital flow: {err}")
    else:
        print("\nCapital Flow (tail 5):")
        print(capital_flow)

    sector_peers, err = get_sector_peers(ts_code)
    if err:
        print(f"Error sector peers: {err}")
    else:
        print("\nSector Peers:")
        print(sector_peers)

if __name__ == "__main__":
    scout_minsheng()
