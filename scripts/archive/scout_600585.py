import os
import sys
import json
import pandas as pd
from datetime import datetime

# Add the project root to sys.path
sys.path.append(os.getcwd())

from data.tushare_client import get_basic_info, get_price_df, get_valuation_history, get_sector_peers, get_capital_flow, resolve_stock

def scout_600585():
    ts_code, name, err = resolve_stock("600585")
    if err:
        print(f"Error resolving stock: {err}")
        return

    # 1. Basic Info
    basic_info, err = get_basic_info(ts_code)
    
    # 2. Price DF
    price_df, err = get_price_df(ts_code, days=140)
    
    # 3. Valuation History
    val_hist, err = get_valuation_history(ts_code, years=1)
    
    # 4. Sector Peers
    sector_peers, err = get_sector_peers(ts_code)
    
    # 5. Capital Flow
    capital_flow, err = get_capital_flow(ts_code)

    print("--- SCOUT DATA ---")
    print(f"Name: {name} ({ts_code})")
    
    if not price_df.empty:
        latest = price_df.iloc[-1]
        print(f"Latest Price: {latest['收盘']}")
        print(f"Today Change: {latest['涨跌幅']}%")
        print(f"Turnover Amount: {latest['成交额']}")
        print(f"Turnover Rate: {latest.get('换手率', 'N/A')}")
        
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
    
    if isinstance(val_hist, pd.DataFrame) and not val_hist.empty:
        latest_val = val_hist.iloc[-1]
        print(f"PE(TTM): {latest_val.get('pe_ttm', 'N/A')}")
        print(f"PB: {latest_val.get('pb', 'N/A')}")
        print(f"Total MV: {latest_val.get('total_mv', 'N/A')}")
        
    print(f"Sector Peers Output:\n{sector_peers}")
    print(f"Capital Flow Output:\n{capital_flow}")
    
    # Also check if we can get some quantitative score if there's a service
    try:
        from services.rank_service import get_stock_rank_info
        rank_info = get_stock_rank_info(ts_code)
        print(f"Rank Info: {rank_info}")
    except Exception:
        pass

if __name__ == "__main__":
    scout_600585()
