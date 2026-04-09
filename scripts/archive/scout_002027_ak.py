import os
import sys

# UNSET PROXIES
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ["NO_PROXY"] = "*"

import pandas as pd
import akshare as ak

# Ensure output is UTF-8
try:
    sys.stdout.reconfigure(encoding='utf-8')
except:
    pass

def get_data_ak(symbol):
    print(f"DEBUG: Fetching data for {symbol}")
    
    # 1. Spot data for price and PE/PB
    try:
        df_spot = ak.stock_zh_a_spot_em()
        row = df_spot[df_spot["代码"] == symbol]
        if not row.empty:
            r = row.iloc[0]
            print(f"NAME: {r['名称']}")
            print(f"PRICE: {r['最新价']}")
            print(f"CHG: {r['涨跌幅']}%")
            print(f"PE_TTM: {r['市盈率-动态']}")
            print(f"PB: {r['市净率']}")
            print(f"TURNOVER: {r['换手率']}%")
            print(f"AMOUNT: {r['成交额']}")
            print(f"MV: {r['总市值']}")
            print(f"INDUSTRY: {r['行业']}")
        else:
            print(f"ERROR: Symbol {symbol} not found in spot data")
    except Exception as e:
        print(f"ERROR spot: {e}")

    # 2. Hist data for gains and MA
    try:
        df_hist = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
        if not df_hist.empty:
            df_hist = df_hist.tail(60)
            latest_price = df_hist.iloc[-1]['收盘']
            gain_5 = (latest_price / df_hist.iloc[-5]['收盘'] - 1) * 100
            gain_20 = (latest_price / df_hist.iloc[-20]['收盘'] - 1) * 100
            
            ma5 = df_hist['收盘'].rolling(5).mean().iloc[-1]
            ma20 = df_hist['收盘'].rolling(20).mean().iloc[-1]
            ma60 = df_hist['收盘'].rolling(60).mean().iloc[-1]
            
            print(f"GAIN_5: {gain_5:.2f}%")
            print(f"GAIN_20: {gain_20:.2f}%")
            print(f"MA5: {ma5:.2f}")
            print(f"MA20: {ma20:.2f}")
            print(f"MA60: {ma60:.2f}")
    except Exception as e:
        print(f"ERROR hist: {e}")

    # 3. Capital flow
    try:
        df_flow = ak.stock_individual_fund_flow(stock=symbol, market="sz")
        if not df_flow.empty:
            latest_flow = df_flow.iloc[-1]
            print(f"NET_FLOW: {latest_flow['主力净流入-净额']}")
    except Exception as e:
        print(f"ERROR flow: {e}")

if __name__ == "__main__":
    get_data_ak("002027")
