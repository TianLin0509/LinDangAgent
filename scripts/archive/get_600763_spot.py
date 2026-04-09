import akshare as ak
import pandas as pd
import sys
import io

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def get_tongce():
    try:
        df = ak.stock_zh_a_spot_em()
        target = df[df['代码'] == '600763']
        print(target.to_json(orient='records', force_ascii=False))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    get_tongce()
