import tushare as ts
import pandas as pd
import datetime

token = "bc83655f008cdf037fddc36bac9bef0eeb31d2e55fc29047afa7d6f39910"
server = "http://lianghua.nanyangqiankun.top"
pro = ts.pro_api(token, server=server)

ts_code = "300274.SZ"
today = datetime.datetime.now().strftime('%Y%m%d')
start_date = (datetime.datetime.now() - datetime.timedelta(days=40)).strftime('%Y%m%d')

print(f"Scouting {ts_code} via Tushare with custom server...")

try:
    # 1. Basic Info & Valuation
    # Note: daily_basic might need trade_date or it returns latest
    df_basic = pro.daily_basic(ts_code=ts_code)
    if not df_basic.empty:
        row = df_basic.sort_values('trade_date').iloc[-1]
        print(f"Date: {row['trade_date']}")
        print(f"Close: {row['close']}")
        print(f"PE(TTM): {row['pe_ttm']}")
        print(f"PB: {row['pb']}")
        print(f"Turnover: {row['turnover_rate']}%")
        print(f"Market Cap: {row['total_mv']} 亿")
        print(f"Volume Ratio: {row['volume_ratio']}")
    else:
        print("No daily basic info found.")

    # 2. Daily Price & Gains
    df_daily = pro.daily(ts_code=ts_code, start_date=start_date, end_date=today)
    if not df_daily.empty:
        df_daily = df_daily.sort_values('trade_date')
        latest_close = df_daily.iloc[-1]['close']
        pct_chg = df_daily.iloc[-1]['pct_chg']
        amount = df_daily.iloc[-1]['amount'] / 10000 # amount is in 1000 RMB
        print(f"Change%: {pct_chg}%")
        print(f"Amount: {amount:.2f} 亿")
        
        if len(df_daily) >= 5:
            gain_5 = (latest_close / df_daily.iloc[-5]['close'] - 1) * 100
            print(f"5-day Gain: {gain_5:.2f}%")
        if len(df_daily) >= 20:
            gain_20 = (latest_close / df_daily.iloc[-20]['close'] - 1) * 100
            print(f"20-day Gain: {gain_20:.2f}%")
            
        ma20 = df_daily['close'].tail(20).mean()
        print(f"MA20: {ma20:.2f}")

    # 3. Money Flow
    df_flow = pro.moneyflow(ts_code=ts_code, start_date=start_date, end_date=today)
    if not df_flow.empty:
        latest_flow = df_flow.sort_values('trade_date').iloc[-1]
        print(f"Main Flow (Net): {latest_flow['net_mf_amt']} 千元")

    # 4. Stock Info (Industry)
    df_info = pro.stock_basic(ts_code=ts_code)
    if not df_info.empty:
        print(f"Name: {df_info.iloc[0]['name']}")
        print(f"Industry: {df_info.iloc[0]['industry']}")

except Exception as e:
    print(f"Error: {e}")
