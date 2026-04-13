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
