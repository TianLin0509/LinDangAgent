import os
import sys
import json
import pandas as pd
from datetime import datetime

# Add the project root to sys.path
sys.path.append(os.getcwd())

from data.tushare_client import get_basic_info, get_price_df, get_sector_peers, get_capital_flow, resolve_stock
from data.akshare_data import get_analyst_consensus

def scout_000001():
    ts_code, name, err = resolve_stock("000001")
    if err and not ts_code:
        print(f"Error resolving stock: {err}")
        ts_code = "000001.SZ"
        name = "平安银行"

    # 1. Basic Info & Valuation
    basic_info, err = get_basic_info(ts_code)
    
    # 2. Price DF for gains
    price_df, err = get_price_df(ts_code)
    
    # 3. Sector Peers for industry average
    sector_peers_str, err = get_sector_peers(ts_code)
    
    # 4. Capital Flow
    capital_flow_str, err = get_capital_flow(ts_code)
    
    # 5. Analyst Consensus
    consensus = get_analyst_consensus("000001")

    print(f"Name: {name} ({ts_code})")
    print(f"Basic Info: {json.dumps(basic_info, ensure_ascii=False)}")
    
    if not price_df.empty:
        latest = price_df.iloc[-1]
        print(f"Latest Price: {latest['收盘']}")
        print(f"Today Change: {latest['涨跌幅']}%")
        print(f"Amount: {latest.get('成交额', 'N/A')}")
        
        def get_gain(days):
            if len(price_df) >= days:
                return (price_df.iloc[-1]['收盘'] / price_df.iloc[-days]['收盘'] - 1) * 100
            return None
        
        print(f"5-day Gain: {get_gain(5)}%")
        print(f"20-day Gain: {get_gain(20)}%")

    print(f"Sector Peers: {sector_peers_str}")
    print(f"Capital Flow: {capital_flow_str}")
    if consensus:
        print(f"Analyst Consensus: {consensus.get('text', '')}")

if __name__ == "__main__":
    scout_000001()
