"""备用数据源 — akshare + 东方财富直接抓取，Tushare 不可用时兜底"""

import pandas as pd
import re
from datetime import datetime, timedelta


# ══════════════════════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════════════════════

def _ts_code_to_ak_symbol(ts_code: str) -> str:
    """000858.SZ → sz000858, 600519.SH → sh600519"""
    code, market = ts_code.split(".")
    return market.lower() + code


def _ts_code_to_code6(ts_code: str) -> str:
    return ts_code.split(".")[0]


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _ndays_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


# ══════════════════════════════════════════════════════════════════════════════
# akshare 层
# ══════════════════════════════════════════════════════════════════════════════

def ak_get_stock_list() -> tuple[pd.DataFrame, str | None]:
    """通过 akshare 获取全部 A 股列表"""
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        # 列: code, name
        df = df.rename(columns={"code": "symbol"})
        df["ts_code"] = df["symbol"].apply(
            lambda c: f"{c}.SH" if c.startswith("6") else
                      (f"{c}.BJ" if c.startswith(("4", "8")) else f"{c}.SZ")
        )
        for col in ["industry", "area", "market"]:
            if col not in df.columns:
                df[col] = ""
        return df, None
    except Exception as e:
        return pd.DataFrame(), f"akshare 股票列表失败：{e}"


def ak_get_basic_info(ts_code: str) -> tuple[dict, str | None]:
    """通过 akshare 获取个股基本信息和实时行情"""
    try:
        import akshare as ak
        code6 = _ts_code_to_code6(ts_code)
        symbol = _ts_code_to_ak_symbol(ts_code)
        result = {}

        # 实时行情
        try:
            df_spot = ak.stock_zh_a_spot_em()
            row = df_spot[df_spot["代码"] == code6]
            if not row.empty:
                r = row.iloc[0]
                result.update({
                    "名称":       str(r.get("名称", "")),
                    "最新价(元)": str(r.get("最新价", "N/A")),
                    "市盈率TTM":  str(r.get("市盈率-动态", "N/A")),
                    "市净率PB":   str(r.get("市净率", "N/A")),
                    "换手率(%)":  str(r.get("换手率", "N/A")),
                    "行业":       str(r.get("行业", r.get("所处行业", ""))),
                })
        except Exception:
            pass

        # 个股信息
        try:
            info_df = ak.stock_individual_info_em(symbol=code6)
            if info_df is not None and not info_df.empty:
                info_dict = dict(zip(info_df["item"], info_df["value"]))
                if "行业" not in result or not result["行业"]:
                    result["行业"] = info_dict.get("行业", "")
                result["名称"] = info_dict.get("股票简称", result.get("名称", ""))
        except Exception:
            pass

        return result, None
    except Exception as e:
        return {}, f"akshare 基本信息失败：{e}"


def ak_get_price_df(ts_code: str, days: int = 140) -> tuple[pd.DataFrame, str | None]:
    """通过 akshare 获取日线数据"""
    try:
        import akshare as ak
        code6 = _ts_code_to_code6(ts_code)
        start = _ndays_ago(days)
        end = _today_str()

        df = ak.stock_zh_a_hist(
            symbol=code6, period="daily",
            start_date=start, end_date=end, adjust="qfq"
        )
        if df is None or df.empty:
            return pd.DataFrame(), "akshare 未获取到K线数据"

        df = df.rename(columns={
            "日期": "日期", "开盘": "开盘", "最高": "最高",
            "最低": "最低", "收盘": "收盘", "成交量": "成交量",
            "涨跌幅": "涨跌幅", "成交额": "成交额",
        })
        # 确保日期是字符串格式
        df["日期"] = df["日期"].astype(str).str.replace("-", "")
        df = df.sort_values("日期").reset_index(drop=True)
        return df, None
    except Exception as e:
        return pd.DataFrame(), f"akshare K线失败：{e}"


