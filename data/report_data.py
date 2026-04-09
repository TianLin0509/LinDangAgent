"""深度报告数据层 — Tushare API + akshare 兜底 + 衍生指标 + build_report_context()

采集全量基本面/财务/资金/股东数据，供综合投研报告使用。
使用 ThreadPoolExecutor 并行获取，Semaphore(5) 限制并发。
所有 API 都有 akshare 兜底，确保 Tushare 宕机时数据来源有保障。
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import numpy as np

from data.tushare_client import (
    _retry_call, get_pro, today, ndays_ago, to_code6,
    get_basic_info, get_price_df, get_financial,
    get_capital_flow, get_dragon_tiger, get_northbound_flow,
    get_margin_trading, get_sector_peers, get_holders_info,
    get_pledge_info, get_fund_holdings, price_summary,
)
from data.indicators import compute_indicators, format_indicators_section

logger = logging.getLogger(__name__)

_sem = threading.Semaphore(5)


# ══════════════════════════════════════════════════════════════════════════════
# 财务数据 API（Tushare 优先，akshare 兜底）
# ══════════════════════════════════════════════════════════════════════════════

def _ts_call(fn):
    """带信号量和重试的 Tushare 调用"""
    with _sem:
        return _retry_call(fn, retries=3, delay=1)


def _ts_or_ak(ts_fn, ak_fn, label: str, bs_fn=None) -> pd.DataFrame:
    """Tushare 优先 → akshare → baostock，统一返回 DataFrame"""
    pro = get_pro()
    if pro is not None:
        try:
            df = ts_fn(pro)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.debug("[%s] tushare 失败: %s", label, e)

    # akshare 兜底
    try:
        df = ak_fn()
        if df is not None and not df.empty:
            logger.info("[%s] akshare 兜底成功", label)
            return df
    except Exception as e:
        logger.debug("[%s] akshare 兜底失败: %s", label, e)

    # baostock 兜底（v3.0新增）
    if bs_fn is not None:
        try:
            df = bs_fn()
            if df is not None and not df.empty:
                logger.info("[%s] baostock 兜底成功", label)
                return df
        except Exception as e:
            logger.debug("[%s] baostock 兜底失败: %s", label, e)

    return pd.DataFrame()


def _ak_financial_report(ts_code: str, report_type: str) -> pd.DataFrame:
    """akshare 通用财务报表获取"""
    try:
        import akshare as ak
        code6 = to_code6(ts_code)
        if report_type == "income":
            df = ak.stock_financial_report_sina(stock=code6, symbol="利润表")
        elif report_type == "balance":
            df = ak.stock_financial_report_sina(stock=code6, symbol="资产负债表")
        elif report_type == "cashflow":
            df = ak.stock_financial_report_sina(stock=code6, symbol="现金流量表")
        else:
            return pd.DataFrame()
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.debug("[_ak_financial_report] %s %s: %s", ts_code, report_type, e)
        return pd.DataFrame()


def get_income(ts_code: str) -> pd.DataFrame:
    """利润表（近8期）"""
    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.income(
            ts_code=ts_code,
            fields="end_date,ann_date,revenue,operate_profit,total_profit,"
                   "n_income,n_income_attr_p,basic_eps,diluted_eps,"
                   "total_cogs,sell_exp,admin_exp,rd_exp,fin_exp"
        )).head(8),
        lambda: _ak_financial_report(ts_code, "income").head(8),
        "get_income",
    )


def get_balancesheet(ts_code: str) -> pd.DataFrame:
    """资产负债表（近4期）"""
    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.balancesheet(
            ts_code=ts_code,
            fields="end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int,"
                   "accounts_receiv,inventories,goodwill,money_cap,"
                   "total_cur_assets,total_cur_liab,lt_borr,bond_payable,"
                   "notes_receiv,prepayment,oth_receiv,acct_payable"
        )).head(4),
        lambda: _ak_financial_report(ts_code, "balance").head(4),
        "get_balancesheet",
    )


def get_cashflow(ts_code: str) -> pd.DataFrame:
    """现金流量表（近4期）"""
    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.cashflow(
            ts_code=ts_code,
            fields="end_date,n_cashflow_act,n_cashflow_inv_act,n_cashflow_fnc_act,"
                   "c_fr_sale_sg,c_paid_goods_s,free_cashflow"
        )).head(4),
        lambda: _ak_financial_report(ts_code, "cashflow").head(4),
        "get_cashflow",
    )


def get_fina_indicator(ts_code: str) -> pd.DataFrame:
    """财务指标（近8期）"""
    def _ak():
        try:
            import akshare as ak
            code6 = to_code6(ts_code)
            # stock_financial_abstract_ths 比 stock_financial_analysis_indicator 更稳定
            df = ak.stock_financial_abstract_ths(symbol=code6)
            if df is not None and not df.empty:
                return df.head(8)
            df = ak.stock_financial_analysis_indicator(symbol=code6)
            return df.head(8) if df is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.fina_indicator(
            ts_code=ts_code,
            fields="end_date,roe,roe_waa,roa,grossprofit_margin,netprofit_margin,"
                   "debt_to_assets,current_ratio,quick_ratio,"
                   "revenue_yoy,netprofit_yoy,basic_eps,"
                   "bps,cfps,ebit_of_gr,netprofit_of_gr,"
                   "ar_turn,inv_turn,assets_turn,"
                   "op_yoy,ocf_yoy,equity_yoy"
        )).head(8),
        _ak,
        "get_fina_indicator",
    )


def get_fina_mainbz(ts_code: str) -> pd.DataFrame:
    """主营业务构成"""
    def _ak():
        try:
            import akshare as ak
            code6 = to_code6(ts_code)
            df = ak.stock_zygc_ym(symbol=code6)
            return df.head(20) if df is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.fina_mainbz(
            ts_code=ts_code, type="P",
            fields="end_date,bz_item,bz_sales,bz_profit,bz_cost"
        )).head(20),
        _ak,
        "get_fina_mainbz",
    )


def get_share_float(ts_code: str) -> pd.DataFrame:
    """限售股解禁计划"""
    def _ak():
        try:
            import akshare as ak
            code6 = to_code6(ts_code)
            df = ak.stock_restricted_release_queue_sina(symbol=code6)
            return df.head(10) if df is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.share_float(
            ts_code=ts_code,
            fields="ann_date,float_date,float_share,float_ratio,holder_name,share_type"
        )).head(10),
        _ak,
        "get_share_float",
    )


def get_repurchase(ts_code: str) -> pd.DataFrame:
    """股票回购"""
    def _ak():
        try:
            import akshare as ak
            code6 = to_code6(ts_code)
            df = ak.stock_repurchase_em(symbol=code6)
            return df.head(5) if df is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.repurchase(
            ts_code=ts_code,
            fields="ann_date,end_date,proc,exp_date,vol,amount,high_limit,low_limit"
        )).head(5),
        _ak,
        "get_repurchase",
    )


def get_stk_holdertrade(ts_code: str) -> pd.DataFrame:
    """股东增减持"""
    def _ak():
        try:
            import akshare as ak
            code6 = to_code6(ts_code)
            df = ak.stock_hold_management_detail_em(symbol=code6)
            return df.head(15) if df is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.stk_holdertrade(
            ts_code=ts_code,
            fields="ann_date,holder_name,holder_type,in_de,change_vol,change_ratio,after_share,after_ratio"
        )).head(15),
        _ak,
        "get_stk_holdertrade",
    )


def get_stk_holdernumber(ts_code: str) -> pd.DataFrame:
    """股东人数"""
    def _ak():
        try:
            import akshare as ak
            code6 = to_code6(ts_code)
            df = ak.stock_hold_num_cninfo(symbol=code6)
            return df.head(8) if df is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.stk_holdernumber(
            ts_code=ts_code,
            fields="end_date,holder_num,holder_nums"
        )).head(8),
        _ak,
        "get_stk_holdernumber",
    )


def get_block_trade(ts_code: str) -> pd.DataFrame:
    """大宗交易"""
    def _ak():
        try:
            import akshare as ak
            code6 = to_code6(ts_code)
            df = ak.stock_dzjy_mdetail(symbol=code6)
            return df.head(10) if df is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.block_trade(
            ts_code=ts_code, start_date=ndays_ago(90), end_date=today(),
            fields="trade_date,price,vol,amount,buyer,seller"
        )).head(10),
        _ak,
        "get_block_trade",
    )


def get_dividend(ts_code: str) -> pd.DataFrame:
    """分红送股"""
    def _ak():
        try:
            import akshare as ak
            code6 = to_code6(ts_code)
            df = ak.stock_history_dividend_detail(symbol=code6, indicator="分红")
            return df.head(8) if df is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.dividend(
            ts_code=ts_code,
            fields="end_date,ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,cash_div,cash_div_tax,ex_date,pay_date"
        )).head(8),
        _ak,
        "get_dividend",
    )


def get_fina_audit(ts_code: str) -> pd.DataFrame:
    """审计意见"""
    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.fina_audit(
            ts_code=ts_code,
            fields="end_date,ann_date,audit_result,audit_agency,audit_sign"
        )).head(5),
        lambda: pd.DataFrame(),  # akshare 无审计意见接口，优雅降级
        "get_fina_audit",
    )


def get_forecast(ts_code: str) -> pd.DataFrame:
    """业绩预告"""
    def _ak():
        try:
            import akshare as ak
            code6 = to_code6(ts_code)
            df = ak.stock_profit_forecast_em(symbol=code6)
            return df.head(4) if df is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    from data.fallback import bs_get_forecast
    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.forecast(
            ts_code=ts_code,
            fields="ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max,summary"
        )).head(4),
        _ak,
        "get_forecast",
        bs_fn=lambda: bs_get_forecast(ts_code),
    )


def get_express(ts_code: str) -> pd.DataFrame:
    """业绩快报"""
    from data.fallback import bs_get_express
    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.express(
            ts_code=ts_code,
            fields="end_date,ann_date,revenue,operate_profit,total_profit,n_income,total_assets,total_hldr_eqy_exc_min_int,"
                   "yoy_net_profit,yoy_sales,yoy_op,yoy_tp,yoy_roe"
        )).head(4),
        lambda: pd.DataFrame(),  # akshare 无业绩快报接口
        "get_express",
        bs_fn=lambda: bs_get_express(ts_code),
    )


def get_disclosure_date(ts_code: str) -> pd.DataFrame:
    """财报披露日期"""
    return _ts_or_ak(
        lambda pro: _ts_call(lambda: pro.disclosure_date(
            ts_code=ts_code,
            fields="end_date,pre_date,actual_date,modify_date"
        )).head(4),
        lambda: pd.DataFrame(),  # akshare 无对应接口
        "get_disclosure_date",
    )


# ══════════════════════════════════════════════════════════════════════════════
# 券商研报 & 分析师一致预期
# ══════════════════════════════════════════════════════════════════════════════

def get_research_reports(ts_code: str) -> str:
    """获取券商研报列表（近1年），三路兜底：东方财富 → 同花顺 → 盈利预测

    返回格式化文本，任一成功即可。
    """
    from datetime import datetime, timedelta
    one_year_ago = datetime.now() - timedelta(days=365)
    code6 = to_code6(ts_code)

    # ── 路线1：东方财富研报（akshare stock_research_report_em）──
    try:
        import akshare as ak
        df = ak.stock_research_report_em(symbol=code6)
        if df is not None and not df.empty:
            if "日期" in df.columns:
                # 日期列可能是 datetime.date 或字符串，统一转换
                df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
                df = df[df["日期"] >= one_year_ago]
            df = df.head(8)
            if not df.empty:
                lines = []
                for _, row in df.iterrows():
                    raw_date = row.get("日期", "")
                    date = raw_date.strftime("%Y-%m-%d") if hasattr(raw_date, "strftime") else str(raw_date)
                    org = row.get("机构", "")
                    rating = row.get("东财评级", "")
                    title = row.get("报告名称", "")
                    eps_cols = [c for c in df.columns if "盈利预测-收益" in str(c)]
                    eps_parts = []
                    for c in eps_cols[:2]:
                        v = row.get(c)
                        if pd.notna(v):
                            year = str(c).split("-")[0]
                            eps_parts.append(f"{year}E EPS={v}")
                    eps_str = "，".join(eps_parts) if eps_parts else ""
                    line = f"  {date} | {org} | 评级:{rating} | {title}"
                    if eps_str:
                        line += f" | {eps_str}"
                    lines.append(line)
                logger.info("[get_research_reports] 东方财富研报获取成功: %d条", len(lines))
                return "\n".join(lines)
    except Exception as e:
        logger.debug("[get_research_reports] 东方财富路线失败: %s", e)

    # ── 路线2：盈利一致预期（akshare stock_profit_forecast_em）──
    try:
        import akshare as ak
        df2 = ak.stock_profit_forecast_em(symbol=code6)
        if df2 is not None and not df2.empty:
            lines = ["（东方财富研报接口暂不可用，以下为盈利一致预期数据）"]
            for _, row in df2.head(5).iterrows():
                line = " | ".join(f"{k}={v}" for k, v in row.items() if pd.notna(v))
                lines.append(f"  {line}")
            logger.info("[get_research_reports] 盈利预测获取成功")
            return "\n".join(lines)
    except Exception as e:
        logger.debug("[get_research_reports] 盈利预测路线失败: %s", e)

    logger.warning("[get_research_reports] 全部路线失败: %s", ts_code)
    return "（券商研报数据采集失败，请通过联网搜索补充）"


def get_analyst_consensus_ts(ts_code: str) -> str:
    """获取卖方研报标题+评级，Tushare report_rc 为主，失败则降级。"""
    from datetime import datetime, timedelta
    one_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

    # ── 路线1：Tushare report_rc ──
    pro = get_pro()
    if pro is not None:
        try:
            df = _ts_call(lambda: pro.report_rc(
                ts_code=ts_code,
                fields="report_date,report_title,report_type,classify,organ_name,target_price,op_rt"
            ))
            if df is not None and not df.empty:
                if "report_date" in df.columns:
                    df = df[df["report_date"] >= one_year_ago]
                df = df.head(8)
                if not df.empty:
                    lines = []
                    for _, row in df.iterrows():
                        date = str(row.get("report_date", ""))
                        title = row.get("report_title", "")
                        rtype = row.get("report_type", "")
                        org = row.get("organ_name", "")
                        tp = row.get("target_price")
                        tp_str = f"目标价{tp}" if pd.notna(tp) and tp else ""
                        date_fmt = f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) >= 8 else date
                        line = f"  {date_fmt} | {org} | {rtype} | {title}"
                        if tp_str:
                            line += f" | {tp_str}"
                        lines.append(line)
                    logger.info("[get_analyst_consensus_ts] Tushare研报获取成功: %d条", len(lines))
                    return "\n".join(lines)
        except Exception as e:
            logger.debug("[get_analyst_consensus_ts] Tushare路线失败: %s", e)

    # Tushare 失败时不再重复 akshare（get_research_reports 已覆盖），返回空让 prompt 提示联网搜索
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# 衍生指标计算
# ══════════════════════════════════════════════════════════════════════════════

def calc_dupont(fina_df: pd.DataFrame, bs_df: pd.DataFrame) -> str:
    """杜邦分析：ROE = 净利率 x 总资产周转率 x 权益乘数"""
    if fina_df.empty or bs_df.empty:
        return "数据不足，无法计算杜邦分析"
    try:
        rows = []
        for _, row in fina_df.head(3).iterrows():
            end = row.get("end_date", "")
            npm = row.get("netprofit_margin")
            at = row.get("assets_turn")
            # 权益乘数 = 总资产 / 股东权益
            bs_match = bs_df[bs_df["end_date"] == end]
            em = None
            if not bs_match.empty:
                ta = bs_match.iloc[0].get("total_assets")
                eq = bs_match.iloc[0].get("total_hldr_eqy_exc_min_int")
                if ta and eq and float(eq) > 0:
                    em = float(ta) / float(eq)

            parts = [f"  {end}:"]
            if npm is not None:
                parts.append(f"净利率={float(npm):.2f}%")
            if at is not None:
                parts.append(f"资产周转率={float(at):.4f}")
            if em is not None:
                parts.append(f"权益乘数={em:.2f}")
            if npm is not None and at is not None and em is not None:
                roe_calc = float(npm) / 100 * float(at) * em * 100
                parts.append(f"→ ROE≈{roe_calc:.2f}%")
            rows.append(" ".join(parts))

        return "杜邦分析（近3期）：\n" + "\n".join(rows) if rows else "杜邦分析数据不足"
    except Exception as e:
        logger.debug("[calc_dupont] %s", e)
        return "杜邦分析计算异常"


def calc_fcf(cf_df: pd.DataFrame) -> str:
    """自由现金流估算"""
    if cf_df.empty:
        return "现金流数据不足"
    try:
        rows = []
        for _, row in cf_df.head(3).iterrows():
            end = row.get("end_date", "")
            ocf = row.get("n_cashflow_act")
            fcf = row.get("free_cashflow")
            if fcf is not None:
                rows.append(f"  {end}: FCF={float(fcf)/1e8:.2f}亿 (经营现金流={float(ocf or 0)/1e8:.2f}亿)")
            elif ocf is not None:
                rows.append(f"  {end}: 经营现金流={float(ocf)/1e8:.2f}亿")
        return "自由现金流：\n" + "\n".join(rows) if rows else "自由现金流数据不足"
    except Exception as e:
        logger.debug("[calc_fcf] %s", e)
        return "自由现金流计算异常"


def calc_ccc(
    fina_df: pd.DataFrame,
    bs_df: pd.DataFrame | None = None,
    income_df: pd.DataFrame | None = None,
) -> str:
    """现金转换周期 CCC = DSO + DIO - DPO

    DPO = 365 / (营业成本 / 应付账款)，需要 bs_df 中的 accounts_payable
    和 income_df 中的 total_cogs。若字段缺失则退化为 DSO + DIO。
    """
    if fina_df.empty:
        return "周转数据不足"
    try:
        rows = []
        for _, row in fina_df.head(3).iterrows():
            end = row.get("end_date", "")
            ar_turn = row.get("ar_turn")
            inv_turn = row.get("inv_turn")
            dso = 365 / float(ar_turn) if ar_turn and float(ar_turn) > 0 else None
            dio = 365 / float(inv_turn) if inv_turn and float(inv_turn) > 0 else None

            # DPO：从资产负债表取应付账款，从利润表取营业成本
            dpo = None
            if bs_df is not None and not bs_df.empty and income_df is not None and not income_df.empty:
                bs_match = bs_df[bs_df["end_date"] == end] if "end_date" in bs_df.columns else pd.DataFrame()
                inc_match = income_df[income_df["end_date"] == end] if "end_date" in income_df.columns else pd.DataFrame()
                if not bs_match.empty and not inc_match.empty:
                    ap = bs_match.iloc[0].get("acct_payable")
                    cogs = inc_match.iloc[0].get("total_cogs")
                    if ap is not None and cogs is not None:
                        ap_f, cogs_f = float(ap), float(cogs)
                        if ap_f > 0 and cogs_f > 0:
                            dpo = 365 / (cogs_f / ap_f)

            parts = [f"  {end}:"]
            if dso is not None:
                parts.append(f"DSO={dso:.0f}天")
            if dio is not None:
                parts.append(f"DIO={dio:.0f}天")
            if dpo is not None:
                parts.append(f"DPO={dpo:.0f}天")
            if dso is not None and dio is not None:
                if dpo is not None:
                    ccc = dso + dio - dpo
                    parts.append(f"CCC={ccc:.0f}天")
                else:
                    ccc = dso + dio
                    parts.append(f"CCC≈{ccc:.0f}天(不含DPO)")
            rows.append(" ".join(parts))
        return "现金转换周期：\n" + "\n".join(rows) if rows else "CCC 数据不足"
    except Exception as e:
        logger.debug("[calc_ccc] %s", e)
        return "CCC 计算异常"


def calc_risk_checklist(
    fina_df: pd.DataFrame,
    bs_df: pd.DataFrame,
    cf_df: pd.DataFrame,
    audit_df: pd.DataFrame,
    pledge_text: str,
    holdernumber_df: pd.DataFrame | None = None,
    block_trade_df: pd.DataFrame | None = None,
) -> list[str]:
    """风险快速排查 — 11 项检查，返回触发的风险项列表"""
    risks = []
    try:
        # 1. 扣非净利润连续两年为负
        if not fina_df.empty and "netprofit_of_gr" in fina_df.columns:
            annual = fina_df[fina_df["end_date"].str.endswith("1231")].head(2)
            if len(annual) >= 2:
                vals = annual["netprofit_of_gr"].astype(float).values
                if all(v < 0 for v in vals):
                    risks.append("扣非净利润连续2年为负")

        # 2. 资产负债率 > 70%（非金融）
        if not fina_df.empty and "debt_to_assets" in fina_df.columns:
            da = fina_df.iloc[0].get("debt_to_assets")
            if da is not None and float(da) > 70:
                risks.append(f"资产负债率={float(da):.1f}%（>70%）")

        # 3. 经营现金流连续为负
        if not cf_df.empty and "n_cashflow_act" in cf_df.columns:
            ocf_vals = cf_df["n_cashflow_act"].dropna().astype(float).head(2).values
            if len(ocf_vals) >= 2 and all(v < 0 for v in ocf_vals):
                risks.append("经营现金流连续2期为负")

        # 4. 商誉占净资产 > 30%
        if not bs_df.empty:
            gw = bs_df.iloc[0].get("goodwill")
            eq = bs_df.iloc[0].get("total_hldr_eqy_exc_min_int")
            if gw is not None and eq is not None and float(eq) > 0:
                ratio = float(gw) / float(eq) * 100
                if ratio > 30:
                    risks.append(f"商誉/净资产={ratio:.1f}%（>30%）")

        # 5. 应收账款增速 >> 营收增速
        if not fina_df.empty:
            ar_yoy = fina_df.iloc[0].get("ar_turn")
            rev_yoy = fina_df.iloc[0].get("revenue_yoy")
            # 简化：如果应收周转率很低，标记风险
            if ar_yoy is not None and float(ar_yoy) < 2:
                risks.append(f"应收账款周转率仅{float(ar_yoy):.2f}次（偏低）")

        # 6. ROE < 5% 且非周期底部
        if not fina_df.empty and "roe" in fina_df.columns:
            roe = fina_df.iloc[0].get("roe")
            if roe is not None and float(roe) < 5:
                risks.append(f"ROE={float(roe):.2f}%（<5%）")

        # 7. 审计非标准意见
        if not audit_df.empty:
            latest_audit = audit_df.iloc[0].get("audit_result", "")
            if latest_audit and "标准" not in str(latest_audit):
                risks.append(f"审计意见：{latest_audit}")

        # 8. 质押比例 > 40%
        if pledge_text and "质押比例" in pledge_text:
            import re
            m = re.search(r"质押比例[=＝](\d+\.?\d*)", pledge_text)
            if m and float(m.group(1)) > 40:
                risks.append(f"股权质押比例={m.group(1)}%（>40%）")

        # 9. 营收连续2期负增长
        if not fina_df.empty and "revenue_yoy" in fina_df.columns:
            rev_yoys = fina_df["revenue_yoy"].dropna().astype(float).head(2).values
            if len(rev_yoys) >= 2 and all(v < 0 for v in rev_yoys):
                risks.append("营收连续2期负增长")

        # 10. 股东人数持续增加（散户化，主力出货信号）
        if holdernumber_df is not None and not holdernumber_df.empty:
            hn_col = "holder_num" if "holder_num" in holdernumber_df.columns else (
                "holder_nums" if "holder_nums" in holdernumber_df.columns else None
            )
            if hn_col:
                hn_vals = holdernumber_df[hn_col].dropna().astype(float).head(3).values
                if len(hn_vals) >= 2 and all(
                    hn_vals[i] > hn_vals[i + 1] for i in range(len(hn_vals) - 1)
                ):
                    risks.append(
                        f"股东人数连续{len(hn_vals)}期增加（散户化信号，警惕主力出货）"
                    )

        # 11. 大宗交易折价率 > 5%（机构甩货信号）
        if block_trade_df is not None and not block_trade_df.empty:
            try:
                bt = block_trade_df.copy()
                if "price" in bt.columns and "close" not in bt.columns:
                    # 用大宗价格与当日收盘价对比，需要有参考价
                    # 简化：若大宗价格明显低于近期均价则标记
                    prices = bt["price"].dropna().astype(float)
                    if len(prices) >= 1:
                        # 取最近一笔大宗，与其他大宗均价对比折价
                        if len(prices) >= 2:
                            latest = prices.iloc[0]
                            avg_rest = prices.iloc[1:].mean()
                            if avg_rest > 0:
                                discount = (avg_rest - latest) / avg_rest * 100
                                if discount > 5:
                                    risks.append(
                                        f"近期大宗交易折价约{discount:.1f}%（机构甩货信号）"
                                    )
            except Exception:
                pass

    except Exception as e:
        logger.debug("[calc_risk_checklist] %s", e)

    return risks


# ══════════════════════════════════════════════════════════════════════════════
# 主入口：build_report_context
# ══════════════════════════════════════════════════════════════════════════════

def _df_to_text(df: pd.DataFrame, label: str, max_rows: int = 10) -> str:
    """DataFrame 转文本摘要"""
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        return f"{label}：暂无数据"
    return f"{label}：\n{df.head(max_rows).to_string(index=False)}"


def _tuple_to_text(result, label: str) -> str:
    """(str, err) 元组转文本"""
    if isinstance(result, tuple):
        text, err = result
        return text if text and text != "暂无数据" else f"{label}：暂无数据"
    return str(result) if result else f"{label}：暂无数据"


_report_context_cache: dict[str, tuple[dict, dict]] = {}  # ts_code -> (ctx, raw)
_report_context_cache_date: str = ""
_report_context_cache_lock = threading.Lock()


def build_report_context(ts_code: str, name: str, progress_cb=None) -> tuple[dict, dict]:
    """采集全量数据，返回 (context_dict, raw_data_dict)

    context_dict: 可直接注入 prompt 的文本字典
    raw_data_dict: 原始 DataFrame/文本，供 UI 或后续计算使用

    分3批并行获取，每批最多5个并发。
    当日同一股票重复调用直接返回缓存。
    """
    global _report_context_cache, _report_context_cache_date
    from datetime import datetime

    today_date = datetime.now().strftime("%Y-%m-%d")
    with _report_context_cache_lock:
        # 跨日清空缓存
        if _report_context_cache_date != today_date:
            _report_context_cache.clear()
            _report_context_cache_date = today_date

        # 命中缓存直接返回
        if ts_code in _report_context_cache:
            logger.info("[report_data] cache hit for %s, skip 18 API calls", ts_code)
            return _report_context_cache[ts_code]

    raw = {}
    ctx = {}

    def _progress(msg):
        if progress_cb:
            progress_cb(msg)

    # ── Batch 1: 基础数据 + 财务三表 + 财务指标 ──────────────────────
    _progress("获取基础数据与财务三表...")

    batch1_tasks = {
        "info": lambda: get_basic_info(ts_code),
        "price": lambda: get_price_df(ts_code),
        "income": lambda: get_income(ts_code),
        "balance": lambda: get_balancesheet(ts_code),
        "cashflow": lambda: get_cashflow(ts_code),
    }

    def _run_batch(tasks: dict, label: str, timeout: int = 30) -> None:
        """并行执行一批任务，带超时保护。单个任务失败不影响其他。"""
        with ThreadPoolExecutor(max_workers=5) as pool:
            futs = {pool.submit(fn): key for key, fn in tasks.items()}
            try:
                for fut in as_completed(futs, timeout=timeout):
                    key = futs[fut]
                    try:
                        raw[key] = fut.result(timeout=5)
                    except Exception as exc:
                        logger.warning("[report_data] %s.%s 失败: %r", label, key, exc)
                        raw[key] = None
            except TimeoutError:
                # 批次整体超时，记录未完成的任务
                for fut, key in futs.items():
                    if not fut.done():
                        logger.warning("[report_data] %s.%s 超时(%ds)", label, key, timeout)
                        raw.setdefault(key, None)

    _run_batch(batch1_tasks, "batch1", timeout=25)

    _progress("获取财务指标与股东数据...")

    # ── Batch 2: 财务指标 + 股东 + 资金 ──────────────────────────────
    batch2_tasks = {
        "fina_ind": lambda: get_fina_indicator(ts_code),
        "mainbz": lambda: get_fina_mainbz(ts_code),
        "capital": lambda: get_capital_flow(ts_code),
        "holders": lambda: get_holders_info(ts_code),
        "pledge": lambda: get_pledge_info(ts_code),
    }

    _run_batch(batch2_tasks, "batch2", timeout=25)

    _progress("获取增减持、解禁、分红等数据...")

    # ── Batch 3: 增减持 + 解禁 + 分红 + 审计 + 预告 + 其他 ──────────
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
        "research_reports": lambda: get_research_reports(ts_code),
        "analyst_consensus": lambda: get_analyst_consensus_ts(ts_code),
    }

    _run_batch(batch3_tasks, "batch3", timeout=30)

    _progress("计算衍生指标...")

    # ── 解包元组类型结果（含 None 安全处理）────────────────────────
    def _unpack(key, default=None):
        """安全解包：None→default, (val, err)→val, 其他→原值"""
        v = raw.get(key)
        if v is None:
            return default
        if isinstance(v, tuple) and len(v) >= 1:
            return v[0]
        return v

    info = _unpack("info", {})
    price_df = _unpack("price", pd.DataFrame())
    if not isinstance(price_df, pd.DataFrame):
        price_df = pd.DataFrame()

    income_df = _unpack("income", pd.DataFrame()) or pd.DataFrame()
    bs_df = _unpack("balance", pd.DataFrame()) or pd.DataFrame()
    cf_df = _unpack("cashflow", pd.DataFrame()) or pd.DataFrame()
    fina_df = _unpack("fina_ind", pd.DataFrame()) or pd.DataFrame()
    mainbz_df = _unpack("mainbz", pd.DataFrame()) or pd.DataFrame()
    audit_df = _unpack("audit", pd.DataFrame()) or pd.DataFrame()

    # ── 构建 context dict ─────────────────────────────────────────────
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
    if "暂无数据" in ctx["holdernumber"]:
        try:
            from data.fallback import em_get_holdernumber_dc
            text, _ = em_get_holdernumber_dc(ts_code)
            if text:
                ctx["holdernumber"] = text
        except Exception:
            pass
    ctx["share_float"] = _df_to_text(raw.get("share_float", pd.DataFrame()), "限售解禁")
    ctx["repurchase"] = _df_to_text(raw.get("repurchase", pd.DataFrame()), "股票回购")
    ctx["block_trade"] = _df_to_text(raw.get("block_trade", pd.DataFrame()), "大宗交易")
    ctx["dividend"] = _df_to_text(raw.get("dividend", pd.DataFrame()), "分红送股")
    ctx["audit"] = _df_to_text(audit_df, "审计意见")
    ctx["forecast"] = _df_to_text(raw.get("forecast", pd.DataFrame()), "业绩预告")
    ctx["express"] = _df_to_text(raw.get("express", pd.DataFrame()), "业绩快报")
    ctx["disclosure"] = _df_to_text(raw.get("disclosure", pd.DataFrame()), "财报披露日期")

    # 券商研报（两路获取，任一成功即可）
    rr = raw.get("research_reports", "")
    ac = raw.get("analyst_consensus", "")
    ctx["research_reports"] = rr if isinstance(rr, str) else ""
    ctx["analyst_consensus"] = ac if isinstance(ac, str) else ""

    # ── 衍生指标 ──────────────────────────────────────────────────────
    pledge_text = ctx.get("pledge", "")
    ctx["dupont"] = calc_dupont(fina_df, bs_df)
    if not ctx["dupont"] or "数据不足" in ctx["dupont"] or "异常" in ctx["dupont"]:
        try:
            from data.fallback import bs_get_dupont_data
            dupont_text, _ = bs_get_dupont_data(ts_code)
            if dupont_text and "数据不足" not in dupont_text:
                ctx["dupont"] = dupont_text
        except Exception:
            pass
    ctx["fcf"] = calc_fcf(cf_df)
    ctx["ccc"] = calc_ccc(fina_df, bs_df=bs_df, income_df=income_df)

    risk_items = calc_risk_checklist(
        fina_df, bs_df, cf_df, audit_df, pledge_text,
        holdernumber_df=raw.get("holdernumber", pd.DataFrame()),
        block_trade_df=raw.get("block_trade", pd.DataFrame()),
    )
    ctx["risk_checklist"] = (
        "风险快速排查：\n" + "\n".join(f"  - {r}" for r in risk_items)
        if risk_items
        else "风险快速排查：未触发任何风险项"
    )

    # ── 存入 raw 供 UI 使用 ───────────────────────────────────────────
    raw["_info"] = info
    raw["_price_df"] = price_df
    raw["_fina_df"] = fina_df
    raw["_bs_df"] = bs_df
    raw["_cf_df"] = cf_df

    # ── 财报情报概况 ──────────────────────────────────────────────
    ctx["report_period_info"] = _build_report_period_info(
        income_df, fina_df,
        raw.get("forecast", pd.DataFrame()),
        raw.get("express", pd.DataFrame()),
        raw.get("disclosure", pd.DataFrame()),
    )

    # ── 数据源可信度标注 ─────────────────────────────────────────────
    from data.tushare_client import get_data_source
    source = get_data_source()
    if source == "tushare":
        ctx["data_source_note"] = ""
    elif source == "akshare":
        ctx["data_source_note"] = "⚠️ 情报可信度提示：当前数据来自备用数据源(akshare)，部分字段精度可能低于一手数据源(Tushare)，请注意交叉验证"
    elif source == "eastmoney":
        ctx["data_source_note"] = "⚠️ 情报可信度提示：当前数据来自保底数据源(东方财富HTTP)，数据覆盖面有限，分析结论需谨慎对待"
    else:
        ctx["data_source_note"] = "⚠️ 情报可信度提示：数据源异常，本次分析数据可能不完整"

    _progress("数据采集完成！")

    # 写入当日缓存
    with _report_context_cache_lock:
        _report_context_cache[ts_code] = (ctx, raw)

    return ctx, raw


def _period_label(end_date_str: str) -> str:
    """将 end_date（如 '20240930'）转为人类可读期次标签"""
    if not end_date_str or len(end_date_str) < 8:
        return end_date_str
    y = end_date_str[:4]
    md = end_date_str[4:]
    mapping = {"0331": "一季报(Q1)", "0630": "中报(H1)", "0930": "三季报(Q3)", "1231": "年报"}
    label = mapping.get(md, md)
    return f"{y}年{label}"


def _days_ago(date_str: str) -> int:
    """计算日期距今天数"""
    from datetime import datetime
    try:
        dt = datetime.strptime(str(date_str)[:8], "%Y%m%d")
        return (datetime.now() - dt).days
    except Exception:
        return -1


def _build_report_period_info(
    income_df: pd.DataFrame,
    fina_df: pd.DataFrame,
    forecast_df: pd.DataFrame,
    express_df: pd.DataFrame,
    disclosure_df: pd.DataFrame,
) -> str:
    """构建【财报情报概况】文本，标注最新财报期次、数据时效、预告/快报状态"""
    lines = []

    # 1. 识别最新正式财报期次
    latest_end = None
    latest_ann = None
    for df in [income_df, fina_df]:
        if df is not None and not df.empty and "end_date" in df.columns:
            latest_end = str(df["end_date"].iloc[0])
            if "ann_date" in df.columns:
                latest_ann = str(df["ann_date"].iloc[0]) if pd.notna(df["ann_date"].iloc[0]) else None
            break

    if latest_end:
        label = _period_label(latest_end)
        days = _days_ago(latest_end)
        ann_part = ""
        if latest_ann:
            ann_days = _days_ago(latest_ann)
            ann_part = f"，披露于{latest_ann[:4]}-{latest_ann[4:6]}-{latest_ann[6:]}（{ann_days}天前）"
        lines.append(f"■ 最新正式财报：{label}（报告期{latest_end[:4]}-{latest_end[4:6]}-{latest_end[6:]}{ann_part}）")
        if days > 120:
            lines.append(f"  ⚠️ 距最新正式财报已过{days}天，数据可能滞后，务必结合预告/快报和联网搜索交叉验证")
    else:
        lines.append("■ 最新正式财报：未获取到，请通过联网搜索补充")

    # 2. 数据覆盖范围
    if income_df is not None and not income_df.empty and "end_date" in income_df.columns:
        oldest = str(income_df["end_date"].iloc[-1])
        newest = str(income_df["end_date"].iloc[0])
        lines.append(f"■ 利润表覆盖：{_period_label(oldest)} ~ {_period_label(newest)}（{len(income_df)}期）")

    # 3. 业绩预告
    if forecast_df is not None and not forecast_df.empty:
        row = forecast_df.iloc[0]
        fc_end = str(row.get("end_date", ""))
        fc_ann = str(row.get("ann_date", ""))
        fc_type = row.get("type", "")
        fc_days = _days_ago(fc_ann) if fc_ann else -1
        lines.append(f"■ 业绩预告：{_period_label(fc_end)}预告已发布（{fc_type}，{fc_days}天前发布）")
    else:
        lines.append("■ 业绩预告：暂无")

    # 4. 业绩快报
    if express_df is not None and not express_df.empty:
        row = express_df.iloc[0]
        ex_end = str(row.get("end_date", ""))
        ex_ann = str(row.get("ann_date", ""))
        ex_days = _days_ago(ex_ann) if ex_ann else -1
        lines.append(f"■ 业绩快报：{_period_label(ex_end)}快报已发布（{ex_days}天前发布）")

    # 5. 下一份财报披露日
    if disclosure_df is not None and not disclosure_df.empty:
        for _, row in disclosure_df.iterrows():
            actual = row.get("actual_date")
            pre = row.get("pre_date")
            end = str(row.get("end_date", ""))
            if pd.isna(actual) and pd.notna(pre):
                lines.append(f"■ 待披露：{_period_label(end)}，预计披露日{str(pre)[:4]}-{str(pre)[4:6]}-{str(pre)[6:]}")
                break

    return "\n".join(lines) if lines else "财报期次信息暂无"
