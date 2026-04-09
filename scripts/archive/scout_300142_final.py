import os
import sys
import json
import pandas as pd
from datetime import datetime

# Add the project root to sys.path
sys.path.append(os.getcwd())

from data.tushare_client import get_basic_info, get_price_df, get_sector_peers, get_capital_flow, resolve_stock
from data.indicators import compute_indicators

def scout_300142():
    symbol = "300142"
    ts_code, name, err = resolve_stock(symbol)
    if err:
        print(f"Error resolving stock: {err}")
        return

    # 1. Basic Info
    basic_info, err = get_basic_info(ts_code)
    
    # 2. Price DF for gains and indicators
    price_df, err = get_price_df(ts_code, days=250)
    
    # 3. Sector Peers for industry average
    sector_peers, err = get_sector_peers(ts_code)
    
    # 4. Capital Flow
    capital_flow, err = get_capital_flow(ts_code)

    print("--- SCOUT DATA 300142 ---")
    print(f"Name: {name} ({ts_code})")
    print(f"Basic Info: {json.dumps(basic_info, ensure_ascii=False, indent=2)}")
    
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
        
        # Technical Indicators
        indicators = compute_indicators(price_df)
        print(f"Technical Summary: {indicators['summary']}")
        print(f"MA Score: {indicators['ma_score']}")
    
    print(f"Sector Peers Output:\n{sector_peers}")
    
    if isinstance(capital_flow, str):
         print(f"Capital Flow Output:\n{capital_flow}")
    else:
         print(f"Capital Flow Output: {capital_flow}")

if __name__ == "__main__":
    scout_300142()
