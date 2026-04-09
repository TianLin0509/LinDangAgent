"""风控规则引擎 — 检查持仓风险并生成告警。"""

from __future__ import annotations

import logging

from portfolio.models import RiskAlert

logger = logging.getLogger(__name__)


def _get_latest_price(ts_code: str) -> float | None:
    """获取最新价。"""
    try:
        from data.tushare_client import get_price_df
        df, err = get_price_df(ts_code, days=3)
        if df is not None and not df.empty:
            return float(df.iloc[-1]["收盘"])
    except Exception:
        pass
    return None


def check_position_risks(position: dict, current_price: float | None = None) -> list[RiskAlert]:
    """检查单个持仓的风险。返回告警列表。current_price 可外部传入避免重复查询。"""
    alerts: list[RiskAlert] = []
    code = position["stock_code"]
    name = position["stock_name"]

    if current_price is None:
        current_price = _get_latest_price(code)
    if current_price is None:
        alerts.append(RiskAlert(
            level="warning", stock_code=code, stock_name=name,
            message="无法获取最新价格",
        ))
        return alerts

    entry_price = position["entry_price"]
    stop_loss = position["stop_loss"]
    take_profit = position["take_profit"]
    pnl_pct = (current_price - entry_price) / entry_price * 100

    # 止损检查
    if stop_loss > 0 and current_price <= stop_loss:
        alerts.append(RiskAlert(
            level="critical", stock_code=code, stock_name=name,
            current_price=current_price,
            message=f"触发止损！当前 {current_price:.2f} <= 止损 {stop_loss:.2f}",
            detail=f"建仓 {entry_price:.2f}，浮亏 {pnl_pct:.1f}%",
        ))

    # 止盈检查
    if take_profit > 0 and current_price >= take_profit:
        alerts.append(RiskAlert(
            level="info", stock_code=code, stock_name=name,
            current_price=current_price,
            message=f"触发止盈！当前 {current_price:.2f} >= 止盈 {take_profit:.2f}",
            detail=f"建仓 {entry_price:.2f}，浮盈 {pnl_pct:.1f}%",
        ))

    # 大幅回撤告警（>15%）
    if pnl_pct < -15:
        alerts.append(RiskAlert(
            level="warning", stock_code=code, stock_name=name,
            current_price=current_price,
            message=f"回撤 {pnl_pct:.1f}%，超过 15% 告警线",
            detail=f"建仓 {entry_price:.2f} → 当前 {current_price:.2f}",
        ))

    # 大幅浮盈未止盈告警（>30%）
    if pnl_pct > 30 and take_profit == 0:
        alerts.append(RiskAlert(
            level="info", stock_code=code, stock_name=name,
            current_price=current_price,
            message=f"浮盈 {pnl_pct:.1f}% 但未设止盈位，建议设定止盈",
        ))

    return alerts


def check_portfolio_risks(positions: list[dict]) -> dict:
    """检查整个投资组合的风险。"""
    all_alerts: list[dict] = []
    portfolio_value = 0.0
    position_details = []

    for pos in positions:
        current_price = _get_latest_price(pos["stock_code"])
        entry_price = pos["entry_price"]
        shares = pos["shares"]

        if current_price:
            market_value = current_price * shares
            cost_value = entry_price * shares
            pnl = market_value - cost_value
            pnl_pct = pnl / cost_value * 100
            portfolio_value += market_value
        else:
            market_value = entry_price * shares
            pnl = 0
            pnl_pct = 0
            portfolio_value += market_value

        position_details.append({
            "position_id": pos["position_id"],
            "stock_name": pos["stock_name"],
            "stock_code": pos["stock_code"],
            "shares": shares,
            "entry_price": entry_price,
            "current_price": current_price or 0,
            "market_value": round(market_value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 1),
            "stop_loss": pos["stop_loss"],
            "take_profit": pos["take_profit"],
        })

        # 单个持仓风控（传入已获取的价格，避免重复查询）
        alerts = check_position_risks(pos, current_price=current_price)
        for a in alerts:
            all_alerts.append({
                "level": a.level,
                "stock": a.stock_name,
                "message": a.message,
                "detail": a.detail,
            })

    # 集中度检查
    if portfolio_value > 0:
        for pd_ in position_details:
            weight = pd_["market_value"] / portfolio_value * 100
            pd_["weight_pct"] = round(weight, 1)
            if weight > 30:
                all_alerts.append({
                    "level": "warning",
                    "stock": pd_["stock_name"],
                    "message": f"持仓集中度 {weight:.1f}%，超过 30% 告警线",
                    "detail": "建议分散持仓，降低单票风险",
                })

    # 按严重度排序
    level_order = {"critical": 0, "warning": 1, "info": 2}
    all_alerts.sort(key=lambda a: level_order.get(a["level"], 9))

    total_pnl = sum(p["pnl"] for p in position_details)
    cost_value = portfolio_value - total_pnl
    total_pnl_pct = (total_pnl / cost_value * 100) if cost_value > 0 else 0

    return {
        "portfolio_value": round(portfolio_value, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 1),
        "position_count": len(positions),
        "positions": position_details,
        "alerts": all_alerts,
        "critical_count": sum(1 for a in all_alerts if a["level"] == "critical"),
        "warning_count": sum(1 for a in all_alerts if a["level"] == "warning"),
    }
