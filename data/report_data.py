"""深度报告数据层 — 18 个 Tushare API + 衍生指标 + build_report_context()

采集全量基本面/财务/资金/股东数据，供综合投研报告使用。
使用 ThreadPoolExecutor 并行获取，Semaphore(5) 限制并发。
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
# 15 个新 Tushare API 函数
# ══════════════════════════════════════════════════════════════════════════════

def _ts_call(fn):
    """带信号量和重试的 Tushare 调用"""
    with _sem:
        return _retry_call(fn, retries=3, delay=1)


def get_income(ts_code: str) -> pd.DataFrame:
    """利润表（近8期）"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.income(
            ts_code=ts_code,
            fields="end_date,ann_date,revenue,operate_profit,total_profit,"
                   "n_income,n_income_attr_p,basic_eps,diluted_eps,"
                   "total_cogs,sell_exp,admin_exp,rd_exp,fin_exp"
        ))
        return df.head(8) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_income] %s: %s", ts_code, e)
        return pd.DataFrame()


def get_balancesheet(ts_code: str) -> pd.DataFrame:
    """资产负债表（近4期）"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.balancesheet(
            ts_code=ts_code,
            fields="end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int,"
                   "accounts_receiv,inventories,goodwill,money_cap,"
                   "total_cur_assets,total_cur_liab,lt_borr,bond_payable,"
                   "notes_receiv,prepayment,oth_receiv"
        ))
        return df.head(4) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_balancesheet] %s: %s", ts_code, e)
        return pd.DataFrame()


def get_cashflow(ts_code: str) -> pd.DataFrame:
    """现金流量表（近4期）"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.cashflow(
            ts_code=ts_code,
            fields="end_date,n_cashflow_act,n_cashflow_inv_act,n_cashflow_fnc_act,"
                   "c_fr_sale_sg,c_paid_goods_s,free_cashflow"
        ))
        return df.head(4) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_cashflow] %s: %s", ts_code, e)
        return pd.DataFrame()


def get_fina_indicator(ts_code: str) -> pd.DataFrame:
    """财务指标（近8期）"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.fina_indicator(
            ts_code=ts_code,
            fields="end_date,roe,roe_waa,roa,grossprofit_margin,netprofit_margin,"
                   "debt_to_assets,current_ratio,quick_ratio,"
                   "revenue_yoy,netprofit_yoy,basic_eps,"
                   "bps,cfps,ebit_of_gr,netprofit_of_gr,"
                   "ar_turn,inv_turn,assets_turn,"
                   "op_yoy,ocf_yoy,equity_yoy"
        ))
        return df.head(8) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_fina_indicator] %s: %s", ts_code, e)
        return pd.DataFrame()


def get_fina_mainbz(ts_code: str) -> pd.DataFrame:
    """主营业务构成"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.fina_mainbz(
            ts_code=ts_code, type="P",
            fields="end_date,bz_item,bz_sales,bz_profit,bz_cost"
        ))
        return df.head(20) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_fina_mainbz] %s: %s", ts_code, e)
        return pd.DataFrame()


def get_share_float(ts_code: str) -> pd.DataFrame:
    """限售股解禁计划"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.share_float(
            ts_code=ts_code,
            fields="ann_date,float_date,float_share,float_ratio,holder_name,share_type"
        ))
        return df.head(10) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_share_float] %s: %s", ts_code, e)
        return pd.DataFrame()


def get_repurchase(ts_code: str) -> pd.DataFrame:
    """股票回购"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.repurchase(
            ts_code=ts_code,
            fields="ann_date,end_date,proc,exp_date,vol,amount,high_limit,low_limit"
        ))
        return df.head(5) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_repurchase] %s: %s", ts_code, e)
        return pd.DataFrame()


def get_stk_holdertrade(ts_code: str) -> pd.DataFrame:
    """股东增减持"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.stk_holdertrade(
            ts_code=ts_code,
            fields="ann_date,holder_name,holder_type,in_de,change_vol,change_ratio,after_share,after_ratio"
        ))
        return df.head(15) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_stk_holdertrade] %s: %s", ts_code, e)
        return pd.DataFrame()


def get_stk_holdernumber(ts_code: str) -> pd.DataFrame:
    """股东人数"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.stk_holdernumber(
            ts_code=ts_code,
            fields="end_date,holder_num,holder_nums"
        ))
        return df.head(8) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_stk_holdernumber] %s: %s", ts_code, e)
        return pd.DataFrame()


def get_block_trade(ts_code: str) -> pd.DataFrame:
    """大宗交易"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.block_trade(
            ts_code=ts_code, start_date=ndays_ago(90), end_date=today(),
            fields="trade_date,price,vol,amount,buyer,seller"
        ))
        return df.head(10) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_block_trade] %s: %s", ts_code, e)
        return pd.DataFrame()


def get_dividend(ts_code: str) -> pd.DataFrame:
    """分红送股"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.dividend(
            ts_code=ts_code,
            fields="end_date,ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,cash_div,cash_div_tax,ex_date,pay_date"
        ))
        return df.head(8) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_dividend] %s: %s", ts_code, e)
        return pd.DataFrame()


