import os
import sys
import json
import pandas as pd
from datetime import datetime

# Add the project root to sys.path
sys.path.append(os.getcwd())

from data.tushare_client import (
    resolve_stock, get_basic_info, get_price_df, 
    get_sector_peers, get_capital_flow, get_valuation_history
)

def scout_002027():
    ts_code, name, err = resolve_stock("002027")
    if err:
        print(f"Error resolving stock: {err}")
        return

    # 1. Basic Info
    basic_info, err = get_basic_info(ts_code)
    
    # 2. Price DF
    price_df, err_p = get_price_df(ts_code)
    
    # 3. Sector Peers
    sector_peers, err_s = get_sector_peers(ts_code)
    
    # 4. Capital Flow
    capital_flow, err_c = get_capital_flow(ts_code)

    # 5. Valuation History (for percentile)
    val_hist, err_v = get_valuation_history(ts_code)

    print(f"STK_NAME: {name}")
    print(f"STK_CODE: {ts_code}")
    print(f"BASIC: {json.dumps(basic_info, ensure_ascii=False)}")
    
    if not price_df.empty:
        latest = price_df.iloc[-1]
        print(f"PRICE: {latest['收盘']}")
        print(f"CHG: {latest['涨跌幅']}")
        print(f"AMOUNT: {latest['成交额']}")
        
        def get_gain(days):
            if len(price_df) >= days:
                return (price_df.iloc[-1]['收盘'] / price_df.iloc[-days]['收盘'] - 1) * 100
            return 0
        
        print(f"GAIN_5: {get_gain(5):.2f}%")
        print(f"GAIN_20: {get_gain(20):.2f}%")
        
        # Technical Signal
        price_df['MA5'] = price_df['收盘'].rolling(5).mean()
        price_df['MA20'] = price_df['收盘'].rolling(20).mean()
        price_df['MA60'] = price_df['收盘'].rolling(60).mean()
        last = price_df.iloc[-1]
        print(f"MA5: {last['MA5']:.2f}, MA20: {last['MA20']:.2f}, MA60: {last['MA60']:.2f}")

    if sector_peers:
        print(f"SECTOR: {sector_peers}")
        
    if capital_flow:
        print(f"CAPITAL: {capital_flow}")
    
    if not val_hist.empty:
        curr_pe = float(basic_info.get("市盈率TTM", 0))
        pe_percentile = (val_hist['pe_ttm'] < curr_pe).mean() * 100
        print(f"PE_PERCENTILE: {pe_percentile:.2f}%")

if __name__ == "__main__":
    scout_002027()