def ak_get_financial(ts_code: str) -> tuple[str, str | None]:
    """通过 akshare 获取财务指标"""
    try:
        import akshare as ak
        code6 = _ts_code_to_code6(ts_code)
        parts = []

        try:
            df = ak.stock_financial_abstract_ths(symbol=code6)
            if df is not None and not df.empty:
                parts.append("财务摘要（近5期）：\n" + df.head(5).to_string(index=False))
        except Exception:
            pass

        if not parts:
            try:
                df = ak.stock_financial_analysis_indicator(symbol=code6)
                if df is not None and not df.empty:
                    parts.append("财务指标（近5期）：\n" + df.head(5).to_string(index=False))
            except Exception:
                pass

        return ("\n\n".join(parts) if parts else "暂无财务数据（akshare）"), None
    except Exception as e:
        return "", f"akshare 财务数据失败：{e}"


def ak_get_capital_flow(ts_code: str) -> tuple[str, str | None]:
    """通过 akshare 获取资金流向"""
    try:
        import akshare as ak
        code6 = _ts_code_to_code6(ts_code)
        df = ak.stock_individual_fund_flow(stock=code6, market=ts_code.split(".")[1])
        if df is not None and not df.empty:
            return df.tail(15).to_string(index=False), None
        return "暂无数据", None
    except Exception as e:
        return "", f"akshare 资金流向失败：{e}"


# ══════════════════════════════════════════════════════════════════════════════
# 东方财富直接抓取层（保底）
# ══════════════════════════════════════════════════════════════════════════════

def _eastmoney_secid(ts_code: str) -> str:
    """000858.SZ → 0.000858, 600519.SH → 1.600519"""
    code, market = ts_code.split(".")
    prefix = "1" if market == "SH" else ("0" if market in ("SZ", "BJ") else "0")
    return f"{prefix}.{code}"


def em_get_basic_info(ts_code: str) -> tuple[dict, str | None]:
    """东方财富 HTTP 直接抓取实时行情"""
    try:
        import requests
        secid = _eastmoney_secid(ts_code)
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get?"
            f"secid={secid}&fields=f23,f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,"
            f"f55,f57,f58,f60,f162,f167,f168,f170,f171&ut=fa5fd1943c7b386f172d6893dbfba10b"
        )
        resp = requests.get(url, timeout=10)
        data = resp.json().get("data", {})
        if not data:
            return {}, "东方财富返回空数据"

        result = {
            "名称":       data.get("f58", ""),
            "最新价(元)": str(data.get("f43", "N/A") / 100) if data.get("f43") else "N/A",
            "换手率(%)":  str(data.get("f168", "N/A") / 100) if data.get("f168") else "N/A",
            "市盈率TTM":  str(data.get("f167", "N/A") / 100) if data.get("f167") else "N/A",
            "市净率PB":   str(data.get("f23", "N/A") / 100) if data.get("f23") else "N/A",
        }
        return result, None
    except Exception as e:
        return {}, f"东方财富抓取失败：{e}"


def em_get_price_df(ts_code: str, days: int = 140) -> tuple[pd.DataFrame, str | None]:
    """东方财富 HTTP 抓取 K 线"""
    try:
        import requests
        secid = _eastmoney_secid(ts_code)
        end = _today_str()
        start = _ndays_ago(days)

        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
            f"secid={secid}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            f"&klt=101&fqt=1&beg={start}&end={end}&ut=fa5fd1943c7b386f172d6893dbfba10b"
        )
        resp = requests.get(url, timeout=10)
        data = resp.json().get("data", {})
        klines = data.get("klines", [])

        if not klines:
            return pd.DataFrame(), "东方财富未获取到K线数据"

        rows = []
        for line in klines:
            parts = line.split(",")
            if len(parts) >= 7:
                rows.append({
                    "日期":   parts[0].replace("-", ""),
                    "开盘":   float(parts[1]),
                    "收盘":   float(parts[2]),
                    "最高":   float(parts[3]),
                    "最低":   float(parts[4]),
                    "成交量": float(parts[5]),
                    "成交额": float(parts[6]),
                    "涨跌幅": float(parts[8]) if len(parts) > 8 else 0,
                })

        df = pd.DataFrame(rows).sort_values("日期").reset_index(drop=True)
        return df, None
    except Exception as e:
        return pd.DataFrame(), f"东方财富K线抓取失败：{e}"
