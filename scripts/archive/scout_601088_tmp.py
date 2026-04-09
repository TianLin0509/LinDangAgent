import os
import akshare as ak
import pandas as pd

# Clear proxy
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(k, None)
os.environ['NO_PROXY'] = '*'

def scout_601088():
    try:
        # 1. Spot data
        df = ak.stock_zh_a_spot_em()
        row = df[df['代码'] == '601088']
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
        
        # 2. Historical for gains
        hist_df = ak.stock_zh_a_hist(symbol="601088", period="daily", start_date="20250101", adjust="qfq")
        if not hist_df.empty:
            latest_close = hist_df.iloc[-1]['收盘']
            if len(hist_df) >= 5:
                gain_5 = (latest_close / hist_df.iloc[-5]['收盘'] - 1) * 100
                print(f"5-day Gain: {gain_5:.2f}%")
            if len(hist_df) >= 20:
                gain_20 = (latest_close / hist_df.iloc[-20]['收盘'] - 1) * 100
                print(f"20-day Gain: {gain_20:.2f}%")
        
        # 3. Individual capital flow
        flow_df = ak.stock_individual_fund_flow(stock="601088", market="sh")
        if not flow_df.empty:
            latest_flow = flow_df.iloc[0] # Usually the most recent is first or last? let's check
            print(f"Capital Flow (Latest): {latest_flow.to_dict()}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    scout_601088()
