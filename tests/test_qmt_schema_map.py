import pandas as pd


def test_qmt_detail_to_tushare_dict_basic():
    from data.qmt_schema_map import qmt_detail_to_tushare_dict

    detail = {
        "InstrumentID": "000001",
        "InstrumentName": "平安银行",
        "ExchangeID": "SZ",
        "OpenDate": "19910403",
        "PreClose": 12.3,
        "UpStopPrice": 13.53,
        "FloatVolume": 1.9e10,
        "TotalVolume": 1.94e10,
    }
    out = qmt_detail_to_tushare_dict(detail)
    assert out["name"] == "平安银行"
    assert out["list_date"] == "19910403"
    assert "float_share" in out
    assert "total_share" in out


def test_qmt_detail_missing_fields_graceful():
    from data.qmt_schema_map import qmt_detail_to_tushare_dict
    out = qmt_detail_to_tushare_dict({})
    assert isinstance(out, dict)


def test_qmt_pershare_to_fina_indicator():
    from data.qmt_schema_map import qmt_pershare_to_fina_indicator

    qmt_df = pd.DataFrame([
        {"m_timetag": "20250331", "m_anntime": "20250430",
         "s_fa_eps_basic": 1.5, "s_fa_eps_diluted": 1.48,
         "s_fa_bps": 15.2, "s_fa_ocfps": 2.3},
    ])
    tushare_df = qmt_pershare_to_fina_indicator(qmt_df)
    assert "end_date" in tushare_df.columns
    assert "basic_eps" in tushare_df.columns
    assert "bps" in tushare_df.columns
    assert tushare_df.iloc[0]["basic_eps"] == 1.5


def test_qmt_financials_to_tushare_text():
    from data.qmt_schema_map import qmt_financials_to_tushare_text

    tables = {
        "Balance": pd.DataFrame([{"m_timetag": "20250331", "tot_assets": 5.77e12,
                                   "tot_liab": 5.27e12, "cap_stk": 1.94e10}]),
        "Income": pd.DataFrame([{"m_timetag": "20250331", "revenue_inc": 3.5e11,
                                  "n_income_attr_p": 1.4e11}]),
        "CashFlow": pd.DataFrame(),
        "PershareIndex": pd.DataFrame([{"m_timetag": "20250331", "s_fa_eps_basic": 1.5}]),
    }
    txt = qmt_financials_to_tushare_text(tables)
    assert isinstance(txt, str)
    assert "资产总计" in txt or "tot_assets" in txt
    assert "20250331" in txt
