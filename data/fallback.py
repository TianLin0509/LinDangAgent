"""备用数据源 — akshare + baostock + 东方财富直接抓取，Tushare 不可用时兜底"""

import logging
import pandas as pd
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _bs_safe_login():
    """baostock 安全登录，失败时抛异常而非静默。"""
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login 失败: {lg.error_msg}")
    return lg


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
        market_raw = ts_code.split(".")[1].lower()
        df = ak.stock_individual_fund_flow(stock=code6, market=market_raw)
        if df is not None and not df.empty:
            return df.tail(15).to_string(index=False), None
        return "暂无数据", None
    except Exception as e:
        return "", f"akshare 资金流向失败：{e}"


def ak_get_dragon_tiger(ts_code: str) -> tuple[str, str | None]:
    """通过 akshare 获取龙虎榜"""
    try:
        import akshare as ak
        # stock_lhb_detail_em 无参数获取全市场，然后过滤
        df = ak.stock_lhb_detail_em()
        if df is not None and not df.empty:
            code6 = _ts_code_to_code6(ts_code)
            # 尝试按代码过滤
            code_col = [c for c in df.columns if "代码" in str(c)]
            if code_col:
                filtered = df[df[code_col[0]].astype(str).str.contains(code6)]
                if not filtered.empty:
                    return f"龙虎榜（akshare）：\n{filtered.head(10).to_string(index=False)}", None
        return "近期无龙虎榜记录", None
    except Exception as e:
        logger.debug("[ak_get_dragon_tiger] 失败: %s", e)
        return "", f"akshare 龙虎榜失败：{e}"


def ak_get_holders_info(ts_code: str) -> tuple[str, str | None]:
    """通过 akshare 获取十大股东"""
    try:
        import akshare as ak
        code6 = _ts_code_to_code6(ts_code)
        df = ak.stock_main_stock_holder(stock=code6)
        if df is not None and not df.empty:
            latest_date = df.iloc[0].get("变动日期", df.iloc[0].get("日期", ""))
            latest = df[df.iloc[:, 0] == df.iloc[0, 0]] if not df.empty else df
            return f"十大股东（akshare，截至 {latest_date}）：\n{latest.head(10).to_string(index=False)}", None
        return "暂无十大股东数据", None
    except Exception as e:
        logger.debug("[ak_get_holders_info] 失败: %s", e)
        return "", f"akshare 十大股东失败：{e}"


def ak_get_pledge_info(ts_code: str) -> tuple[str, str | None]:
    """通过 akshare 获取股权质押"""
    try:
        import akshare as ak
        code6 = _ts_code_to_code6(ts_code)
        # stock_gpzy_pledge_ratio_em 不接受 symbol 参数，获取全市场后过滤
        df = ak.stock_gpzy_pledge_ratio_em()
        if df is not None and not df.empty:
            code_col = [c for c in df.columns if "代码" in str(c) or "code" in str(c).lower()]
            if code_col:
                filtered = df[df[code_col[0]].astype(str).str.contains(code6)]
                if not filtered.empty:
                    return f"股权质押（akshare）：\n{filtered.head(5).to_string(index=False)}", None
        return "暂无质押数据", None
    except Exception as e:
        logger.debug("[ak_get_pledge_info] 失败: %s", e)
        return "", f"akshare 质押数据失败：{e}"


def ak_get_fund_holdings(ts_code: str) -> tuple[str, str | None]:
    """通过 akshare 获取基金持仓"""
    try:
        import akshare as ak
        code6 = _ts_code_to_code6(ts_code)
        df = ak.stock_report_fund_hold(symbol=code6)
        if df is not None and not df.empty:
            return f"基金持仓（akshare）：\n{df.head(20).to_string(index=False)}", None
        return "暂无基金持仓数据", None
    except Exception as e:
        logger.debug("[ak_get_fund_holdings] 失败: %s", e)
        return "", f"akshare 基金持仓失败：{e}"


# ══════════════════════════════════════════════════════════════════════════════
# baostock 层
# ══════════════════════════════════════════════════════════════════════════════

