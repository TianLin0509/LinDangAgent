import akshare as ak
import pandas as pd

def get_300059_data():
    print("--- AKSHARE DATA ---")
    try:
        # 1. Spot data
        df_spot = ak.stock_zh_a_spot_em()
        row = df_spot[df_spot["代码"] == "300059"]
        if not row.empty:
            r = row.iloc[0].to_dict()
            print(f"Spot Data: {r}")
        
        # 2. History for MA and Gains
        df_hist = ak.stock_zh_a_hist(symbol="300059", period="daily", adjust="qfq")
        if not df_hist.empty:
            latest = df_hist.iloc[-1]
            print(f"Latest Hist: {latest.to_dict()}")
            # Gains
            if len(df_hist) >= 5:
                gain_5 = (df_hist.iloc[-1]['收盘'] / df_hist.iloc[-5]['收盘'] - 1) * 100
                print(f"5-day Gain: {gain_5:.2f}%")
            if len(df_hist) >= 20:
                gain_20 = (df_hist.iloc[-1]['收盘'] / df_hist.iloc[-20]['收盘'] - 1) * 100
                print(f"20-day Gain: {gain_20:.2f}%")
            
            # MA
            df_hist['MA5'] = df_hist['收盘'].rolling(5).mean()
            df_hist['MA20'] = df_hist['收盘'].rolling(20).mean()
            df_hist['MA60'] = df_hist['收盘'].rolling(60).mean()
            last_row = df_hist.iloc[-1]
            print(f"MA5: {last_row['MA5']:.2f}, MA20: {last_row['MA20']:.2f}, MA60: {last_row['MA60']:.2f}")

        # 3. Capital Flow
        df_flow = ak.stock_individual_fund_flow(symbol="300059", market="sz")
        if not df_flow.empty:
            print(f"Latest Flow: {df_flow.iloc[-1].to_dict()}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    get_300059_data()
