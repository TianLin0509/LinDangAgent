"""
单股 QMT 重构集成压测 —— 纯数据层，不调 AI。
8 场景覆盖正常/ST/新股/北交所/除权/QMT挂/财务核心空/退市。
"""
import datetime as _dt
import sys
from pathlib import Path

import pandas as pd
import pytest

# Ensure project root on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Fixture: reset QMT module state contaminated by other tests ───
@pytest.fixture(autouse=True)
def _reset_qmt_module_state():
    """
    test_qmt_client_ext.py 直接赋值 qc._connected = True（非 monkeypatch），
    导致跨测试污染：_connected=True 但 _xtdata=None（monkeypatch 已还原）。
    每个测试前保存、测试后恢复真实 QMT 连接状态。
    """
    import data.qmt_client as qc
    saved_connected = qc._connected
    saved_xtdata = qc._xtdata
    # 重置为 None，让 _ensure_connected() 重新执行完整初始化
    qc._connected = None
    qc._xtdata = None
    yield
    # 恢复（主要为了其他测试，不影响我们自己）
    qc._connected = saved_connected
    qc._xtdata = saved_xtdata


# ── Helper ────────────────────────────────────────────────
def _qmt_alive():
    from data import qmt_client
    return qmt_client.is_alive()


# ── 场景 1: 正常股 ─────────────────────────────────────────
def test_scenario_1_normal_stock():
    """000001.SZ 真实跑，gate=OK，核心维度来源=qmt"""
    if not _qmt_alive():
        pytest.skip("QMT 未登录")

    from data.stock_gate import check_tradability, TradabilityStatus
    import data.tushare_client as tc

    result = check_tradability("000001.SZ")
    assert result.status == TradabilityStatus.OK
    assert not result.hard_block

    # basic_info 走 QMT — 重置 map 再调用，确保是本次调用写入
    tc._data_source_map = {}
    info, err = tc.get_basic_info("000001.SZ")
    assert err is None
    assert tc._data_source_map.get("基本信息") == "qmt"


# ── 场景 2: ST 股（动态发现） ──────────────────────────────
def test_scenario_2_st_stock():
    """从沪深A股池前 500 里找 5% 涨停板 ST 股。"""
    if not _qmt_alive():
        pytest.skip("QMT 未登录")

    from data.stock_gate import check_tradability, TradabilityStatus
    from data import qmt_client

    try:
        # get_sector_stocks 返回不带后缀的代码列表
        raw_pool = qmt_client.get_sector_stocks("沪深A股")[:500]
        # get_instrument_info_batch 需要可以带或不带后缀（内部做 normalize）
        details = qmt_client.get_instrument_info_batch(raw_pool)
    except Exception as e:
        pytest.skip(f"无法获取股票池: {e}")

    st_sym = None
    for sym, d in details.items():
        if not d:
            continue
        pre = d.get("PreClose", 0) or 0
        up = d.get("UpStopPrice", 0) or 0
        name = d.get("InstrumentName", "") or ""
        if pre > 0 and up > 0 and (up - pre) / pre < 0.06:
            if name.startswith(("ST", "*ST")):
                st_sym = sym
                break

    if not st_sym:
        pytest.skip("未在沪深A股前 500 里找到 ST 股")

    result = check_tradability(st_sym)
    assert result.status == TradabilityStatus.ST
    assert any("ST" in w for w in result.warnings)


# ── 场景 3: 新股（动态发现） ───────────────────────────────
def test_scenario_3_newly_listed():
    """从 QMT 池找上市 <30 自然日的股票。"""
    if not _qmt_alive():
        pytest.skip("QMT 未登录")

    from data.stock_gate import check_tradability, TradabilityStatus
    from data import qmt_client

    try:
        raw_pool = qmt_client.get_sector_stocks("沪深A股")[:800]
        details = qmt_client.get_instrument_info_batch(raw_pool)
    except Exception as e:
        pytest.skip(f"无法获取股票池: {e}")

    today = _dt.date.today()
    new_sym = None
    for sym, d in details.items():
        if not d:
            continue
        open_date = d.get("OpenDate", "")
        if open_date:
            try:
                od = _dt.datetime.strptime(str(open_date), "%Y%m%d").date()
                days = (today - od).days
                if 0 < days < 30:
                    new_sym = sym
                    break
            except Exception:
                pass

    if not new_sym:
        pytest.skip("未找到 30 日内新股")

    result = check_tradability(new_sym)
    assert result.status == TradabilityStatus.NEWLY_LISTED


