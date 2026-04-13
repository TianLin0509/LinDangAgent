import pytest

def test_normalize_symbol_sz():
    from data.qmt_client import _normalize_symbol
    assert _normalize_symbol("000001") == "000001.SZ"
    assert _normalize_symbol("000001.SZ") == "000001.SZ"

def test_normalize_symbol_sh():
    from data.qmt_client import _normalize_symbol
    assert _normalize_symbol("600000") == "600000.SH"
    assert _normalize_symbol("600000.SH") == "600000.SH"

def test_normalize_symbol_chinext():
    from data.qmt_client import _normalize_symbol
    assert _normalize_symbol("300750") == "300750.SZ"
    assert _normalize_symbol("688981") == "688981.SH"

def test_normalize_symbol_bse():
    from data.qmt_client import _normalize_symbol
    assert _normalize_symbol("832000") == "832000.BJ"

def test_denormalize_symbol():
    from data.qmt_client import _denormalize_symbol
    assert _denormalize_symbol("000001.SZ") == "000001"
    assert _denormalize_symbol("600000.SH") == "600000"

def test_normalize_symbol_bse_4prefix():
    from data.qmt_client import _normalize_symbol
    # 北交所 STB 转让 4xxxxx 段
    assert _normalize_symbol("430570") == "430570.BJ"

def test_normalize_symbol_bse_83prefix():
    from data.qmt_client import _normalize_symbol
    # 北交所 83xxxx 段
    assert _normalize_symbol("833454") == "833454.BJ"

def test_normalize_symbol_sh_605():
    from data.qmt_client import _normalize_symbol
    # 沪主板较新段 605xxx
    assert _normalize_symbol("605288") == "605288.SH"

def test_normalize_symbol_sz_001():
    from data.qmt_client import _normalize_symbol
    # 深主板较新段 001xxx
    assert _normalize_symbol("001979") == "001979.SZ"

def test_normalize_symbol_unknown_prefix_warns(caplog):
    import logging
    from data.qmt_client import _normalize_symbol
    with caplog.at_level(logging.WARNING, logger="data.qmt_client"):
        result = _normalize_symbol("5ABCDE")
    assert result == "5ABCDE.SZ"
    assert any("unknown symbol prefix" in r.message for r in caplog.records)
