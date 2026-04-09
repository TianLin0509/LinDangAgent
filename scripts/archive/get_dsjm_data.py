import akshare as ak
import pandas as pd
import datetime

symbol = "002384"
try:
    # 1. 基础信息
    info = ak.stock_individual_info_em(symbol=symbol)
    print("--- INFO ---")
    print(info)

    # 2. 最新行情 (获取最近30天)
    hist = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date="20250301", adjust="qfq")
    print("\n--- K-LINE ---")
    print(hist.tail(10))

    # 3. 杜邦分析/财务概况
    # finance = ak.stock_financial_abstract_thm(symbol=symbol) # This might be unstable
    # try indicator data instead
    financial_report = ak.stock_financial_report_sina(stock=symbol, symbol="业绩报表")
    print("\n--- FINANCIAL REPORT ---")
    print(financial_report.head(5))

    # 4. 股东结构
    holders = ak.stock_gdfx_free_holding_statistics_em(symbol=symbol)
    print("\n--- HOLDERS ---")
    print(holders.head(3))

except Exception as e:
    print(f"Error: {e}")
