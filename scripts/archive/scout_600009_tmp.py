import os
import sys
import akshare as ak
import json
import pandas as pd

# Clear proxy
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(k, None)
os.environ['NO_PROXY'] = '*'

symbol = "600009"

try:
    print(f"--- SCOUTING {symbol} ---")
    
    # 1. Spot data
    spot_df = ak.stock_zh_a_spot_em()
    row = spot_df[spot_df['代码'] == symbol]
    if not row.empty:
        data = row.iloc[0].to_dict()
        print(f"Name: {data['名称']}")
        print(f"Price: {data['最新价']}")
        print(f"Change%: {data['涨跌幅']}%")
        print(f"Turnover: {data['成交额']}")
        print(f"Turnover Rate: {data['换手率']}%")
        print(f"Volume Ratio: {data['量比']}")
        print(f"PE(TTM): {data['市盈率-动态']}")
        print(f"PB: {data['市净率']}")
        print(f"Market Cap: {data['总市值']}")
        print(f"Industry: {data['板块']}")
    else:
        print(f"{symbol} not found in spot data")
        
    # 2. History data
    hist_df = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
    if not hist_df.empty:
        latest_close = hist_df.iloc[-1]['收盘']
        if len(hist_df) >= 5:
            gain_5 = (latest_close / hist_df.iloc[-5]['收盘'] - 1) * 100
            print(f"5-day Gain: {gain_5:.2f}%")
        if len(hist_df) >= 20:
            gain_20 = (latest_close / hist_df.iloc[-20]['收盘'] - 1) * 100
            print(f"20-day Gain: {gain_20:.2f}%")
        
        # Check MA
        ma5 = hist_df['收盘'].tail(5).mean()
        ma20 = hist_df['收盘'].tail(20).mean()
        ma60 = hist_df['收盘'].tail(60).mean()
        print(f"MA5: {ma5:.2f}, MA20: {ma20:.2f}, MA60: {ma60:.2f}")
        print(f"MA Signal: {'Strong' if latest_close > ma5 > ma20 > ma60 else 'Neutral'}")

    # 3. Capital Flow
    flow_df = ak.stock_individual_fund_flow(stock=symbol, market="sh")
    if not flow_df.empty:
        latest_flow = flow_df.iloc[0]
        print(f"Main Fund Net Inflow: {latest_flow['主力净流入']}")

    # 4. Industry average (Simplified)
    industry = data.get('板块', '机场')
    industry_df = spot_df[spot_df['板块'] == industry]
    if not industry_df.empty:
        avg_pe = industry_df['市盈率-动态'].mean()
        avg_pb = industry_df['市净率'].mean()
        print(f"Industry ({industry}) Average PE: {avg_pe:.2f}, PB: {avg_pb:.2f}")

except Exception as e:
    print(f"Error: {e}")