def _ts_code_to_bs_code(ts_code: str) -> str:
    """000858.SZ → sz.000858, 600519.SH → sh.600519"""
    code, market = ts_code.split(".")
    return f"{market.lower()}.{code}"


def bs_get_price_df(ts_code: str, days: int = 140) -> tuple[pd.DataFrame, str | None]:
    """通过 baostock 获取日K线数据"""
    try:
        import baostock as bs
        bs_code = _ts_code_to_bs_code(ts_code)
        start = _ndays_ago(days)
        end = _today_str()
        # 转为 baostock 日期格式 YYYY-MM-DD
        start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:]}"
        end_fmt = f"{end[:4]}-{end[4:6]}-{end[6:]}"

        lg = _bs_safe_login()
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount,pctChg",
                start_date=start_fmt, end_date=end_fmt,
                frequency="d", adjustflag="2",  # 前复权
            )
            if rs.error_code != "0":
                return pd.DataFrame(), f"baostock 查询失败: {rs.error_msg}"

            data = []
            while rs.error_code == "0" and rs.next():
                data.append(rs.get_row_data())

            if not data:
                return pd.DataFrame(), "baostock 未获取到K线数据"

            df = pd.DataFrame(data, columns=rs.fields)
            df = df.rename(columns={
                "date": "日期", "open": "开盘", "high": "最高",
                "low": "最低", "close": "收盘", "volume": "成交量",
                "amount": "成交额", "pctChg": "涨跌幅",
            })
            # 转换数值类型
            for col in ["开盘", "最高", "最低", "收盘", "成交量", "成交额", "涨跌幅"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            # 日期格式统一为 YYYYMMDD
            df["日期"] = df["日期"].str.replace("-", "")
            df = df.sort_values("日期").reset_index(drop=True)
            return df, None
        finally:
            bs.logout()
    except Exception as e:
        logger.debug("[bs_get_price_df] baostock K线失败: %s", e)
        return pd.DataFrame(), f"baostock K线失败：{e}"


def bs_get_basic_info(ts_code: str) -> tuple[dict, str | None]:
    """通过 baostock 获取个股基本信息"""
    try:
        import baostock as bs
        bs_code = _ts_code_to_bs_code(ts_code)

        lg = _bs_safe_login()
        try:
            rs = bs.query_stock_basic(code=bs_code)
            if rs.error_code != "0":
                return {}, f"baostock 查询失败: {rs.error_msg}"

            data = []
            while rs.error_code == "0" and rs.next():
                data.append(rs.get_row_data())
            if not data:
                return {}, "baostock 无基本信息"

            df = pd.DataFrame(data, columns=rs.fields)
            if df.empty:
                return {}, "baostock 无基本信息"

            row = df.iloc[-1]
            result = {
                "名称": row.get("code_name", ""),
                "行业": row.get("industry", ""),
            }
            return result, None
        finally:
            bs.logout()
    except Exception as e:
        logger.debug("[bs_get_basic_info] baostock 基本信息失败: %s", e)
        return {}, f"baostock 基本信息失败：{e}"


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


# ══════════════════════════════════════════════════════════════════════════════
# v3.0 新增：baostock 扩展（杜邦/业绩预告/快报/盈利/成长/偿债/运营）
# ══════════════════════════════════════════════════════════════════════════════

def _bs_recent_quarters(n: int = 4) -> list[tuple[int, int]]:
    """返回最近 N 个季度的 (year, quarter) 列表"""
    from datetime import datetime
    now = datetime.now()
    y, m = now.year, now.month
    q = (m - 1) // 3  # 当前季度（0-based，未完成的）
    results = []
    for _ in range(n):
        if q <= 0:
            y -= 1
            q = 4
        results.append((y, q))
        q -= 1
    return results


def _bs_query_to_df(bs_module, code: str, fields: str, quarters: list) -> pd.DataFrame:
    """通用 baostock 多季度查询，合并为 DataFrame"""
    all_rows = []
    fields_list = None
    for year, quarter in quarters:
        try:
            rs = bs_module(code=code, year=year, quarter=quarter)
            while rs.error_code == "0" and rs.next():
                all_rows.append(rs.get_row_data())
            if fields_list is None:
                fields_list = rs.fields
        except Exception:
            continue
    if not all_rows:
        return pd.DataFrame()
    return pd.DataFrame(all_rows, columns=fields_list)


def bs_get_dupont_data(ts_code: str) -> tuple[str, str | None]:
    """baostock 杜邦分析数据"""
    try:
        import baostock as bs
        bs_code = _ts_code_to_bs_code(ts_code)
        lg = _bs_safe_login()
        try:
            quarters = _bs_recent_quarters(4)
            rows = []
            for year, quarter in quarters:
                rs = bs.query_dupont_data(code=bs_code, year=year, quarter=quarter)
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
                fields = rs.fields
            if not rows:
                return "杜邦分析：数据不足", None
            df = pd.DataFrame(rows, columns=fields)
            return f"杜邦分析（baostock）：\n{df.to_string(index=False)}", None
        finally:
            bs.logout()
    except Exception as e:
        logger.debug("[bs_get_dupont_data] %s", e)
        return "", f"baostock杜邦失败：{e}"


def bs_get_forecast(ts_code: str) -> pd.DataFrame:
    """baostock 业绩预告"""
    try:
        import baostock as bs
        bs_code = _ts_code_to_bs_code(ts_code)
        start = f"{datetime.now().year - 1}-01-01"
        end = datetime.now().strftime("%Y-%m-%d")
        lg = _bs_safe_login()
        try:
            rs = bs.query_forecast_report(code=bs_code, start_date=start, end_date=end)
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows, columns=rs.fields)
            # 映射列名兼容 Tushare 下游
            col_map = {"profitForcastType": "type", "profitForcastAbstract": "summary",
                       "profitForcastChgPctUp": "p_change_max", "profitForcastChgPctDwn": "p_change_min"}
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            logger.info("[bs_get_forecast] baostock 业绩预告成功: %d条", len(df))
            return df.head(4)
        finally:
            bs.logout()
    except Exception as e:
        logger.debug("[bs_get_forecast] %s", e)
        return pd.DataFrame()


