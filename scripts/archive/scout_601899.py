import os
import sys
import json
import pandas as pd
from datetime import datetime

# Add the project root to sys.path
sys.path.append(os.getcwd())

from data.tushare_client import get_basic_info, get_price_df, get_valuation_history, get_sector_peers, get_capital_flow, resolve_stock

def scout_601899():
    ts_code, name, err = resolve_stock("601899")
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
    
    if basic_info:
        print(f"Market Cap: {basic_info.get('market_cap', 0)} 亿")
        print(f"PE(TTM): {basic_info.get('pe_ttm', 0)}")
        print(f"PB: {basic_info.get('pb', 0)}")
        print(f"Industry: {basic_info.get('industry', 'N/A')}")
    
    if not price_df.empty:
        latest = price_df.iloc[-1]
        print(f"Latest Price: {latest.get('收盘', 0)}")
        print(f"Today Change: {latest.get('涨跌幅', 0)}%")
        print(f"Turnover Amount: {latest.get('成交额', 0)}")
        print(f"Turnover Ratio: {latest.get('换手率', 0)}%")
        
        # Calculate gains
        def get_gain(days):
            if len(price_df) >= days:
                return (price_df.iloc[-1]['收盘'] / price_df.iloc[-days]['收盘'] - 1) * 100
            return 0
        
        print(f"5-day Gain: {get_gain(5)}%")
        print(f"20-day Gain: {get_gain(20)}%")
        
        # MA check
        price_df['MA5'] = price_df['收盘'].rolling(5).mean()
        price_df['MA20'] = price_df['收盘'].rolling(20).mean()
        price_df['MA60'] = price_df['收盘'].rolling(60).mean()
        last_row = price_df.iloc[-1]
        print(f"MA5: {last_row['MA5']}, MA20: {last_row['MA20']}, MA60: {last_row['MA60']}")
    
    if not sector_peers.empty:
        print(f"Industry PE Average: {sector_peers['PE'].mean()}")
        print(f"Industry PB Average: {sector_peers['PB'].mean()}")
    
    if not capital_flow.empty:
        last_flow = capital_flow.iloc[-1]
        print(f"Net Main Inflow (Today): {last_flow.get('主力净流入', 0)} 万")

if __name__ == "__main__":
    scout_601899()
