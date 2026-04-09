import os
import sys
import json
import pandas as pd
from datetime import datetime

# Add the project root to sys.path
sys.path.append(os.getcwd())

from data.tushare_client import get_basic_info, get_price_df, get_valuation_history, get_sector_peers, get_capital_flow, resolve_stock

def scout_300059():
    ts_code, name, err = resolve_stock("300059")
    if err:
        print(f"Error resolving stock: {err}")
        return

    print(f"--- SCOUT DATA for {name} ({ts_code}) ---")

    # 1. Basic Info
    basic_info, err = get_basic_info(ts_code)
    print(f"Basic Info: {json.dumps(basic_info, ensure_ascii=False)}")
    
    # 2. Price DF for gains
    price_df, err = get_price_df(ts_code)
    if isinstance(price_df, pd.DataFrame) and not price_df.empty:
        latest = price_df.iloc[-1]
        print(f"Latest Price: {latest.get('收盘', 'N/A')}")
        print(f"Today Change: {latest.get('涨跌幅', 'N/A')}%")
        print(f"Turnover Amount: {latest.get('成交额', 'N/A')}")
        print(f"Turnover Rate: {latest.get('换手率', 'N/A')}%")
        
        def get_gain(days):
            if len(price_df) >= days:
                return (price_df.iloc[-1]['收盘'] / price_df.iloc[-days]['收盘'] - 1) * 100
            return None
        
        print(f"5-day Gain: {get_gain(5)}%")
        print(f"20-day Gain: {get_gain(20)}%")
        
        price_df['MA5'] = price_df['收盘'].rolling(5).mean()
        price_df['MA20'] = price_df['收盘'].rolling(20).mean()
        price_df['MA60'] = price_df['收盘'].rolling(60).mean()
        last_row = price_df.iloc[-1]
        print(f"MA5: {last_row['MA5']}, MA20: {last_row['MA20']}, MA60: {last_row['MA60']}")
    else:
        print(f"Price DF Error: {price_df if isinstance(price_df, str) else 'Empty'}")

    # 3. Valuation History
    val_hist, err = get_valuation_history(ts_code)
    if isinstance(val_hist, pd.DataFrame) and not val_hist.empty:
        latest_val = val_hist.iloc[-1]
        print(f"PE(TTM): {latest_val.get('pe_ttm', 'N/A')}")
        print(f"PB: {latest_val.get('pb', 'N/A')}")
        # total_mv is in daily_basic but maybe not in valuation_history columns if not selected
        # check columns
        # print(f"Valuation Columns: {val_hist.columns}")
    else:
        print(f"Valuation History Error: {val_hist if isinstance(val_hist, str) else 'Empty'}")

    # 4. Sector Peers
    sector_peers, err = get_sector_peers(ts_code)
    print(f"Sector Peers Output:\n{sector_peers}")

    # 5. Capital Flow
    capital_flow, err = get_capital_flow(ts_code)
    print(f"Capital Flow Output:\n{capital_flow}")

if __name__ == "__main__":
    scout_300059()
