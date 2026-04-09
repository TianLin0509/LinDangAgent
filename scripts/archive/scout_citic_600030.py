import os
import sys
import time

# Clear proxy at the very beginning
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(k, None)
os.environ['NO_PROXY'] = '*'

import akshare as ak
import pandas as pd
import json

symbol = "600030"

def fetch_with_retry(func, *args, **kwargs):
    max_retries = 3
    for i in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if i == max_retries - 1:
                raise e
            time.sleep(2)

# 1. Spot Data
try:
    df_spot = fetch_with_retry(ak.stock_zh_a_spot_em)
    row = df_spot[df_spot['代码'] == symbol]
    if not row.empty:
        data = row.iloc[0].to_dict()
        print(f"Name: {data['名称']}")
        print(f"Price: {data['最新价']}")
        print(f"Change%: {data['涨跌幅']}%")
        print(f"Amount: {data['成交额']/1e8:.2f}亿")
        print(f"Turnover: {data['换手率']}%")
        print(f"Volume Ratio: {data['量比']}")
        print(f"PE(TTM): {data['市盈率-动态']}")
        print(f"PB: {data['市净率']}")
        print(f"Market Cap: {data['总市值']/1e8:.2f}亿")
        print(f"Industry: {data['板块']}")
        
        # 4. Industry average valuation
        industry = data['板块']
        df_ind = df_spot[df_spot['板块'] == industry]
        if not df_ind.empty:
            avg_pe = df_ind['市盈率-动态'].replace('-', 0).astype(float).median()
            avg_pb = df_ind['市净率'].replace('-', 0).astype(float).median()
            print(f"Industry PE Median: {avg_pe:.2f}")
            print(f"Industry PB Median: {avg_pb:.2f}")
    else:
        print(f"{symbol} not found in spot data")

    # 2. Main Inflow
    try:
        df_flow = fetch_with_retry(ak.stock_individual_fund_flow_rank, indicator="今日")
        row_flow = df_flow[df_flow['代码'] == symbol]
        if not row_flow.empty:
            flow = row_flow.iloc[0]['今日主力净流入-净额']
            print(f"Main Net Inflow: {flow/10000:.2f}万")
        else:
            print(f"Main flow for {symbol} not found")
    except:
        print("Main flow failed")

    # 3. 5/20-day gain
    try:
        hist_df = fetch_with_retry(ak.stock_zh_a_hist, symbol=symbol, period="daily", adjust="qfq")
        if not hist_df.empty:
            latest_close = hist_df.iloc[-1]['收盘']
            if len(hist_df) >= 5:
                gain_5 = (latest_close / hist_df.iloc[-5]['收盘'] - 1) * 100
                print(f"5-day Gain: {gain_5:.2f}%")
            if len(hist_df) >= 20:
                gain_20 = (latest_close / hist_df.iloc[-20]['收盘'] - 1) * 100
                print(f"20-day Gain: {gain_20:.2f}%")
    except:
        print("Hist data failed")

except Exception as e:
    print(f"Error: {e}")
