import os
import sys
import pandas as pd
import json

# Add project root to sys.path
sys.path.append(os.getcwd())

from data.tushare_client import get_basic_info, get_price_df, get_sector_peers, to_ts_code

def main():
    ts_code = "600030.SH"
    
    # 1. Basic Info
    basic_info, err = get_basic_info(ts_code)
    if err:
        print(f"Basic Info Error: {err}")
    else:
        print("--- Basic Info ---")
        print(json.dumps(basic_info, ensure_ascii=False, indent=2))
        
    # 2. Price/K-line
    price_df, err = get_price_df(ts_code, days=40)
    if err:
        print(f"Price DF Error: {err}")
    else:
        print("\n--- Price Stats ---")
        latest = price_df.iloc[-1]
        print(f"Latest Price: {latest['收盘']}")
        print(f"Change%: {latest['涨跌幅']}%")
        
        if len(price_df) >= 5:
            gain_5 = (price_df.iloc[-1]['收盘'] / price_df.iloc[-5]['收盘'] - 1) * 100
            print(f"5-day Gain: {gain_5:.2f}%")
        if len(price_df) >= 20:
            gain_20 = (price_df.iloc[-1]['收盘'] / price_df.iloc[-20]['收盘'] - 1) * 100
            print(f"20-day Gain: {gain_20:.2f}%")
            
    # 3. Sector Peers for Industry Averages
    peers_info, err = get_sector_peers(ts_code)
    if err:
        print(f"Peers Error: {err}")
    else:
        print("\n--- Sector Peers ---")
        print(peers_info)

if __name__ == "__main__":
    main()
