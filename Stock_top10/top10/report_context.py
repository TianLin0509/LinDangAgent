"""Build rich report context for Top10 candidate reports."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from data.tushare_client import (
    _retry_call,
    get_basic_info,
    get_capital_flow,
    get_dragon_tiger,
    get_fund_holdings,
    get_holders_info,
    get_margin_trading,
    get_northbound_flow,
    get_pledge_info,
    get_price_df,
    get_pro,
    get_sector_peers,
    ndays_ago,
    price_summary,
    today,
)

logger = logging.getLogger(__name__)
_sem = threading.Semaphore(5)


def _ts_call(fn):
    with _sem:
        return _retry_call(fn, retries=3, delay=1)


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame()


def _code6(ts_code: str) -> str:
    return ts_code.split(".")[0] if "." in ts_code else ts_code


# akshare 兜底映射：endpoint → akshare 函数
def _ak_fallback(endpoint: str, ts_code: str) -> pd.DataFrame:
    """akshare 兜底获取财务数据"""
    try:
        import akshare as ak
        code6 = _code6(ts_code)
        if endpoint == "income":
            return ak.stock_financial_report_sina(stock=code6, symbol="利润表").head(8)
        elif endpoint == "balancesheet":
            return ak.stock_financial_report_sina(stock=code6, symbol="资产负债表").head(4)
        elif endpoint == "cashflow":
            return ak.stock_financial_report_sina(stock=code6, symbol="现金流量表").head(4)
        elif endpoint == "fina_indicator":
            return ak.stock_financial_analysis_indicator(symbol=code6).head(8)
        elif endpoint == "stk_holdertrade":
            return ak.stock_hold_management_detail_em(symbol=code6).head(15)
        elif endpoint == "stk_holdernumber":
            return ak.stock_hold_num_cninfo(symbol=code6).head(8)
        elif endpoint == "dividend":
            return ak.stock_history_dividend_detail(symbol=code6, indicator="分红").head(8)
    except Exception as e:
        logger.debug("[_ak_fallback] %s %s: %s", endpoint, ts_code, e)
    return _empty_df()


def _get_df(endpoint: str, ts_code: str, fields: str, max_rows: int, **kwargs) -> pd.DataFrame:
    pro = get_pro()
    if pro is not None:
        try:
            fn = getattr(pro, endpoint)
            df = _ts_call(lambda: fn(ts_code=ts_code, fields=fields, **kwargs))
            if df is not None and not df.empty:
                return df.head(max_rows)
        except Exception as exc:
            logger.debug("[%s] tushare %s: %s", endpoint, ts_code, exc)

    # akshare 兜底
    df = _ak_fallback(endpoint, ts_code)
    if not df.empty:
        return df.head(max_rows)
    return _empty_df()


def get_income(ts_code: str) -> pd.DataFrame:
    return _get_df(
        "income",
        ts_code,
        "end_date,ann_date,revenue,operate_profit,total_profit,n_income,n_income_attr_p,"
        "basic_eps,diluted_eps,total_cogs,sell_exp,admin_exp,rd_exp,fin_exp",
        8,
    )


def get_balancesheet(ts_code: str) -> pd.DataFrame:
    return _get_df(
        "balancesheet",
        ts_code,
        "end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int,accounts_receiv,"
        "inventories,goodwill,money_cap,total_cur_assets,total_cur_liab,lt_borr,"
        "bond_payable,notes_receiv,prepayment,oth_receiv",
        4,
    )


def get_cashflow(ts_code: str) -> pd.DataFrame:
    return _get_df(
        "cashflow",
        ts_code,
        "end_date,n_cashflow_act,n_cashflow_inv_act,n_cashflow_fnc_act,c_fr_sale_sg,"
        "c_paid_goods_s,free_cashflow",
        4,
    )


def get_fina_indicator(ts_code: str) -> pd.DataFrame:
    return _get_df(
        "fina_indicator",
        ts_code,
        "end_date,roe,roe_waa,roa,grossprofit_margin,netprofit_margin,debt_to_assets,"
        "current_ratio,quick_ratio,revenue_yoy,netprofit_yoy,basic_eps,bps,cfps,"
        "ebit_of_gr,netprofit_of_gr,ar_turn,inv_turn,assets_turn,op_yoy,ocf_yoy,equity_yoy",
        8,
    )


def get_fina_mainbz(ts_code: str) -> pd.DataFrame:
    pro = get_pro()
    if pro is not None:
        try:
            df = _ts_call(
                lambda: pro.fina_mainbz(
                    ts_code=ts_code,
                    type="P",
                    fields="end_date,bz_item,bz_sales,bz_profit,bz_cost",
                )
            )
            if df is not None and not df.empty:
                return df.head(20)
        except Exception as exc:
            logger.debug("[fina_mainbz] tushare %s: %s", ts_code, exc)
    # akshare 兜底
    try:
        import akshare as ak
        df = ak.stock_zygc_ym(symbol=_code6(ts_code))
        if df is not None and not df.empty:
            return df.head(20)
    except Exception as exc:
        logger.debug("[fina_mainbz] akshare %s: %s", ts_code, exc)
    return _empty_df()


def get_share_float(ts_code: str) -> pd.DataFrame:
    return _get_df(
        "share_float",
        ts_code,
        "ann_date,float_date,float_share,float_ratio,holder_name,share_type",
        10,
    )


def get_repurchase(ts_code: str) -> pd.DataFrame:
    return _get_df(
        "repurchase",
        ts_code,
        "ann_date,end_date,proc,exp_date,vol,amount,high_limit,low_limit",
        5,
    )


def get_stk_holdertrade(ts_code: str) -> pd.DataFrame:
    return _get_df(
        "stk_holdertrade",
        ts_code,
        "ann_date,holder_name,holder_type,in_de,change_vol,change_ratio,after_share,after_ratio",
        15,
    )


def get_stk_holdernumber(ts_code: str) -> pd.DataFrame:
    return _get_df(
        "stk_holdernumber",
        ts_code,
        "end_date,holder_num,holder_nums",
        8,
    )


def get_block_trade(ts_code: str) -> pd.DataFrame:
    pro = get_pro()
    if pro is not None:
        try:
            df = _ts_call(
                lambda: pro.block_trade(
                    ts_code=ts_code,
                    start_date=ndays_ago(90),
                    end_date=today(),
                    fields="trade_date,price,vol,amount,buyer,seller",
                )
            )
            if df is not None and not df.empty:
                return df.head(10)
        except Exception as exc:
            logger.debug("[block_trade] tushare %s: %s", ts_code, exc)
    # akshare 兜底
    try:
        import akshare as ak
        df = ak.stock_dzjy_mdetail(symbol=_code6(ts_code))
        if df is not None and not df.empty:
            return df.head(10)
    except Exception as exc:
        logger.debug("[block_trade] akshare %s: %s", ts_code, exc)
    return _empty_df()


def get_dividend(ts_code: str) -> pd.DataFrame:
    return _get_df(
        "dividend",
        ts_code,
        "end_date,ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,cash_div,cash_div_tax,"
        "ex_date,pay_date",
        8,
    )


def get_fina_audit(ts_code: str) -> pd.DataFrame:
    return _get_df(
        "fina_audit",
        ts_code,
        "end_date,ann_date,audit_result,audit_agency,audit_sign",
        5,
    )


def get_forecast(ts_code: str) -> pd.DataFrame:
    return _get_df(
        "forecast",
        ts_code,
        "ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max,summary",
        4,
    )


def get_express(ts_code: str) -> pd.DataFrame:
    return _get_df(
        "express",
        ts_code,
        "end_date,ann_date,revenue,operate_profit,total_profit,n_income,total_assets,"
        "total_hldr_eqy_exc_min_int,yoy_net_profit,yoy_sales,yoy_op,yoy_tp,yoy_roe",
        4,
    )


def get_disclosure_date(ts_code: str) -> pd.DataFrame:
    return _get_df(
        "disclosure_date",
        ts_code,
        "end_date,pre_date,actual_date,modify_date",
        4,
    )


def calc_dupont(fina_df: pd.DataFrame, bs_df: pd.DataFrame) -> str:
    if fina_df.empty or bs_df.empty:
        return "数据不足，无法计算杜邦分析"
    rows = []
    try:
        for _, row in fina_df.head(3).iterrows():
            end = row.get("end_date", "")
            npm = row.get("netprofit_margin")
            assets_turn = row.get("assets_turn")
            bs_match = bs_df[bs_df["end_date"] == end]
            equity_multiple = None
            if not bs_match.empty:
                ta = bs_match.iloc[0].get("total_assets")
                eq = bs_match.iloc[0].get("total_hldr_eqy_exc_min_int")
                if ta and eq and float(eq) > 0:
                    equity_multiple = float(ta) / float(eq)

            parts = [f"{end}:"]
            if npm is not None:
                parts.append(f"净利率={float(npm):.2f}%")
            if assets_turn is not None:
                parts.append(f"资产周转率={float(assets_turn):.4f}")
            if equity_multiple is not None:
                parts.append(f"权益乘数={equity_multiple:.2f}")
            rows.append(" ".join(parts))
    except Exception as exc:
        logger.debug("[dupont] %s", exc)
    return "杜邦分析（近3期）：\n" + "\n".join(rows) if rows else "杜邦分析数据不足"


def calc_fcf(cf_df: pd.DataFrame) -> str:
    if cf_df.empty:
        return "现金流数据不足"
    rows = []
    try:
        for _, row in cf_df.head(3).iterrows():
            end = row.get("end_date", "")
            ocf = row.get("n_cashflow_act")
            fcf = row.get("free_cashflow")
            if fcf is not None:
                rows.append(f"{end}: FCF={float(fcf) / 1e8:.2f}亿 经营现金流={float(ocf or 0) / 1e8:.2f}亿")
            elif ocf is not None:
                rows.append(f"{end}: 经营现金流={float(ocf) / 1e8:.2f}亿")
    except Exception as exc:
        logger.debug("[fcf] %s", exc)
    return "自由现金流：\n" + "\n".join(rows) if rows else "自由现金流数据不足"


def calc_ccc(fina_df: pd.DataFrame) -> str:
    if fina_df.empty:
        return "周转数据不足"
    rows = []
    try:
        for _, row in fina_df.head(3).iterrows():
            end = row.get("end_date", "")
            ar_turn = row.get("ar_turn")
            inv_turn = row.get("inv_turn")
            dso = 365 / float(ar_turn) if ar_turn and float(ar_turn) > 0 else None
            dio = 365 / float(inv_turn) if inv_turn and float(inv_turn) > 0 else None
            parts = [f"{end}:"]
            if dso is not None:
                parts.append(f"DSO={dso:.0f}天")
            if dio is not None:
                parts.append(f"DIO={dio:.0f}天")
            if dso is not None and dio is not None:
                parts.append(f"CCC≈{(dso + dio):.0f}天(未含DPO)")
            rows.append(" ".join(parts))
    except Exception as exc:
        logger.debug("[ccc] %s", exc)
    return "现金转换周期：\n" + "\n".join(rows) if rows else "CCC 数据不足"


def calc_risk_checklist(
    fina_df: pd.DataFrame,
    bs_df: pd.DataFrame,
    cf_df: pd.DataFrame,
    audit_df: pd.DataFrame,
    pledge_text: str,
) -> list[str]:
    risks: list[str] = []
    try:
        if not fina_df.empty and "netprofit_of_gr" in fina_df.columns:
            annual = fina_df[fina_df["end_date"].astype(str).str.endswith("1231")].head(2)
            if len(annual) >= 2:
                vals = annual["netprofit_of_gr"].dropna().astype(float).values
                if len(vals) >= 2 and all(v < 0 for v in vals[:2]):
                    risks.append("扣非净利润连续2年为负")

        if not fina_df.empty and "debt_to_assets" in fina_df.columns:
            debt = fina_df.iloc[0].get("debt_to_assets")
            if debt is not None and float(debt) > 70:
                risks.append(f"资产负债率={float(debt):.1f}%（>70%）")

        if not cf_df.empty and "n_cashflow_act" in cf_df.columns:
            ocf_vals = cf_df["n_cashflow_act"].dropna().astype(float).head(2).values
            if len(ocf_vals) >= 2 and all(v < 0 for v in ocf_vals):
                risks.append("经营现金流连续2期为负")

        if not bs_df.empty:
            goodwill = bs_df.iloc[0].get("goodwill")
            equity = bs_df.iloc[0].get("total_hldr_eqy_exc_min_int")
            if goodwill is not None and equity is not None and float(equity) > 0:
                ratio = float(goodwill) / float(equity) * 100
                if ratio > 30:
                    risks.append(f"商誉/净资产={ratio:.1f}%（>30%）")

        if not fina_df.empty and "roe" in fina_df.columns:
            roe = fina_df.iloc[0].get("roe")
            if roe is not None and float(roe) < 5:
                risks.append(f"ROE={float(roe):.2f}%（<5%）")

        if not audit_df.empty:
            audit_result = str(audit_df.iloc[0].get("audit_result", ""))
            if audit_result and "标准" not in audit_result:
                risks.append(f"审计意见：{audit_result}")

        if pledge_text and "质押比例" in pledge_text:
            import re

            match = re.search(r"质押比例[=＝](\d+\.?\d*)", pledge_text)
            if match and float(match.group(1)) > 40:
                risks.append(f"股权质押比例={match.group(1)}%（>40%）")

        if not fina_df.empty and "revenue_yoy" in fina_df.columns:
            revenue_yoys = fina_df["revenue_yoy"].dropna().astype(float).head(2).values
            if len(revenue_yoys) >= 2 and all(v < 0 for v in revenue_yoys):
                risks.append("营收连续2期负增长")
    except Exception as exc:
        logger.debug("[risk_checklist] %s", exc)
    return risks


def _df_to_text(df: pd.DataFrame, label: str, max_rows: int = 10) -> str:
    if df is None or df.empty:
        return f"{label}：暂无数据"
    return f"{label}：\n{df.head(max_rows).to_string(index=False)}"


def _tuple_to_text(result, label: str) -> str:
    if isinstance(result, tuple):
        text, _ = result
        return text if text and text != "暂无数据" else f"{label}：暂无数据"
    return str(result) if result else f"{label}：暂无数据"


def build_report_context(ts_code: str, progress_cb=None) -> tuple[dict, dict]:
    raw: dict = {}
    ctx: dict = {}

    def _progress(message: str):
        if progress_cb:
            progress_cb(message)

    _progress("获取基础数据与财务三表...")
    batch1_tasks = {
        "info": lambda: get_basic_info(ts_code),
        "price": lambda: get_price_df(ts_code),
        "income": lambda: get_income(ts_code),
        "balance": lambda: get_balancesheet(ts_code),
        "cashflow": lambda: get_cashflow(ts_code),
    }
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fn): key for key, fn in batch1_tasks.items()}
        for future in as_completed(futures):
            raw[futures[future]] = future.result()

    _progress("获取财务指标与股东数据...")
    batch2_tasks = {
        "fina_ind": lambda: get_fina_indicator(ts_code),
        "mainbz": lambda: get_fina_mainbz(ts_code),
        "capital": lambda: get_capital_flow(ts_code),
        "holders": lambda: get_holders_info(ts_code),
        "pledge": lambda: get_pledge_info(ts_code),
    }
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fn): key for key, fn in batch2_tasks.items()}
        for future in as_completed(futures):
            raw[futures[future]] = future.result()

    _progress("获取增减持、解禁、分红等数据...")
    batch3_tasks = {
        "holdertrade": lambda: get_stk_holdertrade(ts_code),
        "holdernumber": lambda: get_stk_holdernumber(ts_code),
        "share_float": lambda: get_share_float(ts_code),
        "repurchase": lambda: get_repurchase(ts_code),
        "block_trade": lambda: get_block_trade(ts_code),
        "dividend": lambda: get_dividend(ts_code),
        "audit": lambda: get_fina_audit(ts_code),
        "forecast": lambda: get_forecast(ts_code),
        "express": lambda: get_express(ts_code),
        "disclosure": lambda: get_disclosure_date(ts_code),
        "northbound": lambda: get_northbound_flow(ts_code),
        "margin": lambda: get_margin_trading(ts_code),
        "dragon": lambda: get_dragon_tiger(ts_code),
        "sector": lambda: get_sector_peers(ts_code),
        "fund": lambda: get_fund_holdings(ts_code),
    }
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fn): key for key, fn in batch3_tasks.items()}
        for future in as_completed(futures):
            raw[futures[future]] = future.result()

    _progress("计算衍生指标...")
    info = raw["info"][0] if isinstance(raw["info"], tuple) else raw.get("info", {})
    price_df = raw["price"][0] if isinstance(raw["price"], tuple) else raw.get("price", pd.DataFrame())
    income_df = raw.get("income", pd.DataFrame())
    bs_df = raw.get("balance", pd.DataFrame())
    cf_df = raw.get("cashflow", pd.DataFrame())
    fina_df = raw.get("fina_ind", pd.DataFrame())
    mainbz_df = raw.get("mainbz", pd.DataFrame())
    audit_df = raw.get("audit", pd.DataFrame())

    ctx["basic_info"] = str(info) if info else "暂无基本信息"
    ctx["price_summary"] = price_summary(price_df) if not price_df.empty else "暂无K线数据"
    ctx["income"] = _df_to_text(income_df, "利润表")
    ctx["balance"] = _df_to_text(bs_df, "资产负债表")
    ctx["cashflow"] = _df_to_text(cf_df, "现金流量表")
    ctx["fina_indicator"] = _df_to_text(fina_df, "核心财务指标", max_rows=8)
    ctx["mainbz"] = _df_to_text(mainbz_df, "主营业务构成")
    ctx["capital"] = _tuple_to_text(raw.get("capital"), "资金流向")
    ctx["dragon"] = _tuple_to_text(raw.get("dragon"), "龙虎榜")
    ctx["northbound"] = _tuple_to_text(raw.get("northbound"), "北向资金")
    ctx["margin"] = _tuple_to_text(raw.get("margin"), "融资融券")
    ctx["holders"] = _tuple_to_text(raw.get("holders"), "十大股东")
    ctx["pledge"] = _tuple_to_text(raw.get("pledge"), "股权质押")
    ctx["fund"] = _tuple_to_text(raw.get("fund"), "基金持仓")
    ctx["sector"] = _tuple_to_text(raw.get("sector"), "板块对比")
    ctx["holdertrade"] = _df_to_text(raw.get("holdertrade", pd.DataFrame()), "股东增减持")
    ctx["holdernumber"] = _df_to_text(raw.get("holdernumber", pd.DataFrame()), "股东人数")
    ctx["share_float"] = _df_to_text(raw.get("share_float", pd.DataFrame()), "限售解禁")
    ctx["repurchase"] = _df_to_text(raw.get("repurchase", pd.DataFrame()), "股票回购")
    ctx["block_trade"] = _df_to_text(raw.get("block_trade", pd.DataFrame()), "大宗交易")
    ctx["dividend"] = _df_to_text(raw.get("dividend", pd.DataFrame()), "分红送股")
    ctx["audit"] = _df_to_text(audit_df, "审计意见")
    ctx["forecast"] = _df_to_text(raw.get("forecast", pd.DataFrame()), "业绩预告")
    ctx["express"] = _df_to_text(raw.get("express", pd.DataFrame()), "业绩快报")
    ctx["disclosure"] = _df_to_text(raw.get("disclosure", pd.DataFrame()), "财报披露日期")

    pledge_text = ctx.get("pledge", "")
    ctx["dupont"] = calc_dupont(fina_df, bs_df)
    ctx["fcf"] = calc_fcf(cf_df)
    ctx["ccc"] = calc_ccc(fina_df)
    risk_items = calc_risk_checklist(fina_df, bs_df, cf_df, audit_df, pledge_text)
    ctx["risk_checklist"] = (
        "风险快速排查：\n" + "\n".join(f"  - {item}" for item in risk_items)
        if risk_items
        else "风险快速排查：未触发任何风险项"
    )

    raw["_info"] = info
    raw["_price_df"] = price_df
    raw["_fina_df"] = fina_df
    raw["_bs_df"] = bs_df
    raw["_cf_df"] = cf_df

    _progress("数据采集完成！")
    return ctx, raw
