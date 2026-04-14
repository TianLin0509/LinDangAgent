"""
QMT ↔ Tushare 字段映射。
原则：只映射本期下游实际消费的字段，不做全映射。
未知字段默认丢弃 + 记 warning。
"""
from __future__ import annotations
import logging

import pandas as pd

logger = logging.getLogger(__name__)


# ── 元信息字段映射 ──────────────────────────────────────────
QMT_DETAIL_TO_TUSHARE_BASIC = {
    "InstrumentID": "ts_code_base",
    "InstrumentName": "name",
    "ExchangeID": "exchange",
    "OpenDate": "list_date",
    "ExpireDate": "delist_date",
    "PreClose": "pre_close",
    "UpStopPrice": "up_limit",
    "DownStopPrice": "down_limit",
    "FloatVolume": "float_share",
    "TotalVolume": "total_share",
    "InstrumentStatus": "status_code",
    "IsTrading": "is_trading",
}


def qmt_detail_to_tushare_dict(detail: dict) -> dict:
    """QMT instrument_detail → Tushare basic_info dict 格式。"""
    if not detail:
        return {}
    out = {}
    for qmt_key, ts_key in QMT_DETAIL_TO_TUSHARE_BASIC.items():
        if qmt_key in detail:
            out[ts_key] = detail[qmt_key]
    return out


# ── PershareIndex → Tushare fina_indicator 映射 ─────────────
QMT_PERSHARE_TO_FINA = {
    "m_timetag": "end_date",
    "m_anntime": "ann_date",
    "s_fa_eps_basic": "basic_eps",
    "s_fa_eps_diluted": "diluted_eps",
    "s_fa_bps": "bps",
    "s_fa_ocfps": "cfps",
    "s_fa_roe": "roe",
    "s_fa_roe_basic": "roe_waa",
    "s_fa_roa": "roa",
    "s_fa_grossprofitmargin": "grossprofit_margin",
    "s_fa_netprofitmargin": "netprofit_margin",
    "s_fa_debttoassets": "debt_to_assets",
    "s_fa_current": "current_ratio",
    "s_fa_quick": "quick_ratio",
    "s_fa_yoy_tr": "revenue_yoy",
    "s_fa_yoyocf": "ocf_yoy",
    "s_fa_yoynetprofit": "netprofit_yoy",
}


def qmt_pershare_to_fina_indicator(df: pd.DataFrame) -> pd.DataFrame:
    """QMT PershareIndex DataFrame → Tushare fina_indicator schema。"""
    if df is None or df.empty:
        return pd.DataFrame()
    rename_map = {qmt: ts for qmt, ts in QMT_PERSHARE_TO_FINA.items() if qmt in df.columns}
    out = df.rename(columns=rename_map)
    keep_cols = list(rename_map.values())
    if "end_date" not in out.columns and "m_timetag" in df.columns:
        out["end_date"] = df["m_timetag"]
        keep_cols.append("end_date")
    available = [c for c in keep_cols if c in out.columns]
    if len(df.columns) > len(available):
        dropped = set(df.columns) - set(rename_map.keys())
        logger.debug("[qmt_schema_map] 丢弃 %d 个未映射字段: %s", len(dropped), list(dropped)[:10])
    return out[available] if available else out


# ── 资产负债表 / 利润表 / 现金流 中文映射 ────────────────
QMT_BALANCE_TO_CN = {
    "m_timetag": "报告期",
    "m_anntime": "公告日期",
    "tot_assets": "资产总计",
    "tot_liab": "负债合计",
    "tot_shrhldr_eqy_excl_min_int": "归母股东权益",
    "cap_stk": "股本",
    "cap_rsrv": "资本公积",
    "undistributed_profit": "未分配利润",
    "tot_cur_assets": "流动资产合计",
    "total_current_liability": "流动负债合计",
    "account_receivable": "应收账款",
    "inventories": "存货",
    "fix_assets": "固定资产",
    "goodwill": "商誉",
    "cash_equivalents": "货币资金",
    "shortterm_loan": "短期借款",
    "long_term_loans": "长期借款",
    "bonds_payable": "应付债券",
}

QMT_INCOME_TO_CN = {
    "m_timetag": "报告期",
    "m_anntime": "公告日期",
    "revenue_inc": "营业总收入",
    "total_operating_cost": "营业总成本",
    "operating_profit": "营业利润",
    "total_profit": "利润总额",
    "n_income_attr_p": "归母净利润",
    "basic_eps": "基本每股收益",
}

QMT_CASHFLOW_TO_CN = {
    "m_timetag": "报告期",
    "m_anntime": "公告日期",
    "n_cashflow_act": "经营活动现金流净额",
    "n_cashflow_inv_act": "投资活动现金流净额",
    "n_cashflow_fnc_act": "筹资活动现金流净额",
}


def _df_to_table_text(df: pd.DataFrame, title: str, col_map: dict, max_rows: int = 8) -> str:
    """把 QMT DataFrame 用字段映射转成可读表格字符串。"""
    if df is None or df.empty:
        return f"\n【{title}】\n（无数据）\n"
    rename = {k: v for k, v in col_map.items() if k in df.columns}
    sub = df.rename(columns=rename)
    cols = [c for c in col_map.values() if c in sub.columns]
    if not cols:
        return f"\n【{title}】\n（字段映射为空）\n"
    sub = sub[cols].head(max_rows)
    return f"\n【{title}（近{len(sub)}期）】\n{sub.to_string(index=False)}\n"


def qmt_financials_to_tushare_text(tables: dict[str, pd.DataFrame]) -> str:
    """8 张财务表整合成一个文本报告（Tushare get_financial 同格式）。"""
    parts = []
    parts.append(_df_to_table_text(tables.get("Balance"), "资产负债表", QMT_BALANCE_TO_CN))
    parts.append(_df_to_table_text(tables.get("Income"), "利润表", QMT_INCOME_TO_CN))
    parts.append(_df_to_table_text(tables.get("CashFlow"), "现金流量表", QMT_CASHFLOW_TO_CN))
    pershare = tables.get("PershareIndex")
    if pershare is not None and not pershare.empty:
        ps_df = qmt_pershare_to_fina_indicator(pershare)
        if not ps_df.empty:
            parts.append(f"\n【核心财务指标（近{len(ps_df.head(8))}期）】\n{ps_df.head(8).to_string(index=False)}\n")
    holder_num = tables.get("HolderNum")
    if holder_num is not None and not holder_num.empty:
        parts.append(f"\n【股东数（近{len(holder_num.head(4))}期）】\n{holder_num.head(4).to_string(index=False)}\n")
    return "\n".join(parts)
