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
