import tushare as ts
import pandas as pd
from datetime import datetime, timedelta

TOKEN = "bc83655f008cdf037fddc36bac9bef0eeb31d2e55fc29047afa7d6f39910"
URL = "http://lianghua.nanyangqiankun.top"

def get_data():
    ts.set_token(TOKEN)
    pro = ts.pro_api(TOKEN)
    # Patch for custom URL
    pro._DataApi__http_url = URL
    
    ts_code = "600016.SH"
    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=40)).strftime("%Y%m%d")
    
    print(f"Fetching {ts_code} from {start} to {today}...")
    
    # 1. Daily data (Price, Change, Amount, Volume)
    try:
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=today)
        if not df.empty:
            print("\nDaily Data (Last 2):")
            print(df.head(2).to_string(index=False))
            
            # Calculate returns
            if len(df) >= 20:
                c_now = df.iloc[0]['close']
                c_5 = df.iloc[4]['close'] if len(df) > 4 else df.iloc[-1]['close']
                c_20 = df.iloc[19]['close'] if len(df) > 19 else df.iloc[-1]['close']
                print(f"5-day change: {(c_now/c_5 - 1)*100:.2f}%")
                print(f"20-day change: {(c_now/c_20 - 1)*100:.2f}%")
    except Exception as e:
        print(f"Error daily: {e}")

    # 2. Daily basic (PE, PB, Turnover, Volume Ratio)
    try:
        df_b = pro.daily_basic(ts_code=ts_code, start_date=start, end_date=today)
        if not df_b.empty:
            print("\nDaily Basic (Last 1):")
            print(df_b.head(1).to_string(index=False))
    except Exception as e:
        print(f"Error daily basic: {e}")

    # 3. Capital flow
    try:
        df_f = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=today)
        if not df_f.empty:
            print("\nMoneyflow (Last 1):")
            print(df_f.head(1).to_string(index=False))
    except Exception as e:
        print(f"Error moneyflow: {e}")

if __name__ == "__main__":
    get_data()
