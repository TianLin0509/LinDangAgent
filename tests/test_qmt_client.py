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
