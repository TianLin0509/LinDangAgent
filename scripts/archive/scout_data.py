import os
import sys
import json
import pandas as pd
from datetime import datetime

# Add the project root to sys.path
sys.path.append(os.getcwd())

from data.tushare_client import get_basic_info, get_price_df, get_valuation_history, get_sector_peers, get_capital_flow, resolve_stock

def scout_moutai():
    ts_code, name, err = resolve_stock("600519")
    if err:
        print(f"Error resolving stock: {err}")
        return

    # 1. Basic Info
    basic_info, err = get_basic_info(ts_code)
    if err:
        print(f"Error getting basic info: {err}")
    
    # 2. Price DF for gains
    price_df, err = get_price_df(ts_code)
    if err:
        print(f"Error getting price df: {err}")
    
    # 3. Sector Peers for industry average
    sector_peers, err = get_sector_peers(ts_code)
    if err:
        print(f"Error getting sector peers: {err}")
    
    # 4. Capital Flow
    capital_flow, err = get_capital_flow(ts_code)
    if err:
        print(f"Error getting capital flow: {err}")

    print("--- SCOUT DATA ---")
    print(f"Name: {name} ({ts_code})")
    print(f"Basic Info: {json.dumps(basic_info, ensure_ascii=False)}")
    
    if not price_df.empty:
        latest = price_df.iloc[-1]
        print(f"Latest Price: {latest['收盘']}")
        print(f"Today Change: {latest['涨跌幅']}%")
        print(f"Turnover Amount: {latest['成交额']}")
        
        # Calculate gains
        def get_gain(days):
            if len(price_df) >= days:
                return (price_df.iloc[-1]['收盘'] / price_df.iloc[-days]['收盘'] - 1) * 100
            return None
        
        print(f"5-day Gain: {get_gain(5)}%")
        print(f"20-day Gain: {get_gain(20)}%")
        
        # MA check
        price_df['MA5'] = price_df['收盘'].rolling(5).mean()
        price_df['MA20'] = price_df['收盘'].rolling(20).mean()
        price_df['MA60'] = price_df['收盘'].rolling(60).mean()
        last_row = price_df.iloc[-1]
        print(f"MA5: {last_row['MA5']}, MA20: {last_row['MA20']}, MA60: {last_row['MA60']}")
    
    print(f"Sector Peers Output:\n{sector_peers}")
    print(f"Capital Flow Output:\n{capital_flow}")

if __name__ == "__main__":
    scout_moutai()
