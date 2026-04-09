import sys
import os
import json

# Add the project root to sys.path
sys.path.append(os.getcwd())

from data.tushare_client import get_basic_info

def scout_val():
    ts_code = "600763.SH"
    basic_info, err = get_basic_info(ts_code)
    if err:
        print(f"Error: {err}")
    else:
        print(f"Basic Info: {json.dumps(basic_info, ensure_ascii=False, indent=2)}")

if __name__ == "__main__":
    scout_val()
