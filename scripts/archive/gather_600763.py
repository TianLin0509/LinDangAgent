import sys
import io

# Set encoding to utf-8 for stdout and stderr
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import os
import json
import pandas as pd
from data.tushare_client import get_basic_info, get_price_df, get_sector_peers, get_capital_flow, get_financial, to_ts_code, load_stock_list

def gather_scout_info(code6):
    ts_code = to_ts_code(code6)
    print(f"Gathering info for {ts_code}...")
    
    info = {}
    
    # 1. Basic Info (Price, PE, PB, MV, Turnover, Vol Ratio)
    basic, err = get_basic_info(ts_code)
    if not err:
        info['basic'] = basic
    else:
        print(f"Error basic: {err}")
        
    # 2. Price DF (Recent trends)
    df_price, err = get_price_df(ts_code, days=30)
    if not err and not df_price.empty:
        last_close = df_price.iloc[-1]['收盘']
        p5 = df_price.iloc[-5]['收盘'] if len(df_price) >= 5 else df_price.iloc[0]['收盘']
        p20 = df_price.iloc[-20]['收盘'] if len(df_price) >= 20 else df_price.iloc[0]['收盘']
        info['trends'] = {
            "5d_pct": round((last_close / p5 - 1) * 100, 2),
            "20d_pct": round((last_close / p20 - 1) * 100, 2),
            "last_close": last_close
        }
    else:
        print(f"Error price: {err}")

    # 3. Sector Peers (Industry PE/PB)
    peers, err = get_sector_peers(ts_code)
    if not err:
        info['peers'] = peers
    else:
        print(f"Error peers: {err}")
        
    # 4. Capital Flow
    flow, err = get_capital_flow(ts_code)
    if not err:
        info['flow'] = flow
    else:
        print(f"Error flow: {err}")
        
    # 5. Financials
    fin, err = get_financial(ts_code)
    if not err:
        info['financial'] = fin
    else:
        print(f"Error fin: {err}")

    print(json.dumps(info, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    gather_scout_info("600763")