def bs_get_express(ts_code: str) -> pd.DataFrame:
    """baostock 业绩快报"""
    try:
        import baostock as bs
        bs_code = _ts_code_to_bs_code(ts_code)
        start = f"{datetime.now().year - 1}-01-01"
        end = datetime.now().strftime("%Y-%m-%d")
        lg = _bs_safe_login()
        try:
            rs = bs.query_performance_express_report(code=bs_code, start_date=start, end_date=end)
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows, columns=rs.fields)
            logger.info("[bs_get_express] baostock 业绩快报成功: %d条", len(df))
            return df.head(4)
        finally:
            bs.logout()
    except Exception as e:
        logger.debug("[bs_get_express] %s", e)
        return pd.DataFrame()


def bs_get_profit_data(ts_code: str) -> pd.DataFrame:
    """baostock 盈利能力指标（ROE/净利率/毛利率/EPS）"""
    try:
        import baostock as bs
        bs_code = _ts_code_to_bs_code(ts_code)
        lg = _bs_safe_login()
        try:
            rows, fields = [], None
            for year, quarter in _bs_recent_quarters(8):
                rs = bs.query_profit_data(code=bs_code, year=year, quarter=quarter)
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
                fields = rs.fields
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows, columns=fields)
            for col in df.columns:
                if col not in ("code", "pubDate", "statDate"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df
        finally:
            bs.logout()
    except Exception as e:
        logger.debug("[bs_get_profit_data] %s", e)
        return pd.DataFrame()


def bs_get_growth_data(ts_code: str) -> pd.DataFrame:
    """baostock 成长能力指标（YOY系列）"""
    try:
        import baostock as bs
        bs_code = _ts_code_to_bs_code(ts_code)
        lg = _bs_safe_login()
        try:
            rows, fields = [], None
            for year, quarter in _bs_recent_quarters(8):
                rs = bs.query_growth_data(code=bs_code, year=year, quarter=quarter)
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
                fields = rs.fields
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows, columns=fields)
            for col in df.columns:
                if col not in ("code", "pubDate", "statDate"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df
        finally:
            bs.logout()
    except Exception as e:
        logger.debug("[bs_get_growth_data] %s", e)
        return pd.DataFrame()


def bs_get_balance_data(ts_code: str) -> pd.DataFrame:
    """baostock 偿债能力指标（流动比率/速动比率/资产负债率）"""
    try:
        import baostock as bs
        bs_code = _ts_code_to_bs_code(ts_code)
        lg = _bs_safe_login()
        try:
            rows, fields = [], None
            for year, quarter in _bs_recent_quarters(4):
                rs = bs.query_balance_data(code=bs_code, year=year, quarter=quarter)
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
                fields = rs.fields
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows, columns=fields)
            for col in df.columns:
                if col not in ("code", "pubDate", "statDate"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df
        finally:
            bs.logout()
    except Exception as e:
        logger.debug("[bs_get_balance_data] %s", e)
        return pd.DataFrame()


def bs_get_operation_data(ts_code: str) -> pd.DataFrame:
    """baostock 运营能力指标（周转率系列）"""
    try:
        import baostock as bs
        bs_code = _ts_code_to_bs_code(ts_code)
        lg = _bs_safe_login()
        try:
            rows, fields = [], None
            for year, quarter in _bs_recent_quarters(4):
                rs = bs.query_operation_data(code=bs_code, year=year, quarter=quarter)
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
                fields = rs.fields
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows, columns=fields)
            for col in df.columns:
                if col not in ("code", "pubDate", "statDate"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df
        finally:
            bs.logout()
    except Exception as e:
        logger.debug("[bs_get_operation_data] %s", e)
        return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
# v3.0 新增：东财 datacenter-web 接口（不同于被限流的 push2 子域名）
# ══════════════════════════════════════════════════════════════════════════════

def _em_datacenter_get(report_name: str, filter_str: str,
                       sort_col: str = "", page_size: int = 15) -> pd.DataFrame:
    """东方财富 datacenter-web 通用查询"""
    try:
        import requests
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": report_name,
            "columns": "ALL",
            "filter": filter_str,
            "pageSize": page_size,
            "sortColumns": sort_col,
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
        }
        headers = {"Referer": "https://data.eastmoney.com"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        if data.get("success") and data.get("result", {}).get("data"):
            return pd.DataFrame(data["result"]["data"])
        return pd.DataFrame()
    except Exception as e:
        logger.debug("[_em_datacenter_get] %s %s: %s", report_name, filter_str, e)
        return pd.DataFrame()


def em_get_holdernumber_dc(ts_code: str) -> tuple[str, str | None]:
    """东财datacenter 股东人数"""
    code6 = _ts_code_to_code6(ts_code)
    df = _em_datacenter_get("RPT_HOLDERNUM_DET", f'(SECURITY_CODE="{code6}")', "END_DATE")
    if df.empty:
        return "", "datacenter股东人数为空"
    cols = ["END_DATE", "HOLDER_NUM", "PRE_HOLDER_NUM", "HOLDER_NUM_CHANGE", "HOLDER_NUM_RATIO"]
    available = [c for c in cols if c in df.columns]
    return f"股东人数（datacenter）：\n{df[available].head(8).to_string(index=False)}", None


def em_get_dragon_tiger_dc(ts_code: str) -> tuple[str, str | None]:
    """东财datacenter 龙虎榜"""
    code6 = _ts_code_to_code6(ts_code)
    df = _em_datacenter_get("RPT_DAILYBILLBOARD_DETAILSNEW", f'(SECURITY_CODE="{code6}")', "TRADE_DATE")
    if df.empty:
        return "近期无龙虎榜记录", None
    cols = ["TRADE_DATE", "SECURITY_NAME_ABBR", "DEAL_NET_AMT", "BUY_AMT", "SELL_AMT", "OPERATEDEPT_NAME"]
    available = [c for c in cols if c in df.columns]
    return f"龙虎榜（datacenter）：\n{df[available].head(10).to_string(index=False)}", None


def em_get_margin_dc(ts_code: str) -> tuple[str, str | None]:
    """东财datacenter 融资融券"""
    code6 = _ts_code_to_code6(ts_code)
    df = _em_datacenter_get("RPTA_WEB_RZRQ_GGMX", f'(SCODE="{code6}")', "DATE")
    if df.empty:
        return "", "datacenter融资融券为空"
    cols = ["DATE", "RZYE", "RZMRE", "RZCHE", "RQYE", "RQMCL", "RQCHL"]
    available = [c for c in cols if c in df.columns]
    return f"融资融券（datacenter）：\n{df[available].head(15).to_string(index=False)}", None


def em_get_block_trade_dc(ts_code: str) -> tuple[str, str | None]:
    """东财datacenter 大宗交易"""
    code6 = _ts_code_to_code6(ts_code)
    df = _em_datacenter_get("RPT_DATA_BLOCKTRADE", f'(SECURITY_CODE="{code6}")', "TRADE_DATE")
    if df.empty:
        return "", "datacenter大宗交易为空"
    cols = ["TRADE_DATE", "DEAL_PRICE", "DEAL_VOL", "DEAL_AMT", "PREMIUM_RATIO", "BUYER", "SELLER"]
    available = [c for c in cols if c in df.columns]
    return f"大宗交易（datacenter）：\n{df[available].head(10).to_string(index=False)}", None


def em_get_capital_flow_hist(ts_code: str) -> tuple[str, str | None]:
    """东财 push2his 资金流向（不同于被限流的 push2）"""
    try:
        import requests
        secid = _eastmoney_secid(ts_code)
        url = f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        params = {"secid": secid, "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56,f57",
                  "klt": 101, "lmt": 20, "ut": "fa5fd1943c7b386f172d6893dbfba10b"}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json().get("data", {})
        klines = data.get("klines", [])
        if not klines:
            return "", "push2his资金流向为空"
        lines = ["日期 | 主力净流入 | 小单净流入 | 中单净流入 | 大单净流入 | 超大单净流入"]
        for k in klines[-15:]:
            lines.append(k)
        return f"资金流向（push2his）：\n" + "\n".join(lines), None
    except Exception as e:
        logger.debug("[em_get_capital_flow_hist] %s", e)
        return "", f"push2his资金流向失败：{e}"


# ══════════════════════════════════════════════════════════════════════════════
# v3.0 新增：Sina Finance（无鉴权，basic_info 终极兜底）
# ══════════════════════════════════════════════════════════════════════════════

def sina_get_realtime_quote(ts_code: str) -> tuple[dict, str | None]:
    """新浪财经实时行情（GBK编码，无鉴权，极其稳定）"""
    try:
        import requests
        code6 = _ts_code_to_code6(ts_code)
        market = ts_code.split(".")[1].lower() if "." in ts_code else ("sh" if code6.startswith("6") else "sz")
        symbol = f"{market}{code6}"
        url = f"https://hq.sinajs.cn/list={symbol}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        resp = requests.get(url, headers=headers, timeout=5)
        text = resp.content.decode("gbk", errors="ignore")
        # 格式: var hq_str_sh600519="贵州茅台,1800.00,1790.00,...";
        parts = text.split('"')
        if len(parts) < 2 or not parts[1]:
            return {}, "新浪行情数据为空"
        fields = parts[1].split(",")
        if len(fields) < 32:
            return {}, "新浪行情字段不足"
        result = {
            "名称": fields[0],
            "最新价(元)": fields[3],
            "开盘价": fields[1],
            "昨收价": fields[2],
            "最高价": fields[4],
            "最低价": fields[5],
            "成交量(手)": fields[8],
            "成交额(元)": fields[9],
            "换手率(%)": "N/A",  # 新浪不直接提供
            "日期": fields[30],
            "时间": fields[31],
        }
        return result, None
    except Exception as e:
        logger.debug("[sina_get_realtime_quote] %s", e)
        return {}, f"新浪行情失败：{e}"
