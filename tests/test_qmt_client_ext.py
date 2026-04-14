import pandas as pd
import pytest


def test_get_instrument_info_returns_dict_or_none(monkeypatch):
    import data.qmt_client as qc

    fake_detail = {"InstrumentName": "平安银行", "ExchangeID": "SZ",
                   "InstrumentStatus": 0, "OpenDate": "19910403",
                   "PreClose": 12.0, "UpStopPrice": 13.2,
                   "IsTrading": True, "TotalVolume": 1000000}

    class FakeXt:
        def get_instrument_detail(self, sym, iscomplete):
            assert iscomplete is True
            return fake_detail if sym == "000001.SZ" else None

    qc._connected = True
    monkeypatch.setattr(qc, "_xtdata", FakeXt())

    assert qc.get_instrument_info("000001") == fake_detail
    assert qc.get_instrument_info("999999.SZ") is None


def test_get_instrument_info_batch(monkeypatch):
    import data.qmt_client as qc

    class FakeXt:
        def get_instrument_detail_list(self, syms, iscomplete):
            assert iscomplete is True
            return {s: {"InstrumentName": f"股票{s}"} for s in syms}

    qc._connected = True
    monkeypatch.setattr(qc, "_xtdata", FakeXt())

    result = qc.get_instrument_info_batch(["000001", "600036"])
    assert "000001.SZ" in result
    assert "600036.SH" in result


def test_get_trading_dates_before(monkeypatch):
    import data.qmt_client as qc

    class FakeXt:
        def get_trading_dates(self, market, start_time="", end_time="", count=-1):
            import datetime
            base = datetime.date(2026, 4, 1)
            dates = []
            for i in range(14):
                d = base + datetime.timedelta(days=i)
                if d.weekday() < 5:
                    dt = datetime.datetime(d.year, d.month, d.day)
                    dates.append(int(dt.timestamp() * 1000))
            return dates

    qc._connected = True
    monkeypatch.setattr(qc, "_xtdata", FakeXt())

    dates = qc.get_trading_dates_before("2026-04-14", count=5)
    assert len(dates) == 5
    assert all(isinstance(d, str) for d in dates)
    assert dates == sorted(dates)


def test_get_financial_returns_all_tables(monkeypatch):
    import data.qmt_client as qc

    called_download = []

    class FakeXt:
        def download_financial_data2(self, syms, table_list, start_time, end_time, callback):
            called_download.append((tuple(syms), tuple(table_list)))
            callback({"total": 1, "finished": 1})

        def get_financial_data(self, syms, table_list, start_time="", end_time="", report_type="report_time"):
            return {syms[0]: {t: pd.DataFrame({"col": [1.0]}) for t in table_list}}

    qc._connected = True
    monkeypatch.setattr(qc, "_xtdata", FakeXt())

    tables = qc.get_financial("000001.SZ", years=3)
    assert "Balance" in tables
    assert "Income" in tables
    assert "PershareIndex" in tables
    assert called_download
