"""
单股交易状态前置过滤。优先 QMT，Tushare 兜底，两源都挂时放行不拦截。

Task 1 discovery 结论：
- 退市股 QMT.get_instrument_detail 返 None（不是特定 Status 码）
- BJ 股也返 None（权限问题，非退市）
- ST 判定不能依赖 InstrumentStatus，用 name 前缀 + UpStop/Pre 比
"""
from __future__ import annotations
import datetime as _dt
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class TradabilityStatus(Enum):
    OK = "ok"
    ST = "st"
    NEWLY_LISTED = "newly_listed"
    BSE_NO_DATA = "bse_no_data"
    DELISTED = "delisted"
    UNKNOWN = "unknown"


@dataclass
class TradabilityResult:
    status: TradabilityStatus
    hard_block: bool
    warnings: list[str] = field(default_factory=list)
    facts: dict = field(default_factory=dict)


class TradabilityBlocked(Exception):
    def __init__(self, result: TradabilityResult):
        self.result = result
        super().__init__(f"TradabilityBlocked: {result.status.value}")


NEWLY_LISTED_CALENDAR_DAYS = 30


def _days_since(date_str: str) -> Optional[int]:
    if not date_str:
        return None
    try:
        d = _dt.datetime.strptime(str(date_str), "%Y%m%d").date()
        return (_dt.date.today() - d).days
    except Exception:
        return None


def _classify_from_qmt_detail(ts_code: str, detail: dict) -> TradabilityResult:
    facts = {
        "InstrumentStatus": detail.get("InstrumentStatus"),
        "InstrumentName": detail.get("InstrumentName"),
        "OpenDate": detail.get("OpenDate"),
        "IsTrading": detail.get("IsTrading"),
        "PreClose": detail.get("PreClose"),
        "UpStopPrice": detail.get("UpStopPrice"),
    }
    name = detail.get("InstrumentName", "") or ""
    pre = detail.get("PreClose", 0) or 0
    up = detail.get("UpStopPrice", 0) or 0

    # ST 判定（name 前缀 OR 5% 涨跌停板）
    is_st = False
    if name.startswith(("ST", "*ST", "S*ST")):
        is_st = True
    elif pre > 0 and up > 0 and (up - pre) / pre < 0.06:
        # ETF (5xxxxx/1xxxxx) 不按此判
        code6 = str(detail.get("InstrumentID", ts_code.split(".")[0]))
        exch = detail.get("ExchangeID", "")
        is_etf = code6.startswith(("5", "1")) and exch in ("SH", "SZ")
        if not is_etf:
            is_st = True

    # 新股判定
    is_new = False
    days = _days_since(detail.get("OpenDate"))
    if days is not None and days < NEWLY_LISTED_CALENDAR_DAYS:
        is_new = True

    warnings = []
    status = TradabilityStatus.OK
    if is_st:
        warnings.append("ST 标记（5% 涨跌停板或名称含 ST）")
        status = TradabilityStatus.ST
    elif is_new:
        warnings.append(f"上市 {days} 天（新股，数据窗口可能较短）")
        status = TradabilityStatus.NEWLY_LISTED

    return TradabilityResult(status=status, hard_block=False,
                             warnings=warnings, facts=facts)


def _classify_from_tushare_basic(ts_code: str, info: dict) -> TradabilityResult:
    """Tushare 兜底判定：名字含 ST + 上市日期。"""
    name = info.get("name", "") or ""
    list_date = info.get("list_date", "")
    facts = {"name": name, "list_date": list_date}

    is_st = name.startswith(("ST", "*ST", "S*ST"))
    days = _days_since(list_date)
    is_new = days is not None and days < NEWLY_LISTED_CALENDAR_DAYS

    warnings = []
    status = TradabilityStatus.OK
    if is_st:
        warnings.append("ST 标记（来自 Tushare）")
        status = TradabilityStatus.ST
    elif is_new:
        warnings.append(f"上市 {days} 天（新股）")
        status = TradabilityStatus.NEWLY_LISTED

    return TradabilityResult(status=status, hard_block=False,
                             warnings=warnings, facts=facts)


def check_tradability(ts_code: str) -> TradabilityResult:
    """
    返回 TradabilityResult。hard_block=True 时调用方应抛 TradabilityBlocked。
    两源都挂 → UNKNOWN，放行不拦截。
    """
    from data import qmt_client
    from data import tushare_client

    qmt_ok = False
    try:
        if qmt_client.is_alive():
            detail = qmt_client.get_instrument_info(ts_code)
            qmt_ok = True  # QMT 响应了，不论 detail 是否 None
            if detail:
                return _classify_from_qmt_detail(ts_code, detail)
            # detail=None
            if ts_code.endswith(".BJ"):
                return TradabilityResult(
                    status=TradabilityStatus.BSE_NO_DATA, hard_block=False,
                    warnings=["QMT 无此北交所股元信息，基础信息走 Tushare"],
                    facts={"ts_code": ts_code},
                )
            # 非 BJ 但 QMT 没有 → 疑似退市
            logger.warning("[stock_gate] QMT 返 None（非 BJ）: %s，疑似已退市/代码无效", ts_code)
            # 退市判定不应立即返回 — 尝试 Tushare 兜底，若 Tushare 也无数据再判 DELISTED
            try:
                info, err = tushare_client.get_basic_info(ts_code)
                if err is None and info:
                    # Tushare 有数据说明股票存在，但 QMT 无 → 仍按 DELISTED
                    return TradabilityResult(
                        status=TradabilityStatus.DELISTED, hard_block=True,
                        warnings=[f"QMT 未找到 {ts_code}，疑似已退市"],
                        facts={"ts_code": ts_code, "source": "qmt_none", "tushare_info": info},
                    )
            except Exception as e2:
                logger.debug(f"Tushare 兜底异常（退市判定）: {e2}")
            # Tushare 也没数据 → 还是 DELISTED
            return TradabilityResult(
                status=TradabilityStatus.DELISTED, hard_block=True,
                warnings=[f"QMT 未找到 {ts_code}，疑似已退市/代码无效"],
                facts={"ts_code": ts_code, "source": "qmt_none"},
            )
    except Exception as e:
        logger.warning("[stock_gate] QMT 判定异常: %s，降级 Tushare", e)

    # QMT 整体挂 → Tushare 兜底
    if not qmt_ok:
        try:
            info, err = tushare_client.get_basic_info(ts_code)
            if err is None and info:
                return _classify_from_tushare_basic(ts_code, info)
        except Exception as e:
            logger.warning("[stock_gate] Tushare 兜底异常: %s", e)

    # 两源都挂 → UNKNOWN，放行
    return TradabilityResult(
        status=TradabilityStatus.UNKNOWN, hard_block=False,
        warnings=["数据源异常，未能确认交易状态"],
        facts={"ts_code": ts_code},
    )
