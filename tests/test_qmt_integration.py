import pandas as pd
import pytest


def test_try_with_fallback_qmt_first():
    """QMT 成功时不应调用 tushare/其他源"""
    from data import tushare_client

    called = []

    def qmt_fn():
        called.append("qmt")
        df = pd.DataFrame({"收盘": [10.0]})
        return df, None

    def tushare_fn():
        called.append("tushare")
        return pd.DataFrame(), "should not be called"

    df, err = tushare_client._try_with_fallback(
        tushare_fn, label="K线", qmt_fn=qmt_fn
    )
    assert err is None
    assert called == ["qmt"]
    assert not df.empty


def test_try_with_fallback_qmt_fail_fallback(monkeypatch):
    """QMT 抛异常时静默降级到 tushare"""
    from data import tushare_client
    from data.qmt_client import QMTUnavailable

    def qmt_fn():
        raise QMTUnavailable("not logged in")

    def tushare_fn():
        return pd.DataFrame({"收盘": [10.0]}), None

    monkeypatch.setattr(tushare_client, "_get_pro", lambda: object())

    df, err = tushare_client._try_with_fallback(
        tushare_fn, label="K线", qmt_fn=qmt_fn
    )
    assert err is None
    assert not df.empty


def test_get_price_df_uses_qmt(monkeypatch):
    """QMT 可用时，get_price_df 应返回 QMT 数据，列名是中文"""
    import pandas as pd
    from data import tushare_client
    import data.qmt_client as qmt_client

    # 伪造 qmt_client.is_alive + get_kline
    def fake_get_kline(symbol, period="1d", start=None, end=None, count=120, adjust="front"):
        idx = pd.to_datetime(["2026-04-10", "2026-04-11"])
        return pd.DataFrame({
            "open": [10.0, 10.5], "high": [10.8, 10.9],
            "low": [9.9, 10.3], "close": [10.5, 10.7],
            "volume": [1000, 1200], "amount": [10500, 12800],
        }, index=idx)

    monkeypatch.setattr(qmt_client, "get_kline", fake_get_kline)
    monkeypatch.setattr(qmt_client, "is_alive", lambda: True)

    # IMPORTANT: get_price_df has @compat_cache(ttl=300); bust the cache
    # by using a unique ts_code that wasn't cached previously
    df, err = tushare_client.get_price_df("TEST001.SZ", days=2)
    assert err is None, f"unexpected err: {err}"
    for col in ["日期", "开盘", "最高", "最低", "收盘", "成交量"]:
        assert col in df.columns, f"缺少列 {col}: 实际={list(df.columns)}"
    assert len(df) == 2


def test_data_source_map_per_label():
    """每个 label 独立记录 data_source。"""
    from data import tushare_client
    tushare_client._data_source_map = {}

    def qmt_a():
        return ({"name": "A"}, None)

    def ts_b():
        return ("B data", None)

    # QMT 成功 → A 标签记 qmt
    tushare_client._try_with_fallback(lambda: (None, "fail"), label="A", qmt_fn=qmt_a)
    assert tushare_client._data_source_map["A"] == "qmt"

    # Tushare 成功（无 QMT） → B 标签记 tushare
    import unittest.mock as um
    with um.patch.object(tushare_client, "_get_pro", return_value=object()):
        tushare_client._try_with_fallback(ts_b, label="B")
    assert tushare_client._data_source_map["B"] == "tushare"
    # A 标签保留原值不被覆盖
    assert tushare_client._data_source_map["A"] == "qmt"


def test_get_data_source_map_returns_copy():
    """暴露 getter，返回 dict 拷贝。"""
    from data import tushare_client
    tushare_client._data_source_map = {"K线": "qmt", "基本信息": "tushare"}
    m = tushare_client.get_data_source_map()
    assert m == {"K线": "qmt", "基本信息": "tushare"}
    m["foo"] = "bar"
    # 返回的是拷贝，修改不影响原 dict
    assert "foo" not in tushare_client._data_source_map


def test_get_basic_info_uses_qmt(monkeypatch):
    """get_basic_info 优先走 QMT。"""
    from data import tushare_client
    import data.qmt_client as qc

    fake_detail = {
        "InstrumentName": "平安银行", "ExchangeID": "SZ",
        "OpenDate": "19910403", "PreClose": 12.0, "UpStopPrice": 13.2,
        "FloatVolume": 1.9e10, "TotalVolume": 1.94e10,
    }
    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_instrument_info", lambda sym: fake_detail)

    tushare_client._data_source_map = {}
    info, err = tushare_client.get_basic_info("000001.SZ")
    assert err is None
    assert info.get("name") == "平安银行"
    assert tushare_client._data_source_map.get("基本信息") == "qmt"


