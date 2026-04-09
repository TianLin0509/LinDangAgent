import os
import akshare as ak
import pandas as pd
import json

# Clear proxy environment variables
for _proxy_key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(_proxy_key, None)

def get_wly_data():
    try:
        # Get latest daily info
        df = ak.stock_zh_a_spot_em()
        row = df[df['代码'] == '000858']
        
        # Get indicator info (PE/PB)
        ind_df = ak.stock_a_lg_indicator(symbol="000858")
        latest_ind = ind_df.iloc[-1]
        
        # Get history for gains
        hist_df = ak.stock_zh_a_hist(symbol="000858", period="daily", start_date="20260101")
        
        result = {
            "name": row.iloc[0]['名称'],
            "price": row.iloc[0]['最新价'],
            "change": row.iloc[0]['涨跌额'],
            "pct_change": row.iloc[0]['涨跌幅'],
            "turnover": row.iloc[0]['成交额'],
            "turnover_rate": row.iloc[0]['换手率'],
            "pe_ttm": latest_ind['pe'],
            "pb": latest_ind['pb'],
            "market_cap": row.iloc[0]['总市值'],
            "gain_5d": (hist_df.iloc[-1]['收盘'] / hist_df.iloc[-5]['收盘'] - 1) * 100 if len(hist_df) >= 5 else 0,
            "gain_20d": (hist_df.iloc[-1]['收盘'] / hist_df.iloc[-20]['收盘'] - 1) * 100 if len(hist_df) >= 20 else 0,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    get_wly_data()
