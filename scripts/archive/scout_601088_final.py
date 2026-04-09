import sys
import os
import pandas as pd
from data.tushare_client import get_basic_info, get_price_df, get_capital_flow, get_sector_peers, price_summary, to_ts_code

def main():
    ts_code = "601088.SH"
    
    print(f"--- Scouting {ts_code} ---")
    
    # 1. Basic Info & Valuation
    basic_info, err = get_basic_info(ts_code)
    if err:
        print(f"Basic Info Error: {err}")
    else:
        print("Basic Info:")
        for k, v in basic_info.items():
            print(f"  {k}: {v}")
            
    # 2. Price & Technicals
    df_price, err = get_price_df(ts_code)
    if err:
        print(f"Price Error: {err}")
    else:
        summary = price_summary(df_price)
        print("\nPrice Summary:")
        print(summary)
        
    # 3. Capital Flow
    flow, err = get_capital_flow(ts_code)
    if err:
        print(f"Capital Flow Error: {err}")
    else:
        print("\nCapital Flow (Recent):")
        print(flow)
        
    # 4. Sector Peers
    peers, err = get_sector_peers(ts_code)
    if err:
        print(f"Sector Peers Error: {err}")
    else:
        print("\nSector Peers:")
        print(peers)

if __name__ == "__main__":
    main()