def test_get_financial_uses_qmt(monkeypatch):
    """QMT 8 表核心非空 → 走 QMT，结果含中文字段。"""
    import pandas as pd
    from data import tushare_client
    import data.qmt_client as qc

    fake_tables = {
        "Balance": pd.DataFrame([{"m_timetag": "20250331", "tot_assets": 5.77e12,
                                   "tot_liab": 5.27e12, "cap_stk": 1.94e10}]),
        "Income": pd.DataFrame([{"m_timetag": "20250331", "revenue_inc": 3.5e11,
                                  "n_income_attr_p": 1.4e11}]),
        "CashFlow": pd.DataFrame(),
        "Capital": pd.DataFrame(),
        "Top10FlowHolder": pd.DataFrame(),
        "Top10Holder": pd.DataFrame(),
        "HolderNum": pd.DataFrame(),
        "PershareIndex": pd.DataFrame([{"m_timetag": "20250331", "s_fa_eps_basic": 1.5, "s_fa_bps": 15.2}]),
    }
    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_financial", lambda sym, years=3: fake_tables)

    tushare_client._data_source_map = {}
    # Bust cache by using a unique code per test run
    txt, err = tushare_client.get_financial("QMTTEST1.SZ")
    assert err is None
    assert "资产总计" in txt
    assert tushare_client._data_source_map.get("财务") == "qmt"


def test_get_financial_core_empty_falls_back(monkeypatch):
    """核心财务表空 → QMTUnavailable → 降级 Tushare（或其他非 QMT 源）。"""
    from data import tushare_client
    from tests.fixtures.qmt_mocks import patch_qmt_financial_empty_core

    patch_qmt_financial_empty_core(monkeypatch)

    import unittest.mock as um
    with um.patch.object(tushare_client, "_get_pro", return_value=None):
        tushare_client._data_source_map = {}
        txt, err = tushare_client.get_financial("QMTTEST2.SZ")
        # 降级后不是 qmt
        assert tushare_client._data_source_map.get("财务") != "qmt"


def test_get_income_uses_qmt(monkeypatch):
    import pandas as pd
    from data import report_data
    import data.qmt_client as qc

    fake_tables = {
        "Balance": pd.DataFrame(), "CashFlow": pd.DataFrame(),
        "Capital": pd.DataFrame(), "Top10FlowHolder": pd.DataFrame(),
        "Top10Holder": pd.DataFrame(), "HolderNum": pd.DataFrame(),
        "PershareIndex": pd.DataFrame(),
        "Income": pd.DataFrame([
            {"m_timetag": "20250331", "revenue_inc": 3.5e11, "n_income_attr_p": 1.4e11,
             "basic_eps": 0.6},
            {"m_timetag": "20241231", "revenue_inc": 1.6e12, "n_income_attr_p": 4.5e11,
             "basic_eps": 2.3},
        ]),
    }
    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_financial", lambda sym, years=3: fake_tables)

    df = report_data.get_income("QMTTEST_INCOME.SZ")
    assert not df.empty
    # 必须有中文列或等价字段
    has_cn = "报告期" in df.columns or "营业总收入" in df.columns
    has_en = "end_date" in df.columns
    assert has_cn or has_en


def test_get_balancesheet_uses_qmt(monkeypatch):
    import pandas as pd
    from data import report_data
    import data.qmt_client as qc

    fake_tables = {k: pd.DataFrame() for k in ("Income", "CashFlow", "Capital",
                                                 "Top10FlowHolder", "Top10Holder",
                                                 "HolderNum", "PershareIndex")}
    fake_tables["Balance"] = pd.DataFrame([
        {"m_timetag": "20250331", "tot_assets": 5.77e12, "tot_liab": 5.27e12, "cap_stk": 1.94e10},
    ])
    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_financial", lambda sym, years=3: fake_tables)

    df = report_data.get_balancesheet("QMTTEST_BS.SZ")
    assert not df.empty


def test_get_fina_indicator_uses_qmt(monkeypatch):
    import pandas as pd
    from data import report_data
    import data.qmt_client as qc

    fake_tables = {k: pd.DataFrame() for k in ("Balance", "Income", "CashFlow",
                                                 "Capital", "Top10FlowHolder",
                                                 "Top10Holder", "HolderNum")}
    fake_tables["PershareIndex"] = pd.DataFrame([
        {"m_timetag": "20250331", "s_fa_eps_basic": 1.5, "s_fa_bps": 15.2,
         "s_fa_roe": 12.5},
    ])
    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_financial", lambda sym, years=3: fake_tables)

    df = report_data.get_fina_indicator("QMTTEST_FINA.SZ")
    assert not df.empty
    # 映射后应该包含 Tushare fina_indicator 的字段名
    assert "basic_eps" in df.columns or "bps" in df.columns or "roe" in df.columns
