"""数据层 — Tushare 优先，akshare 备用，东方财富保底

Tushare 探测在后台线程中执行，不阻塞首屏加载。
探测完成前所有请求自动走 fallback，探测成功后自动切换回 Tushare。
"""

import logging
import threading
import pandas as pd
import re
import os
from datetime import datetime, timedelta
from utils.app_config import get_secret
from utils.cache_compat import compat_cache

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# LAZY INIT — 后台线程探测，不阻塞 import
# ══════════════════════════════════════════════════════════════════════════════

TUSHARE_TOKEN = get_secret("TUSHARE_TOKEN", "")
TUSHARE_URL   = get_secret("TUSHARE_URL", "http://lianghua.nanyangqiankun.top")

_init_lock = threading.Lock()
_pro = None
_ts_err = ""
_init_done = threading.Event()
_data_source = "fallback"


def _init_tushare_bg():
    """后台探测 Tushare 可用性（超时 5 秒）"""
    global _pro, _ts_err, _data_source

    try:
        import tushare as ts
        import requests as _req

        ts.set_token(TUSHARE_TOKEN)
        p = ts.pro_api(TUSHARE_TOKEN)
        p._DataApi__token = TUSHARE_TOKEN
        p._DataApi__http_url = TUSHARE_URL

        _orig_post = _req.post
        def _patched_post(*a, **kw):
            kw.setdefault("timeout", 5)
            return _orig_post(*a, **kw)
        _req.post = _patched_post

        try:
            test = p.trade_cal(exchange="SSE", start_date="20240101", end_date="20240103")
            if test is not None and not test.empty:
                with _init_lock:
                    _pro = p
                    _ts_err = ""
                    _data_source = "tushare"
                logger.info("[tushare] 后台探测成功，Tushare 可用")
                return
        except Exception:
            pass
        with _init_lock:
            _ts_err = "Tushare 接口返回空，已自动切换备用数据源"
        logger.debug("[tushare] 后台探测失败，使用 fallback")
    except Exception as e:
        with _init_lock:
            _ts_err = f"Tushare 初始化失败：{e}"
        logger.debug("[tushare] 初始化异常: %s", e)
    finally:
        _init_done.set()


# 立即启动后台探测线程
_init_thread = threading.Thread(target=_init_tushare_bg, daemon=True)
_init_thread.start()


def _get_pro():
    """获取 Tushare pro 实例（如果后台探测已完成）"""
    with _init_lock:
        return _pro


def ts_ok() -> bool:
    """Tushare 或备用源是否可用（始终返回 True，因为有三层兜底）"""
    return True


def get_ts_error() -> str:
    # 探测未完成时不显示错误（避免一闪而过的警告）
    if not _init_done.is_set():
        return ""
    with _init_lock:
        return _ts_err or ""


def get_data_source() -> str:
    """返回当前实际使用的数据源"""
    with _init_lock:
        return _data_source


def get_pro():
    return _get_pro()


# ══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════════

def to_ts_code(code6: str) -> str:
    code6 = code6.strip()
    if "." in code6:
        return code6.upper()
    if code6.startswith("6"):
        return f"{code6}.SH"
    if code6.startswith(("4", "8")):
        return f"{code6}.BJ"
    return f"{code6}.SZ"


def to_code6(ts_code: str) -> str:
    return ts_code.split(".")[0] if "." in ts_code else ts_code


def today() -> str:
    return datetime.now().strftime("%Y%m%d")


def ndays_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


# ══════════════════════════════════════════════════════════════════════════════
# 通用重试
# ══════════════════════════════════════════════════════════════════════════════

def _retry_call(fn, retries=3, delay=1):
    import time as _time
    import random
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:
            if attempt < retries:
                _time.sleep(delay + random.uniform(0, delay * 0.3))
                delay *= 2
            else:
                raise


# ══════════════════════════════════════════════════════════════════════════════
# 三层兜底调度器
# ══════════════════════════════════════════════════════════════════════════════

