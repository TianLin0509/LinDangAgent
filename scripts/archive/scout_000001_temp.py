import os
import sys
import json
import pandas as pd
from datetime import datetime

# Add the project root to sys.path
sys.path.append(os.getcwd())

from data.tushare_client import get_basic_info, get_price_df, get_valuation_history, get_sector_peers, get_capital_flow, resolve_stock

def scout_pingan():
    ts_code, name, err = resolve_stock("000001")
    if err:
        print(f"Error resolving stock: {err}")
        return

    # 1. Basic Info
    basic_info, err = get_basic_info(ts_code)
    
    # 2. Price DF for gains
    price_df, err = get_price_df(ts_code)
    
    # 3. Sector Peers for industry average
    sector_peers, err = get_sector_peers(ts_code)
    
    # 4. Capital Flow
    capital_flow, err = get_capital_flow(ts_code)

    print(f"Name: {name} ({ts_code})")
    if basic_info:
        print(f"Basic Info: {json.dumps(basic_info, ensure_ascii=False)}")
    
    if not price_df.empty:
        latest = price_df.iloc[-1]
        print(f"Latest Price: {latest['收盘']}")
        print(f"Today Change: {latest['涨跌幅']}%")
        print(f"Turnover Amount: {latest.get('成交额', 'N/A')}")
        print(f"Turnover Rate: {latest.get('换手率', 'N/A')}")
        
        # Calculate gains
        def get_gain(days):
            if len(price_df) >= days:
                return (price_df.iloc[-1]['收盘'] / price_df.iloc[-days]['收盘'] - 1) * 100
            return None
        
        print(f"5-day Gain: {get_gain(5)}%")
        print(f"20-day Gain: {get_gain(20)}%")
    
    if not sector_peers.empty:
        print(f"PE TTM: {sector_peers.iloc[0].get('pe_ttm', 'N/A')}")
        print(f"PB: {sector_peers.iloc[0].get('pb', 'N/A')}")
        print(f"Industry: {sector_peers.iloc[0].get('industry', 'N/A')}")
        print(f"Avg PE: {sector_peers['pe_ttm'].mean()}")
        print(f"Avg PB: {sector_peers['pb'].mean()}")

    if not capital_flow.empty:
        print(f"Main Net Inflow: {capital_flow.iloc[-1].get('net_mf_amount', 'N/A')}")

if __name__ == "__main__":
    scout_pingan()