# ── 场景 4: 北交所股 ───────────────────────────────────────
def test_scenario_4_bse_stock():
    """BJ 股 QMT 无数据，走 BSE_NO_DATA 路径（或 UNKNOWN 降级）。"""
    if not _qmt_alive():
        pytest.skip("QMT 未登录")

    from data.stock_gate import check_tradability, TradabilityStatus

    result = check_tradability("430300.BJ")
    assert result.status in (TradabilityStatus.BSE_NO_DATA, TradabilityStatus.UNKNOWN)
    assert not result.hard_block


# ── 场景 5: 除权股 ────────────────────────────────────────
def test_scenario_5_divided_stock():
    """002594.SZ 比亚迪前复权 vs 不复权对比（长窗口）。"""
    if not _qmt_alive():
        pytest.skip("QMT 未登录")

    from data import qmt_client

    none_df = qmt_client.get_kline("002594.SZ", count=500, adjust="none")
    front_df = qmt_client.get_kline("002594.SZ", count=500, adjust="front")

    assert not none_df.empty and not front_df.empty
    # 500 天前两者首日应不等（有除权事件）
    first_none = float(none_df.iloc[0]["close"])
    first_front = float(front_df.iloc[0]["close"])
    assert abs(first_none - first_front) > 0.01, \
        f"长窗口内 front vs none 首日应不同: {first_none} vs {first_front}"

    # 今日收盘应相同
    last_none = float(none_df.iloc[-1]["close"])
    last_front = float(front_df.iloc[-1]["close"])
    assert abs(last_none - last_front) < 0.01


# ── 场景 6: QMT 整体挂（mock） ─────────────────────────────
def test_scenario_6_qmt_unavailable(monkeypatch):
    """monkey-patch QMT 挂掉，验证全量降级到非 QMT 源。"""
    import data.tushare_client as tc
    from tests.fixtures.qmt_mocks import patch_qmt_unavailable

    patch_qmt_unavailable(monkeypatch)
    tc._data_source_map = {}

    info, err = tc.get_basic_info("000001.SZ")
    src = tc._data_source_map.get("基本信息")
    assert src != "qmt", f"QMT 挂了还走 QMT: src={src}"
    assert src in ("tushare", "eastmoney", "akshare", "baostock", "sina", "unavailable")


# ── 场景 7: QMT 财务核心表空（mock） ───────────────────────
def test_scenario_7_qmt_financial_core_empty(monkeypatch):
    """QMT 核心财务表空 → 财务降级；基本信息仍走 QMT。"""
    import data.qmt_client as qc
    import data.tushare_client as tc
    from tests.fixtures.qmt_mocks import patch_qmt_financial_empty_core

    # QMT 可用 + instrument_info 返正常元信息
    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_instrument_info",
                        lambda sym: {"InstrumentName": "A", "OpenDate": "19950101",
                                     "InstrumentStatus": 0, "PreClose": 10.0,
                                     "UpStopPrice": 11.0, "FloatVolume": 1e9,
                                     "TotalVolume": 1e9, "ExchangeID": "SZ",
                                     "InstrumentID": "000001"})
    patch_qmt_financial_empty_core(monkeypatch)

    tc._data_source_map = {}
    info, err = tc.get_basic_info("000001.SZ")
    assert tc._data_source_map.get("基本信息") == "qmt"

    # 财务降级（核心表空 → 不是 qmt）
    fin_text, fin_err = tc.get_financial("SCN7TEST.SZ")
    assert tc._data_source_map.get("财务") != "qmt"


# ── 场景 8: 退市硬拦截（mock） ─────────────────────────────
def test_scenario_8_delisted_hard_block(monkeypatch):
    """非 BJ 股 + QMT get_instrument_info 返 None → DELISTED hard_block."""
    import data.qmt_client as qc
    import data.tushare_client as tc
    from data.stock_gate import check_tradability, TradabilityStatus, TradabilityBlocked

    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_instrument_info", lambda sym: None)
    # 让 Tushare 兜底也返空（加速 DELISTED 判定路径）
    monkeypatch.setattr(tc, "get_basic_info",
                        lambda ts_code: ({}, "delisted"))

    result = check_tradability("600087.SH")  # 长航凤凰（已退市）
    assert result.status == TradabilityStatus.DELISTED
    assert result.hard_block is True

    # 验证 TradabilityBlocked 异常可以正常抛出捕获
    with pytest.raises(TradabilityBlocked):
        raise TradabilityBlocked(result)