def get_fina_audit(ts_code: str) -> pd.DataFrame:
    """审计意见"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.fina_audit(
            ts_code=ts_code,
            fields="end_date,ann_date,audit_result,audit_agency,audit_sign"
        ))
        return df.head(5) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_fina_audit] %s: %s", ts_code, e)
        return pd.DataFrame()


def get_forecast(ts_code: str) -> pd.DataFrame:
    """业绩预告"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.forecast(
            ts_code=ts_code,
            fields="ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max,summary"
        ))
        return df.head(4) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_forecast] %s: %s", ts_code, e)
        return pd.DataFrame()


def get_express(ts_code: str) -> pd.DataFrame:
    """业绩快报"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.express(
            ts_code=ts_code,
            fields="end_date,ann_date,revenue,operate_profit,total_profit,n_income,total_assets,total_hldr_eqy_exc_min_int,"
                   "yoy_net_profit,yoy_sales,yoy_op,yoy_tp,yoy_roe"
        ))
        return df.head(4) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_express] %s: %s", ts_code, e)
        return pd.DataFrame()


def get_disclosure_date(ts_code: str) -> pd.DataFrame:
    """财报披露日期"""
    pro = get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = _ts_call(lambda: pro.disclosure_date(
            ts_code=ts_code,
            fields="end_date,pre_date,actual_date,modify_date"
        ))
        return df.head(4) if df is not None and not df.empty else pd.DataFrame()
    except Exception as e:
        logger.debug("[get_disclosure_date] %s: %s", ts_code, e)
        return pd.DataFrame()


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


def calc_ccc(fina_df: pd.DataFrame) -> str:
    """现金转换周期 CCC = DSO + DIO - DPO（基于周转率估算）"""
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
            parts = [f"  {end}:"]
            if dso is not None:
                parts.append(f"DSO={dso:.0f}天")
            if dio is not None:
                parts.append(f"DIO={dio:.0f}天")
            if dso is not None and dio is not None:
                ccc = dso + dio  # DPO 需要应付账款数据，这里简化
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
) -> list[str]:
    """风险快速排查 — 9 项检查，返回触发的风险项列表"""
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


def build_report_context(ts_code: str, name: str, progress_cb=None) -> tuple[dict, dict]:
    """采集全量数据，返回 (context_dict, raw_data_dict)

    context_dict: 可直接注入 prompt 的文本字典
    raw_data_dict: 原始 DataFrame/文本，供 UI 或后续计算使用

    分3批并行获取，每批最多5个并发。
    """
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

    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {pool.submit(fn): key for key, fn in batch1_tasks.items()}
        for fut in as_completed(futs):
            raw[futs[fut]] = fut.result()

    _progress("获取财务指标与股东数据...")

    # ── Batch 2: 财务指标 + 股东 + 资金 ──────────────────────────────
    batch2_tasks = {
        "fina_ind": lambda: get_fina_indicator(ts_code),
        "mainbz": lambda: get_fina_mainbz(ts_code),
        "capital": lambda: get_capital_flow(ts_code),
        "holders": lambda: get_holders_info(ts_code),
        "pledge": lambda: get_pledge_info(ts_code),
    }

    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {pool.submit(fn): key for key, fn in batch2_tasks.items()}
        for fut in as_completed(futs):
            raw[futs[fut]] = fut.result()

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
    }

    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {pool.submit(fn): key for key, fn in batch3_tasks.items()}
        for fut in as_completed(futs):
            raw[futs[fut]] = fut.result()

    _progress("计算衍生指标...")

    # ── 解包元组类型结果 ──────────────────────────────────────────────
    info = raw["info"][0] if isinstance(raw["info"], tuple) else raw.get("info", {})
    price_df = raw["price"][0] if isinstance(raw["price"], tuple) else raw.get("price", pd.DataFrame())

    income_df = raw.get("income", pd.DataFrame())
    bs_df = raw.get("balance", pd.DataFrame())
    cf_df = raw.get("cashflow", pd.DataFrame())
    fina_df = raw.get("fina_ind", pd.DataFrame())
    mainbz_df = raw.get("mainbz", pd.DataFrame())
    audit_df = raw.get("audit", pd.DataFrame())

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
    ctx["share_float"] = _df_to_text(raw.get("share_float", pd.DataFrame()), "限售解禁")
    ctx["repurchase"] = _df_to_text(raw.get("repurchase", pd.DataFrame()), "股票回购")
    ctx["block_trade"] = _df_to_text(raw.get("block_trade", pd.DataFrame()), "大宗交易")
    ctx["dividend"] = _df_to_text(raw.get("dividend", pd.DataFrame()), "分红送股")
    ctx["audit"] = _df_to_text(audit_df, "审计意见")
    ctx["forecast"] = _df_to_text(raw.get("forecast", pd.DataFrame()), "业绩预告")
    ctx["express"] = _df_to_text(raw.get("express", pd.DataFrame()), "业绩快报")
    ctx["disclosure"] = _df_to_text(raw.get("disclosure", pd.DataFrame()), "财报披露日期")

    # ── 衍生指标 ──────────────────────────────────────────────────────
    pledge_text = ctx.get("pledge", "")
    ctx["dupont"] = calc_dupont(fina_df, bs_df)
    ctx["fcf"] = calc_fcf(cf_df)
    ctx["ccc"] = calc_ccc(fina_df)

    risk_items = calc_risk_checklist(fina_df, bs_df, cf_df, audit_df, pledge_text)
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

    _progress("数据采集完成！")
    return ctx, raw
