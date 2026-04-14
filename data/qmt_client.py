"""
QMT / xtquant 薄封装。
- 只覆盖本期接入需要的 API：健康检查 / 历史 K 线 / 实时行情 / 板块成分
- 未登录 / 超时 / schema 异常一律抛 QMTUnavailable，由上层降级
- 代码归一化：对外不带市场后缀（与现有数据层一致），内部自动补 .SZ/.SH/.BJ
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Optional, Set

import pandas as pd

logger = logging.getLogger(__name__)


class QMTUnavailable(Exception):
    """QMT 客户端未登录 / 连接超时 / schema 不符，调用方应降级"""


# ── 代码归一化 ────────────────────────────────────────────────
def _normalize_symbol(code: str) -> str:
    """
    "000001" → "000001.SZ"
    "600000" → "600000.SH"
    "300750" → "300750.SZ"（创业板）
    "688981" → "688981.SH"（科创板）
    "832000" → "832000.BJ"（北交所）
    已带后缀原样返回。
    """
    if "." in code:
        return code
    prefix = code[:3] if len(code) >= 3 else code
    if prefix.startswith(("600", "601", "603", "605", "688", "689", "900")):
        return f"{code}.SH"
    if prefix.startswith(("000", "001", "002", "003", "300", "301", "200")):
        return f"{code}.SZ"
    if prefix[:1] in ("4", "8"):  # 北交所 43/83/87/88 起；9xx 已在 SH 分支处理
        return f"{code}.BJ"
    # 未知前缀：默认按深交所处理，但记 warning 方便定位异常输入
    logger.warning("[qmt] unknown symbol prefix for %s, defaulting to .SZ", code)
    return f"{code}.SZ"


def _denormalize_symbol(code: str) -> str:
    """去掉 .SH/.SZ/.BJ 后缀"""
    return code.split(".", 1)[0]


# ── 模块级状态（lazy init） ────────────────────────────────────
_init_lock = threading.Lock()
_connected: Optional[bool] = None
_xtdata = None
_downloaded: Set[tuple] = set()


def _ensure_connected() -> None:
    """首次调用才 import + connect；失败后标记不可用"""
    global _connected, _xtdata
    if _connected is True:
        return
    if _connected is False:
        raise QMTUnavailable("QMT 之前已标记不可用")
    with _init_lock:
        if _connected is True:
            return
        try:
            from xtquant import xtdata  # noqa
            try:
                ver = xtdata.get_client_version() if hasattr(xtdata, "get_client_version") else "unknown"
                logger.info("[qmt] connected, version=%s", ver)
            except Exception as e:
                raise QMTUnavailable(f"xtdata 无法访问客户端: {e}")
            _xtdata = xtdata
            _connected = True
        except ImportError as e:
            _connected = False
            raise QMTUnavailable(f"xtquant 未安装: {e}")
        except QMTUnavailable:
            _connected = False
            raise
        except Exception as e:
            _connected = False
            raise QMTUnavailable(f"xtdata 连接失败: {e}")


def _ensure_downloaded(sym: str, period: str, start: str, end: str) -> None:
    """
    xtquant 要求先 download_history_data 才能 get_market_data_ex；
    按 (symbol, period) 粒度缓存，确保只下载一次。
    下载失败不抛异常，交给后续查询暴露真实问题。
    """
    key = (sym, period)
    if key in _downloaded:
        return
    try:
        t0 = time.time()
        _xtdata.download_history_data(sym, period=period, start_time=start or "", end_time=end or "")
        logger.info("[qmt] download_history_data %s period=%s cost=%dms", sym, period, int((time.time() - t0) * 1000))
        _downloaded.add(key)
    except Exception as e:
        # 下载失败不阻塞查询——可能数据已有或 API 瞬时抖动
        logger.warning("[qmt] download_history_data %s failed (continuing): %s", sym, e)
        # 仍加入缓存，避免每次重试下载；真实查询失败会报错
        _downloaded.add(key)


def is_alive() -> bool:
    """健康检查，失败返回 False 不抛异常。"""
    try:
        _ensure_connected()
        return True
    except QMTUnavailable:
        return False
    except Exception as e:
        logger.warning("[qmt] is_alive unexpected error: %s", e)
        return False


# ── K 线 ──────────────────────────────────────────────────────
_REQUIRED_COLS = ("open", "high", "low", "close", "volume")


def get_kline(
    symbol: str,
    period: str = "1d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    count: int = 120,
    adjust: str = "front",
) -> pd.DataFrame:
    """
    返回标准 OHLCV DataFrame：index=datetime, columns=[open, high, low, close, volume, amount]
    QMT 不可用或 schema 异常抛 QMTUnavailable。
    """
    _ensure_connected()
    sym = _normalize_symbol(symbol)
    dividend_type = {"front": "front", "back": "back", "none": "none"}.get(adjust, "front")
    start_time = start or ""
    end_time = end or ""
    n = count if (not start and not end) else -1
    _ensure_downloaded(sym, period, start_time, end_time)

    t0 = time.time()
    try:
        data = _xtdata.get_market_data_ex(
            field_list=["time", "open", "high", "low", "close", "volume", "amount"],
            stock_list=[sym],
            period=period,
            start_time=start_time,
            end_time=end_time,
            count=n,
            dividend_type=dividend_type,
            fill_data=True,
        )
    except Exception as e:
        raise QMTUnavailable(f"get_market_data_ex 调用失败: {e}")

    if not data or sym not in data:
        raise QMTUnavailable(f"QMT 未返回 {sym} 数据")
    df = data[sym]
    if df is None or df.empty:
        raise QMTUnavailable(f"QMT 返回 {sym} 空数据")

    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise QMTUnavailable(f"QMT 返回列缺失: {missing}, 实际={list(df.columns)}")

    if "time" in df.columns:
        df = df.copy()
        df.index = pd.to_datetime(df["time"], unit="ms", errors="coerce")
        df = df.drop(columns=["time"])

    logger.info("[qmt] get_kline %s period=%s rows=%d cost=%dms",
                sym, period, len(df), int((time.time() - t0) * 1000))
    return df


# ── 实时行情 ──────────────────────────────────────────────────
def get_realtime(symbols: list[str]) -> dict[str, dict]:
    """返回: {"000001": {"price": 12.3, "bid1": ..., "ask1": ..., "ts": ...}, ...}"""
    _ensure_connected()
    syms = [_normalize_symbol(s) for s in symbols]
    # 实时快照需要最新历史数据铺底
    for s in syms:
        _ensure_downloaded(s, "1d", "", "")
    try:
        tick = _xtdata.get_full_tick(syms)
    except Exception as e:
        raise QMTUnavailable(f"get_full_tick 失败: {e}")
    if not tick:
        raise QMTUnavailable("QMT 未返回实时行情")

    result = {}
    for sym_with_suffix, row in tick.items():
        plain = _denormalize_symbol(sym_with_suffix)
        result[plain] = {
            "price": row.get("lastPrice"),
            "bid1": row.get("bidPrice", [None])[0] if row.get("bidPrice") else None,
            "ask1": row.get("askPrice", [None])[0] if row.get("askPrice") else None,
            "volume": row.get("volume"),
            "ts": row.get("time"),
        }
    return result


# ── 板块成分 ──────────────────────────────────────────────────
def get_sector_stocks(sector: str) -> list[str]:
    """板块成分股；返回不带市场后缀的代码列表"""
    _ensure_connected()
    try:
        stocks = _xtdata.get_stock_list_in_sector(sector)
    except Exception as e:
        raise QMTUnavailable(f"get_stock_list_in_sector 失败: {e}")
    if not stocks:
        raise QMTUnavailable(f"QMT 未返回板块 {sector} 成分")
    return [_denormalize_symbol(s) for s in stocks]


# ══════════════════════════════════════════════════════════════
# 扩展 API（单股重构新增）
# ══════════════════════════════════════════════════════════════

def get_instrument_info(symbol: str) -> Optional[dict]:
    """
    单股完整元信息（iscomplete=True 返回 83 字段）。
    未查到返回 None（不 raise）——区别于 _ensure_connected 失败的 QMTUnavailable。
    """
    _ensure_connected()
    sym = _normalize_symbol(symbol)
    try:
        detail = _xtdata.get_instrument_detail(sym, iscomplete=True)
    except Exception as e:
        raise QMTUnavailable(f"get_instrument_detail 失败: {e}")
    return detail if detail else None


def get_instrument_info_batch(symbols: list[str]) -> dict[str, dict]:
    """批量元信息，约 1ms/只。key 保留带后缀（方便上游处理）。"""
    _ensure_connected()
    syms = [_normalize_symbol(s) for s in symbols]
    try:
        result = _xtdata.get_instrument_detail_list(syms, iscomplete=True)
    except Exception as e:
        raise QMTUnavailable(f"get_instrument_detail_list 失败: {e}")
    return result or {}


_FINANCIAL_TABLES = [
    "Balance", "Income", "CashFlow", "Capital",
    "Top10FlowHolder", "Top10Holder", "HolderNum", "PershareIndex",
]


def get_financial(symbol: str, years: int = 3) -> dict[str, pd.DataFrame]:
    """
    下载 + 查询 8 张财务表。窗口 [今天-years年, 今天]。
    返回 {table_name: DataFrame}。失败 raise QMTUnavailable。
    """
    import datetime as _dt
    _ensure_connected()
    sym = _normalize_symbol(symbol)
    end = _dt.date.today().strftime("%Y%m%d")
    start = (_dt.date.today() - _dt.timedelta(days=years * 366)).strftime("%Y%m%d")

    t0 = time.time()
    try:
        _xtdata.download_financial_data2(
            [sym], table_list=_FINANCIAL_TABLES,
            start_time=start, end_time=end,
            callback=lambda d: None,
        )
    except Exception as e:
        raise QMTUnavailable(f"download_financial_data2 失败: {e}")

    try:
        raw = _xtdata.get_financial_data(
            [sym], table_list=_FINANCIAL_TABLES,
            start_time=start, end_time=end,
            report_type="report_time",
        )
    except Exception as e:
        raise QMTUnavailable(f"get_financial_data 失败: {e}")

    if not raw or sym not in raw:
        raise QMTUnavailable(f"get_financial_data 返回空: {sym}")
    per_sym = raw[sym]
    if not isinstance(per_sym, dict):
        raise QMTUnavailable(f"per-symbol 返回非 dict: {type(per_sym).__name__}")

    out = {}
    for t in _FINANCIAL_TABLES:
        df = per_sym.get(t)
        out[t] = df if isinstance(df, pd.DataFrame) else pd.DataFrame()

    logger.info("[qmt] get_financial %s cost=%dms tables=%s",
                sym, int((time.time() - t0) * 1000),
                {k: len(v) for k, v in out.items()})
    return out


def get_trading_dates_before(end_date: str, count: int, market: str = "SH") -> list[str]:
    """
    返回 end_date（含）之前 count 个真实交易日，'YYYY-MM-DD' 升序。
    end_date 格式: 'YYYY-MM-DD' 或 'YYYYMMDD'。
    """
    import datetime as _dt
    _ensure_connected()

    end_clean = end_date.replace("-", "")
    end_dt = _dt.datetime.strptime(end_clean, "%Y%m%d").date()
    start_dt = end_dt - _dt.timedelta(days=max(count * 2, 30))
    start_str = start_dt.strftime("%Y%m%d")

    try:
        timestamps = _xtdata.get_trading_dates(
            market, start_time=start_str, end_time=end_clean, count=-1,
        )
    except Exception as e:
        raise QMTUnavailable(f"get_trading_dates 失败: {e}")

    if not timestamps:
        raise QMTUnavailable("get_trading_dates 返回空")

    dates = [
        _dt.datetime.fromtimestamp(t / 1000).strftime("%Y-%m-%d")
        for t in timestamps
    ]
    dates.sort()
    return dates[-count:]
