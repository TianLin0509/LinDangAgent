import akshare as ak
import pandas as pd
import json

def get_data():
    stock_code = "600016"
    print(f"Fetching data for {stock_code}...")
    
    # Real-time quote
    try:
        quote = ak.stock_individual_info_em(symbol=stock_code)
        print("\nReal-time Quote (Individual Info):")
        print(quote.to_string(index=False))
    except Exception as e:
        print(f"Error quote: {e}")

    # Valuation
    try:
        indicator = ak.stock_a_lg_indicator(symbol=stock_code)
        if not indicator.empty:
            last_val = indicator.iloc[-1]
            print("\nValuation (LG):")
            print(last_val.to_string())
    except Exception as e:
        print(f"Error valuation: {e}")

    # Spot price & volume
    try:
        spot = ak.stock_zh_a_spot_em()
        target = spot[spot['代码'] == stock_code]
        if not target.empty:
            print("\nSpot Data:")
            print(target.to_string(index=False))
    except Exception as e:
        print(f"Error spot: {e}")

if __name__ == "__main__":
    get_data()
