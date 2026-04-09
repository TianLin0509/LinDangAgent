import os
import sys
import json
import pandas as pd

# Clear proxy at the very beginning
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(k, None)
os.environ['NO_PROXY'] = '*'

try:
    import akshare as ak
    
    code = "300274"
    print(f"Scouting {code}...")
    
    # 1. Spot Data
    df_spot = ak.stock_zh_a_spot_em()
    row = df_spot[df_spot['代码'] == code]
    if not row.empty:
        data = row.iloc[0].to_dict()
        print(f"Name: {data['名称']}")
        print(f"Price: {data['最新价']}")
        print(f"Change%: {data['涨跌幅']}%")
        print(f"Amount: {data['成交额']}")
        print(f"Turnover: {data['换手率']}%")
        print(f"Volume Ratio: {data['量比']}")
        print(f"PE(TTM): {data['市盈率-动态']}")
        print(f"PB: {data['市净率']}")
        print(f"Market Cap: {data['总市值']}")
        print(f"Industry: {data['板块']}")
    else:
        print(f"{code} not found in spot data")

    # 2. History Data
    hist_df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
    if not hist_df.empty:
        latest_close = hist_df.iloc[-1]['收盘']
        if len(hist_df) >= 5:
            gain_5 = (latest_close / hist_df.iloc[-5]['收盘'] - 1) * 100
            print(f"5-day Gain: {gain_5:.2f}%")
        if len(hist_df) >= 20:
            gain_20 = (latest_close / hist_df.iloc[-20]['收盘'] - 1) * 100
            print(f"20-day Gain: {gain_20:.2f}%")
        
        # Technical Signal (Very simple SMA)
        ma20 = hist_df['收盘'].tail(20).mean()
        print(f"Latest Close: {latest_close}, MA20: {ma20:.2f}")
        if latest_close > ma20:
            print("Technical: Above MA20 (Bullish)")
        else:
            print("Technical: Below MA20 (Bearish)")

    # 3. Fund Flow
    df_flow = ak.stock_individual_fund_flow(stock=code, market="sz")
    if not df_flow.empty:
        latest_flow = df_flow.iloc[-1]
        print(f"Main Flow: {latest_flow['主力净流入']} 万")

    # 4. Industry averages (Approximation)
    if not row.empty:
        industry = data['板块']
        df_ind = df_spot[df_spot['板块'] == industry]
        avg_pe = df_ind['市盈率-动态'].replace('-', 0).astype(float).mean()
        avg_pb = df_ind['市净率'].replace('-', 0).astype(float).mean()
        print(f"Industry {industry} Avg PE: {avg_pe:.2f}, Avg PB: {avg_pb:.2f}")

except Exception as e:
    print(f"Error: {e}")
