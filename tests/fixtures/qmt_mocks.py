"""压测场景用的 QMT monkey-patch 工具。"""
from __future__ import annotations
import pandas as pd


def patch_qmt_unavailable(monkeypatch):
    """模拟 QMT 整体不可用（客户端挂）。"""
    import data.qmt_client as qc
    monkeypatch.setattr(qc, "is_alive", lambda: False)

    def _raise(*args, **kw):
        from data.qmt_client import QMTUnavailable
        raise QMTUnavailable("mocked unavailable")

    monkeypatch.setattr(qc, "get_instrument_info", _raise)
    monkeypatch.setattr(qc, "get_instrument_info_batch", _raise)
    monkeypatch.setattr(qc, "get_financial", _raise)
    monkeypatch.setattr(qc, "get_kline", _raise)
    monkeypatch.setattr(qc, "get_trading_dates_before", _raise)


def patch_qmt_instrument_info(monkeypatch, responses: dict):
    """
    responses: {ts_code_or_prefix: detail_dict or None}
    按前缀匹配（例如 "000001" 匹配 "000001.SZ"）。
    """
    import data.qmt_client as qc

    def fake(sym):
        clean = sym.split(".")[0]
        for k, v in responses.items():
            k_clean = k.split(".")[0]
            if k_clean == clean:
                return v
        return None

    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_instrument_info", fake)


def patch_qmt_financial_empty_core(monkeypatch):
    """模拟 QMT 财务核心表空（Balance empty），非核心非空。"""
    import data.qmt_client as qc

    def fake_financial(sym, years=3):
        return {
            "Balance": pd.DataFrame(),
            "Income": pd.DataFrame([{"m_timetag": "20250331", "revenue_inc": 1e11}]),
            "CashFlow": pd.DataFrame(),
            "Capital": pd.DataFrame(),
            "Top10FlowHolder": pd.DataFrame([{"name": "张三"}]),
            "Top10Holder": pd.DataFrame(),
            "HolderNum": pd.DataFrame(),
            "PershareIndex": pd.DataFrame([{"m_timetag": "20250331", "s_fa_eps_basic": 1.5}]),
        }

    monkeypatch.setattr(qc, "is_alive", lambda: True)
    monkeypatch.setattr(qc, "get_financial", fake_financial)
