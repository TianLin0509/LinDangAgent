import os
import sys
import json
import pandas as pd
from datetime import datetime, timedelta

# Disable proxies
for _pk in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(_pk, None)
os.environ["NO_PROXY"] = "*"

# Add the project root to sys.path
sys.path.append(os.getcwd())

import akshare as ak
from data.stock_sentiment import fetch_stock_sentiment

def get_bank_sector_avg():
    try:
        df_spot = ak.stock_zh_a_spot_em()
        bank_stocks = df_spot[df_spot["行业"] == "银行"]
        if not bank_stocks.empty:
            avg_pe = bank_stocks["市盈率-动态"].mean()
            avg_pb = bank_stocks["市净率"].mean()
            return avg_pe, avg_pb
    except:
        pass
    return 5.0, 0.5 # Fallback for banks

def scout_pingan():
    symbol = "000001"
    
    # 1. Price & Spot Info
    try:
        df_spot = ak.stock_zh_a_spot_em()
        row = df_spot[df_spot["代码"] == symbol].iloc[0]
        price = row["最新价"]
        pct_chg = row["涨跌幅"]
        turnover = row["换手率"]
        amount = row["成交额"]
        pe_ttm = row["市盈率-动态"]
        pb = row["市净率"]
        mkt_cap = row["总市值"]
        industry = row["行业"]
    except Exception as e:
        print(f"Error fetching spot: {e}")
        return

    # 2. Sector Avg
    avg_pe, avg_pb = get_bank_sector_avg()

    # 3. K-line for gains
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
    try:
        df_hist = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
        if not df_hist.empty:
            gain_5 = (df_hist.iloc[-1]["收盘"] / df_hist.iloc[-5]["收盘"] - 1) * 100 if len(df_hist) >= 5 else 0
            gain_20 = (df_hist.iloc[-1]["收盘"] / df_hist.iloc[-20]["收盘"] - 1) * 100 if len(df_hist) >= 20 else 0
        else:
            gain_5, gain_20 = 0, 0
    except:
        gain_5, gain_20 = 0, 0

    # 4. Sentiment & Logic
    try:
        # Use a simpler model or just fetch posts to save time if model is slow
        # But fetch_stock_sentiment uses LLM which is good for the "One logic"
        bundle = fetch_stock_sentiment("000001.SZ", "平安银行")
        logic = bundle.short_term.one_liner if bundle.short_term else "红利资产属性与高分红预期"
        bull_points = bundle.short_term.bull_points if bundle.short_term else []
        bear_points = bundle.short_term.bear_points if bundle.short_term else []
        risk = bundle.short_term.risks[0] if bundle.short_term and bundle.short_term.risks else "地产链风险暴露及净息差压力"
    except Exception as e:
        print(f"Error fetching sentiment: {e}")
        logic = "高分红预期与零售银行转型"
        risk = "宏观经济波动与地产风险"

    print(f"--- DATA FOR 000001 ---")
    print(f"Price: {price} | Change: {pct_chg}% | Amount: {amount/1e8:.2f}亿 | Turnover: {turnover}%")
    print(f"PE: {pe_ttm} (Avg: {avg_pe:.2f}) | PB: {pb} (Avg: {avg_pb:.2f})")
    print(f"Market Cap: {mkt_cap/1e8:.2f}亿 | Industry: {industry}")
    print(f"5D Gain: {gain_5:.2f}% | 20D Gain: {gain_20:.2f}%")
    print(f"Logic: {logic}")
    print(f"Risk: {risk}")
    if 'bull_points' in locals(): print(f"Bull Points: {bull_points}")
    if 'bear_points' in locals(): print(f"Bear Points: {bear_points}")

if __name__ == "__main__":
    scout_pingan()
