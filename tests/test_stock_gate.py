import pytest


def test_tradability_ok_normal_stock(monkeypatch):
    from data.stock_gate import check_tradability, TradabilityStatus
    from tests.fixtures.qmt_mocks import patch_qmt_instrument_info

    patch_qmt_instrument_info(monkeypatch, {
        "000001": {
            "InstrumentName": "平安银行", "InstrumentStatus": 0,
            "PreClose": 12.0, "UpStopPrice": 13.2,
            "OpenDate": "19910403", "IsTrading": True,
        },
    })
    r = check_tradability("000001.SZ")
    assert r.status == TradabilityStatus.OK
    assert not r.hard_block
    assert r.warnings == []


def test_tradability_st_by_name(monkeypatch):
    from data.stock_gate import check_tradability, TradabilityStatus
    from tests.fixtures.qmt_mocks import patch_qmt_instrument_info

    patch_qmt_instrument_info(monkeypatch, {
        "600225": {
            "InstrumentName": "ST 某某", "InstrumentStatus": 0,
            "PreClose": 10.0, "UpStopPrice": 10.5,
            "OpenDate": "19970101", "IsTrading": True,
        },
    })
    r = check_tradability("600225.SH")
    assert r.status == TradabilityStatus.ST
    assert not r.hard_block
    assert any("ST" in w for w in r.warnings)


def test_tradability_st_by_stop_ratio(monkeypatch):
    """即使 name 不含 ST，UpStop/Pre<0.06 也判 ST。"""
    from data.stock_gate import check_tradability, TradabilityStatus
    from tests.fixtures.qmt_mocks import patch_qmt_instrument_info

    patch_qmt_instrument_info(monkeypatch, {
        "600225": {
            "InstrumentName": "隐藏ST", "InstrumentStatus": 0,
            "PreClose": 10.0, "UpStopPrice": 10.4,  # 4% 涨停板
            "OpenDate": "19970101", "IsTrading": True,
        },
    })
    r = check_tradability("600225.SH")
    assert r.status == TradabilityStatus.ST


def test_tradability_newly_listed(monkeypatch):
    from data.stock_gate import check_tradability, TradabilityStatus
    from tests.fixtures.qmt_mocks import patch_qmt_instrument_info
    import datetime

    recent = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y%m%d")
    patch_qmt_instrument_info(monkeypatch, {
        "301999": {
            "InstrumentName": "新股", "InstrumentStatus": 0,
            "PreClose": 25.0, "UpStopPrice": 27.5,
            "OpenDate": recent, "IsTrading": True,
        },
    })
    r = check_tradability("301999.SZ")
    assert r.status == TradabilityStatus.NEWLY_LISTED
    assert any("上市" in w for w in r.warnings)


def test_tradability_bse_no_data(monkeypatch):
    """BJ 股 + QMT 返 None → BSE_NO_DATA，不 hard_block。"""
    from data.stock_gate import check_tradability, TradabilityStatus
    import data.qmt_client as qc

    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_instrument_info", lambda sym: None)

    r = check_tradability("430300.BJ")
    assert r.status == TradabilityStatus.BSE_NO_DATA
    assert not r.hard_block


def test_tradability_delisted_non_bj_hard_block(monkeypatch):
    """非 BJ 股 + QMT 返 None → DELISTED hard_block（Task 1 discovery 结论）。"""
    from data.stock_gate import check_tradability, TradabilityStatus
    import data.qmt_client as qc

    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_instrument_info", lambda sym: None)
    # 不让 Tushare 兜底干扰（让它也返空）
    from data import tushare_client
    monkeypatch.setattr(tushare_client, "get_basic_info",
                        lambda ts_code: ({}, "also down"))

    r = check_tradability("600087.SH")
    assert r.status == TradabilityStatus.DELISTED
    assert r.hard_block is True


def test_tradability_qmt_down_fallback_tushare(monkeypatch):
    """QMT 挂了走 Tushare 兜底（正常股）。"""
    from data.stock_gate import check_tradability, TradabilityStatus
    from tests.fixtures.qmt_mocks import patch_qmt_unavailable
    from data import tushare_client

    patch_qmt_unavailable(monkeypatch)
    monkeypatch.setattr(tushare_client, "get_basic_info",
                        lambda ts_code: ({"name": "平安银行", "list_date": "19910403"}, None))

    r = check_tradability("000001.SZ")
    assert r.status == TradabilityStatus.OK


def test_tradability_both_down_unknown(monkeypatch):
    from data.stock_gate import check_tradability, TradabilityStatus
    from tests.fixtures.qmt_mocks import patch_qmt_unavailable
    from data import tushare_client

    patch_qmt_unavailable(monkeypatch)
    monkeypatch.setattr(tushare_client, "get_basic_info",
                        lambda ts_code: ({}, "tushare also down"))

    r = check_tradability("999999.SZ")
    assert r.status == TradabilityStatus.UNKNOWN
    assert not r.hard_block