def _try_with_fallback(tushare_fn, akshare_fn=None, eastmoney_fn=None, baostock_fn=None, sina_fn=None, label="数据"):
    """依次尝试 Tushare → 东方财富 → AKShare → Baostock，返回第一个成功的结果

    优先级依据实测：东方财富最快最稳(0.23s)，AKShare批量不稳，Baostock稳但慢(1.75s)
    """
    global _data_source

    # 第一层：Tushare（数据最全）
    if _get_pro() is not None:
        try:
            result, err = tushare_fn()
            if err is None:
                with _init_lock:
                    _data_source = "tushare"
                return result, None
        except Exception as e:
            logger.debug("[%s] tushare 失败: %s", label, e)

    # 第二层：东方财富（最快最稳）
    if eastmoney_fn is not None:
        try:
            result, err = eastmoney_fn()
            if err is None:
                with _init_lock:
                    _data_source = "eastmoney"
                return result, None
        except Exception as e:
            logger.debug("[%s] eastmoney 失败: %s", label, e)

    # 第三层：AKShare（数据丰富，批量时不稳定）
    if akshare_fn is not None:
        try:
            result, err = akshare_fn()
            if err is None:
                with _init_lock:
                    _data_source = "akshare"
                return result, None
        except Exception as e:
            logger.debug("[%s] akshare 失败: %s", label, e)

    # 第四层：Baostock（最稳兜底，免费无限流，但较慢）
    if baostock_fn is not None:
        try:
            result, err = baostock_fn()
            if err is None:
                with _init_lock:
                    _data_source = "baostock"
                return result, None
        except Exception as e:
            logger.debug("[%s] baostock 失败: %s", label, e)

    # 第五层：Sina Finance（无鉴权，极稳定）
    if sina_fn is not None:
        try:
            result, err = sina_fn()
            if err is None:
                with _init_lock:
                    _data_source = "sina"
                return result, None
        except Exception as e:
            logger.debug("[%s] sina 失败: %s", label, e)

    with _init_lock:
        _data_source = "unavailable"
    # 根据 label 返回符合调用方类型期望的默认值
    if label in ("K线", "历史估值"):
        default = pd.DataFrame()
    elif label == "基本信息":
        default = {}
    else:
        default = ""
    return default, f"所有数据源均不可用（{label}）"


# ══════════════════════════════════════════════════════════════════════════════
# 数据获取（带三层兜底）
# ══════════════════════════════════════════════════════════════════════════════

_STOCK_LIST_CSV = os.path.join(os.path.dirname(__file__), "stock_list.csv")


