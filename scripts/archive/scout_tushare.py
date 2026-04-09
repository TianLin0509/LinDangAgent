import tushare as ts
import pandas as pd
from datetime import datetime, timedelta

def get_600585_tushare():
    token = "bc83655f008cdf037fddc36bac9bef0eeb31d2e55fc29047afa7d6f39910"
    ts.set_token(token)
    pro = ts.pro_api(token)
    
    ts_code = "600585.SH"
    today = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
    
    # Daily quote
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=today)
        if not df.empty:
            df = df.sort_values("trade_date")
            latest = df.iloc[-1]
            print(f"Price: {latest['close']}")
            print(f"Change%: {latest['pct_chg']}%")
            print(f"Turnover: {latest['amount'] * 1000} 元") # amount is in 1000 CNY
            
            latest_close = latest['close']
            if len(df) >= 5:
                gain_5 = (latest_close / df.iloc[-5]['close'] - 1) * 100
                print(f"5-day Gain: {gain_5:.2f}%")
            if len(df) >= 20:
                gain_20 = (latest_close / df.iloc[-20]['close'] - 1) * 100
                print(f"20-day Gain: {gain_20:.2f}%")
    except Exception as e:
        print(f"Daily Error: {e}")

    # Daily basic (PE, PB, MV)
    try:
        df_basic = pro.daily_basic(ts_code=ts_code, start_date=start_date, end_date=today)
        if not df_basic.empty:
            latest_basic = df_basic.iloc[0] # Usually first row is latest in daily_basic
            print(f"PE(TTM): {latest_basic['pe_ttm']}")
            print(f"PB: {latest_basic['pb']}")
            print(f"Total MV: {latest_basic['total_mv']}")
            print(f"Turnover Rate: {latest_basic['turnover_rate']}%")
            print(f"Volume Ratio: {latest_basic['volume_ratio']}")
    except Exception as e:
        print(f"Basic Error: {e}")

    # Capital flow
    try:
        df_flow = pro.moneyflow(ts_code=ts_code, start_date=start_date, end_date=today)
        if not df_flow.empty:
            latest_flow = df_flow.iloc[0]
            print(f"Main Net Inflow: {latest_flow['net_mf_amount'] * 10000} 元") # usually in 10000 CNY
    except Exception as e:
        print(f"Flow Error: {e}")

if __name__ == "__main__":
    get_600585_tushare()
