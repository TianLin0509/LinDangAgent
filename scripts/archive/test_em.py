import os
import sys
import requests

# UNSET PROXIES
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ["NO_PROXY"] = "*"

def em_get_basic_info(ts_code):
    """东方财富 HTTP 直接抓取实时行情"""
    try:
        code, market = ts_code.split(".")
        prefix = "1" if market == "SH" else "0"
        secid = f"{prefix}.{code}"
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get?"
            f"secid={secid}&fields=f23,f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,"
            f"f55,f57,f58,f60,f162,f167,f168,f170,f171,f43,f167,f23,f168,f45,f44,f46,f60,f47,f48,f57&ut=fa5fd1943c7b386f172d6893dbfba10b"
        )
        resp = requests.get(url, timeout=10)
        data = resp.json().get("data", {})
        if not data:
            return None
        return data
    except Exception as e:
        return str(e)

if __name__ == "__main__":
    data = em_get_basic_info("002027.SZ")
    print(data)
