import os
import sys
import json
import pandas as pd
from datetime import datetime

# Add the project root to sys.path
sys.path.append(os.getcwd())

from data.tushare_client import get_basic_info, get_price_df, get_valuation_history, get_sector_peers, get_capital_flow, resolve_stock

def gather_data(stock_name_or_code):
    ts_code, name, err = resolve_stock(stock_name_or_code)
    if err:
        print(f"Error resolving stock: {err}")
        return

    # 1. Basic Info
    basic_info, _ = get_basic_info(ts_code)
    
    # 2. Price DF
    price_df, _ = get_price_df(ts_code)
    
    # 3. Sector Peers
    sector_peers, _ = get_sector_peers(ts_code)
    
    # 4. Capital Flow
    capital_flow, _ = get_capital_flow(ts_code)

    # 5. Valuation
    valuation_df, _ = get_valuation_history(ts_code, years=1)
    if isinstance(valuation_df, pd.DataFrame) and not valuation_df.empty:
        valuation = valuation_df.iloc[-1].to_dict()
    else:
        valuation = {}

    result = {
        "name": name,
        "ts_code": ts_code,
        "basic_info": basic_info,
        "valuation": valuation,
        "sector_peers": sector_peers,
        "capital_flow": capital_flow
    }
    
    if not price_df.empty:
        latest = price_df.iloc[-1]
        result["latest_price"] = latest['收盘']
        result["today_change"] = latest['涨跌幅']
        result["turnover_amount"] = latest['成交额']
        result["turnover_rate"] = latest.get('换手率', 0)
        
        def get_gain(days):
            if len(price_df) >= days:
                return (price_df.iloc[-1]['收盘'] / price_df.iloc[-days]['收盘'] - 1) * 100
            return 0
        
        result["gain_5d"] = get_gain(5)
        result["gain_20d"] = get_gain(20)
        
        # MA
        price_df['MA5'] = price_df['收盘'].rolling(5).mean()
        price_df['MA20'] = price_df['收盘'].rolling(20).mean()
        last_row = price_df.iloc[-1]
        result["MA5"] = last_row['MA5']
        result["MA20"] = last_row['MA20']

    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    gather_data("000858")
