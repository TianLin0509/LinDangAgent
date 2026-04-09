import os
import sys

# Clear proxy at the very beginning
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(k, None)
os.environ['NO_PROXY'] = '*'

try:
    import akshare as ak
    import json
    import pandas as pd

    df = ak.stock_zh_a_spot_em()
    row = df[df['代码'] == '600585']
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
        print("600585 not found in spot data")
        
    hist_df = ak.stock_zh_a_hist(symbol="600585", period="daily", adjust="qfq")
    if not hist_df.empty:
        latest_close = hist_df.iloc[-1]['收盘']
        if len(hist_df) >= 5:
            gain_5 = (latest_close / hist_df.iloc[-5]['收盘'] - 1) * 100
            print(f"5-day Gain: {gain_5:.2f}%")
        if len(hist_df) >= 20:
            gain_20 = (latest_close / hist_df.iloc[-20]['收盘'] - 1) * 100
            print(f"20-day Gain: {gain_20:.2f}%")

except Exception as e:
    print(f"Error: {e}")