@compat_cache(ttl=3600, show_spinner=False)
def load_stock_list() -> tuple[pd.DataFrame, str | None]:
    """优先读本地 CSV → Tushare API → akshare"""
    if os.path.exists(_STOCK_LIST_CSV):
        try:
            df = None
            last_error = None
            for encoding in ("utf-8", "utf-8-sig", "gbk"):
                try:
                    df = pd.read_csv(_STOCK_LIST_CSV, encoding=encoding)
                    logger.info("[load_stock_list] loaded local csv with encoding=%s rows=%s", encoding, len(df))
                    break
                except Exception as exc:
                    last_error = exc
            if df is None:
                raise last_error or RuntimeError("failed to load stock_list.csv")
            for col in ["ts_code", "symbol", "name", "industry", "area", "market"]:
                if col not in df.columns:
                    df[col] = ""
            df["ts_code"] = df["ts_code"].astype(str).str.strip().str.upper()
            df["symbol"] = df["symbol"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
            for col in ["name", "industry", "area", "market"]:
                df[col] = df[col].astype(str).str.strip()
            return df, None
        except Exception as e:
            logger.debug("[load_stock_list] CSV 读取失败: %s", e)

    if _get_pro() is not None:
        try:
            df = _retry_call(
                lambda: _get_pro().stock_basic(
                    exchange="", list_status="L",
                    fields="ts_code,symbol,name,industry,area,market"
                ),
                retries=5, delay=2,
            )
            if df is not None and not df.empty:
                return df, None
        except Exception as e:
            logger.debug("[load_stock_list] tushare 失败: %s", e)

    # akshare 兜底
    from data.fallback import ak_get_stock_list
    return ak_get_stock_list()


def resolve_stock(query: str) -> tuple[str, str, str | None]:
    """→ (ts_code, name, err)"""
    query = query.strip()
    if not query:
        return "", "", "请输入股票代码或名称"
    df, err = load_stock_list()

    def _is_code_like(value: str) -> bool:
        value = value.strip().upper()
        return bool(
            re.fullmatch(r"\d{6}", value)
            or re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", value)
        )

    if err:
        if _is_code_like(query):
            ts_code = to_ts_code(re.sub(r"\D", "", query) or query)
            return ts_code, query, f"股票列表获取失败（{err}），已按代码直接查询"
        return "", query, f"股票列表获取失败（{err}），且未识别到有效股票代码"

    if not df.empty:
        if re.match(r"^\d{6}$", query):
            m = df[df["symbol"].astype(str) == query]
            if not m.empty:
                return m.iloc[0]["ts_code"], m.iloc[0]["name"], None
            return "", query, f"未识别到股票：{query}"

        if re.match(r"^\d{6}\.(SH|SZ|BJ)$", query, re.IGNORECASE):
            normalized = query.upper()
            m = df[df["ts_code"].astype(str).str.upper() == normalized]
            if not m.empty:
                return m.iloc[0]["ts_code"], m.iloc[0]["name"], None
            return "", query, f"未识别到股票：{query}"

        m = df[df["name"].astype(str).str.contains(query, na=False, regex=False)]
        if not m.empty:
            return m.iloc[0]["ts_code"], m.iloc[0]["name"], None

        m = df[df["symbol"].astype(str).str.contains(query, na=False, regex=False)]
        if not m.empty:
            return m.iloc[0]["ts_code"], m.iloc[0]["name"], None

    return "", query, f"未识别到股票：{query}"


@compat_cache(ttl=600, show_spinner=False)
def get_basic_info(ts_code: str) -> tuple[dict, str | None]:
    from data.fallback import ak_get_basic_info, em_get_basic_info

    def _tushare():
        if _get_pro() is None:
            return {}, _ts_err
        result = {}
        err_msgs = []

        df_list, _ = load_stock_list()
        if not df_list.empty:
            m = df_list[df_list["ts_code"] == ts_code]
            if not m.empty:
                row = m.iloc[0]
                result.update({"名称": row.get("name", ""), "行业": row.get("industry", ""),
                               "地区": row.get("area", ""), "市场": row.get("market", "")})
        try:
            df_db = _retry_call(
                lambda: _get_pro().daily_basic(
                    ts_code=ts_code, start_date=ndays_ago(10), end_date=today(),
                    fields="ts_code,trade_date,close,pe_ttm,pb,ps_ttm,total_mv,turnover_rate,volume_ratio"
                ),
                retries=3, delay=1,
            )
            if df_db is not None and not df_db.empty:
                row = df_db.iloc[0]
                mv = row.get("total_mv")
                result.update({
                    "最新价(元)":   str(row.get("close", "N/A")),
                    "市盈率TTM":    str(row.get("pe_ttm", "N/A")),
                    "市净率PB":     str(row.get("pb", "N/A")),
                    "市销率PS":     str(row.get("ps_ttm", "N/A")),
                    "总市值(万元)": f"{float(mv):,.0f}" if mv else "N/A",
                    "换手率(%)":    str(row.get("turnover_rate", "N/A")),
                    "量比":         str(row.get("volume_ratio", "N/A")),
                })
                return result, None
        except Exception as e:
            err_msgs.append(f"估值数据：{e}")

        if result:
            return result, ("; ".join(err_msgs) if err_msgs else None)
        return {}, "; ".join(err_msgs) if err_msgs else "Tushare 无数据"

    from data.fallback import bs_get_basic_info, sina_get_realtime_quote
    return _try_with_fallback(
        _tushare,
        lambda: ak_get_basic_info(ts_code),
        lambda: em_get_basic_info(ts_code),
        baostock_fn=lambda: bs_get_basic_info(ts_code),
        sina_fn=lambda: sina_get_realtime_quote(ts_code),
        label="基本信息",
    )


@compat_cache(ttl=300, show_spinner=False)
def get_price_df(ts_code: str, days: int = 140) -> tuple[pd.DataFrame, str | None]:
    from data.fallback import ak_get_price_df, em_get_price_df, bs_get_price_df

    def _tushare():
        if _get_pro() is None:
            return pd.DataFrame(), _ts_err
        df = _retry_call(
            lambda: _get_pro().daily(ts_code=ts_code, start_date=ndays_ago(days), end_date=today()),
            retries=3, delay=1,
        )
        if df is None or df.empty:
            return pd.DataFrame(), "未获取到K线数据"
        df = df.sort_values("trade_date").reset_index(drop=True)
        df = df.rename(columns={
            "trade_date": "日期", "open": "开盘", "high": "最高",
            "low": "最低", "close": "收盘", "vol": "成交量",
            "pct_chg": "涨跌幅", "amount": "成交额",
        })
        return df, None

    return _try_with_fallback(
        _tushare,
        lambda: ak_get_price_df(ts_code, days),
        lambda: em_get_price_df(ts_code, days),
        baostock_fn=lambda: bs_get_price_df(ts_code, days),
        label="K线",
    )


@compat_cache(ttl=600, show_spinner=False)
def get_financial(ts_code: str) -> tuple[str, str | None]:
    from data.fallback import ak_get_financial

    def _tushare():
        if _get_pro() is None:
            return "", _ts_err
        parts, errs = [], []
        try:
            df = _retry_call(
                lambda: _get_pro().fina_indicator(
                    ts_code=ts_code,
                    fields="end_date,roe,roa,grossprofit_margin,netprofit_margin,"
                           "debt_to_assets,current_ratio,quick_ratio,revenue_yoy,netprofit_yoy,basic_eps"
                ),
                retries=3, delay=1,
            )
            if df is not None and not df.empty:
                parts.append("核心财务指标（近5期）：\n" + df.head(5).to_string(index=False))
        except Exception as e:
            errs.append(f"财务指标：{e}")
        try:
            rpt = str((datetime.now().year - 1) * 10000 + 1231)
            df2 = _retry_call(
                lambda: _get_pro().income(
                    ts_code=ts_code, start_date=str(int(rpt) - 30000), end_date=rpt,
                    fields="end_date,total_revenue,operate_profit,n_income,n_income_attr_p"
                ),
                retries=3, delay=1,
            )
            if df2 is not None and not df2.empty:
                parts.append("利润表摘要（近4期）：\n" + df2.head(4).to_string(index=False))
        except Exception as e:
            errs.append(f"利润表：{e}")

        if parts:
            return "\n\n".join(parts), None
        return "", "; ".join(errs) if errs else "Tushare 无财务数据"

    return _try_with_fallback(
        _tushare,
        lambda: ak_get_financial(ts_code),
        None,
        label="财务",
    )


@compat_cache(ttl=300, show_spinner=False)
def get_capital_flow(ts_code: str) -> tuple[str, str | None]:
    from data.fallback import ak_get_capital_flow

    def _tushare():
        if _get_pro() is None:
            return "", _ts_err
        df = _retry_call(
            lambda: _get_pro().moneyflow(
                ts_code=ts_code, start_date=ndays_ago(20), end_date=today(),
                fields="trade_date,buy_sm_amount,buy_md_amount,buy_lg_amount,"
                       "buy_elg_amount,sell_sm_amount,sell_md_amount,sell_lg_amount,"
                       "sell_elg_amount,net_mf_amount"
            ),
            retries=3, delay=1,
        )
        if df is not None and not df.empty:
            return df.sort_values("trade_date").tail(15).to_string(index=False), None
        return "暂无数据", None

    from data.fallback import em_get_capital_flow_hist
    return _try_with_fallback(
        _tushare,
        lambda: ak_get_capital_flow(ts_code),
        eastmoney_fn=lambda: em_get_capital_flow_hist(ts_code),
        label="资金流向",
    )


@compat_cache(ttl=600, show_spinner=False)
def get_dragon_tiger(ts_code: str) -> tuple[str, str | None]:
    """龙虎榜：Tushare → akshare 兜底"""
    from data.fallback import ak_get_dragon_tiger

    def _tushare():
        if _get_pro() is None:
            return "", _ts_err or "Tushare 不可用"
        df = _retry_call(
            lambda: _get_pro().top_list(start_date=ndays_ago(30), end_date=today(), ts_code=ts_code,
                                  fields="trade_date,name,close,pct_change,net_amount,reason"),
            retries=3, delay=1,
        )
        if df is not None and not df.empty:
            return df.head(10).to_string(index=False), None
        return "近30日无龙虎榜记录", None

    from data.fallback import em_get_dragon_tiger_dc
    return _try_with_fallback(
        _tushare,
        lambda: ak_get_dragon_tiger(ts_code),
        eastmoney_fn=lambda: em_get_dragon_tiger_dc(ts_code),
        label="龙虎榜",
    )


@compat_cache(ttl=600, show_spinner=False)
def get_valuation_history(ts_code: str, years: int = 5) -> tuple[pd.DataFrame, str | None]:
    """获取历史估值数据（PE_TTM, PB, PS_TTM），用于分位图"""
    def _tushare():
        if _get_pro() is None:
            return pd.DataFrame(), _ts_err
        df = _retry_call(
            lambda: _get_pro().daily_basic(
                ts_code=ts_code,
                start_date=ndays_ago(years * 365),
                end_date=today(),
                fields="trade_date,pe_ttm,pb,ps_ttm,close"
            ),
            retries=3, delay=1,
        )
        if df is not None and not df.empty:
            df = df.sort_values("trade_date").reset_index(drop=True)
            # 过滤掉异常值（负值或极端值）
            for col in ["pe_ttm", "pb", "ps_ttm"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df, None
        return pd.DataFrame(), "未获取到历史估值数据"

    def _akshare():
        try:
            import akshare as ak
            code6 = to_code6(ts_code)
            df = ak.stock_a_lg_indicator(symbol=code6)
            if df is not None and not df.empty:
                df = df.rename(columns={
                    "trade_date": "trade_date", "pe": "pe_ttm", "pb": "pb", "ps": "ps_ttm"
                })
                for col in ["pe_ttm", "pb", "ps_ttm"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.tail(years * 250).reset_index(drop=True)
                return df, None
        except Exception as e:
            logger.debug("[get_valuation_history] akshare 失败: %s", e)
        return pd.DataFrame(), "akshare 无历史估值数据"

    return _try_with_fallback(_tushare, _akshare, None, label="历史估值")


@compat_cache(ttl=300, show_spinner=False)
def get_northbound_flow(ts_code: str) -> tuple[str, str | None]:
    """获取北向资金持仓变化"""
    def _tushare():
        if _get_pro() is None:
            return "", _ts_err
        try:
            df = _retry_call(
                lambda: _get_pro().hk_hold(
                    ts_code=ts_code,
                    start_date=ndays_ago(60),
                    end_date=today(),
                    fields="trade_date,ts_code,name,vol,ratio,exchange"
                ),
                retries=3, delay=1,
            )
            if df is not None and not df.empty:
                df = df.sort_values("trade_date")
                return f"北向资金持仓（近60日）：\n{df.tail(20).to_string(index=False)}", None
            return "暂无北向资金持仓数据", None
        except Exception as e:
            return "", f"北向资金：{e}"

    def _akshare():
        try:
            import akshare as ak
            code6 = to_code6(ts_code)
            df = ak.stock_hsgt_individual_em(symbol=code6)
            if df is not None and not df.empty:
                df = df.tail(20)
                return f"北向资金持仓（近20日）：\n{df.to_string(index=False)}", None
        except Exception as e:
            logger.debug("[get_northbound_flow] akshare 失败: %s", e)
        return "北向资金数据暂不可用", None

    return _try_with_fallback(_tushare, _akshare, None, label="北向资金")


@compat_cache(ttl=300, show_spinner=False)
def get_margin_trading(ts_code: str) -> tuple[str, str | None]:
    """获取融资融券数据"""
    def _tushare():
        if _get_pro() is None:
            return "", _ts_err
        try:
            df = _retry_call(
                lambda: _get_pro().margin_detail(
                    ts_code=ts_code,
                    start_date=ndays_ago(30),
                    end_date=today(),
                    fields="trade_date,rzye,rzmre,rzche,rqye,rqmcl,rqchl"
                ),
                retries=3, delay=1,
            )
            if df is not None and not df.empty:
                df = df.sort_values("trade_date")
                return f"融资融券（近30日）：\n{df.tail(15).to_string(index=False)}", None
            return "暂无融资融券数据", None
        except Exception as e:
            return "", f"融资融券：{e}"

    def _akshare():
        try:
            import akshare as ak
            code6 = to_code6(ts_code)
            df = ak.stock_margin_detail_info(code=code6)
            if df is not None and not df.empty:
                df = df.tail(15)
                return f"融资融券（近15日）：\n{df.to_string(index=False)}", None
        except Exception as e:
            logger.debug("[get_margin_trading] akshare 失败: %s", e)
        return "融资融券数据暂不可用", None

    from data.fallback import em_get_margin_dc
    return _try_with_fallback(_tushare, _akshare, eastmoney_fn=lambda: em_get_margin_dc(ts_code), label="融资融券")


@compat_cache(ttl=600, show_spinner=False)
def get_sector_peers(ts_code: str) -> tuple[str, str | None]:
    """获取同行业个股列表及基本估值，用于板块对比"""
    df_list, _ = load_stock_list()
    if df_list.empty:
        return "", "股票列表不可用"

    # 找到目标股的行业
    m = df_list[df_list["ts_code"] == ts_code]
    if m.empty or not m.iloc[0].get("industry"):
        return "", "无法确定所属行业"

    industry = m.iloc[0]["industry"]
    peers = df_list[df_list["industry"] == industry].head(20)

    if peers.empty or len(peers) <= 1:
        return f"行业：{industry}，同业个股数据不足", None

    # 尝试获取同行估值数据
    if _get_pro() is not None:
        try:
            codes = ",".join(peers["ts_code"].tolist()[:10])
            df_val = _retry_call(
                lambda: _get_pro().daily_basic(
                    ts_code=codes,
                    trade_date=today(),
                    fields="ts_code,close,pe_ttm,pb,total_mv,turnover_rate"
                ),
                retries=2, delay=1,
            )
            if df_val is not None and not df_val.empty:
                # 合并名称
                df_val = df_val.merge(
                    peers[["ts_code", "name"]], on="ts_code", how="left"
                )
                df_val = df_val.sort_values("total_mv", ascending=False)
                return (f"行业：{industry}\n同行业个股估值对比（按市值排序）：\n"
                        f"{df_val.to_string(index=False)}"), None
        except Exception as e:
            logger.debug("[get_sector_peers] 同行估值获取失败: %s", e)

    # 兜底：只返回名称列表
    names = peers["name"].tolist()[:10]
    return f"行业：{industry}\n同行业个股：{'、'.join(names)}", None


@compat_cache(ttl=600, show_spinner=False)
def get_holders_info(ts_code: str) -> tuple[str, str | None]:
    """十大股东：Tushare → akshare 兜底"""
    from data.fallback import ak_get_holders_info

    def _tushare():
        if _get_pro() is None:
            return "", _ts_err or "Tushare 不可用"
        df = _retry_call(
            lambda: _get_pro().top10_holders(
                ts_code=ts_code,
                fields="ann_date,end_date,holder_name,hold_amount,hold_ratio"
            ),
            retries=3, delay=1,
        )
        if df is not None and not df.empty:
            latest = df[df["end_date"] == df["end_date"].max()]
            return (f"十大股东（截至 {latest.iloc[0]['end_date']}）：\n"
                    f"{latest.to_string(index=False)}"), None
        return "暂无十大股东数据", None

    return _try_with_fallback(
        _tushare,
        lambda: ak_get_holders_info(ts_code),
        label="十大股东",
    )


@compat_cache(ttl=600, show_spinner=False)
def get_pledge_info(ts_code: str) -> tuple[str, str | None]:
    """股权质押：Tushare → akshare 兜底"""
    from data.fallback import ak_get_pledge_info

    def _tushare():
        if _get_pro() is None:
            return "", _ts_err or "Tushare 不可用"
        df = _retry_call(
            lambda: _get_pro().pledge_stat(
                ts_code=ts_code,
                fields="end_date,pledge_count,unrest_pledge,rest_pledge,total_share,pledge_ratio"
            ),
            retries=3, delay=1,
        )
        if df is not None and not df.empty:
            latest = df.iloc[0]
            ratio = latest.get("pledge_ratio", "N/A")
            return (f"股权质押统计（截至 {latest.get('end_date', 'N/A')}）：\n"
                    f"质押笔数={latest.get('pledge_count', 'N/A')}  "
                    f"质押比例={ratio}%\n"
                    f"{df.head(5).to_string(index=False)}"), None
        return "暂无质押数据", None

    return _try_with_fallback(
        _tushare,
        lambda: ak_get_pledge_info(ts_code),
        label="质押数据",
    )


@compat_cache(ttl=600, show_spinner=False)
def get_fund_holdings(ts_code: str) -> tuple[str, str | None]:
    """基金持仓：Tushare → akshare 兜底"""
    from data.fallback import ak_get_fund_holdings

    def _tushare():
        if _get_pro() is None:
            return "", _ts_err or "Tushare 不可用"
        df = _retry_call(
            lambda: _get_pro().fund_portfolio(
                ts_code=ts_code,
                fields="ann_date,end_date,symbol,mkv,amount,stk_mkv_ratio"
            ),
            retries=3, delay=1,
        )
        if df is not None and not df.empty:
            return (f"基金持仓情况（近两期）：\n"
                    f"{df.head(20).to_string(index=False)}"), None
        return "暂无基金持仓数据", None

    return _try_with_fallback(
        _tushare,
        lambda: ak_get_fund_holdings(ts_code),
        label="基金持仓",
    )


def price_summary(df: pd.DataFrame) -> str:
    """生成K线数据的文本摘要，供AI分析使用"""
    if df.empty:
        return "暂无K线数据"
    d = df.copy()
    for p in [5, 20, 60]:
        d[f"MA{p}"] = d["收盘"].rolling(p).mean()
    lt = d.iloc[-1]

    def pct(n):
        if len(d) <= n: return "N/A"
        return f"{(d.iloc[-1]['收盘'] / d.iloc[-n]['收盘'] - 1) * 100:.2f}%"

    ma_arr = ("多头排列↑" if lt["MA5"] > lt["MA20"] > lt["MA60"]
              else "空头排列↓" if lt["MA5"] < lt["MA20"] < lt["MA60"]
              else "均线纠缠~")
    return "\n".join([
        f"最新收盘: {lt['收盘']:.2f}元",
        f"5日:{pct(5)}  20日:{pct(20)}  60日:{pct(60)}",
        f"MA5={lt['MA5']:.2f}  MA20={lt['MA20']:.2f}  MA60={lt['MA60']:.2f} → {ma_arr}",
        f"60日区间: 最高{d.tail(60)['最高'].max():.2f} / 最低{d.tail(60)['最低'].min():.2f}",
        "",
        "近15日 OHLCV：",
        d.tail(15)[["日期", "开盘", "最高", "最低", "收盘", "成交量", "涨跌幅"]].to_string(index=False),
    ])
