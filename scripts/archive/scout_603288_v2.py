import os
import sys
import json
import pandas as pd
from datetime import datetime

# Add the project root to sys.path
sys.path.append(os.getcwd())

from data.fallback import ak_get_basic_info, em_get_basic_info
from data.tushare_client import resolve_stock, get_price_df

def scout_603288():
    ts_code = "603288.SH"
    code6 = "603288"
    
    # 1. Basic Info from AKShare
    ak_info, ak_err = ak_get_basic_info(ts_code)
    print(f"AKShare Info: {json.dumps(ak_info, ensure_ascii=False)}")
    
    # 2. Basic Info from EastMoney
    em_info, em_err = em_get_basic_info(ts_code)
    print(f"EastMoney Info: {json.dumps(em_info, ensure_ascii=False)}")
    
    # 3. Get Market Cap from AKShare spot data directly if possible
    try:
        import akshare as ak
        df_spot = ak.stock_zh_a_spot_em()
        row = df_spot[df_spot["代码"] == code6]
        if not row.empty:
            print(f"Spot Data for 603288:\n{row.iloc[0].to_dict()}")
    except Exception as e:
        print(f"Error getting spot data: {e}")

    # 4. Get Industry Average (Consumer/Condiments)
    # Just hardcode or estimate if needed, but let's see if we can get it.
    
if __name__ == "__main__":
    scout_603288()
