"""持仓数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Position:
    """一笔持仓记录。"""
    stock_code: str           # ts_code (如 600519.SH)
    stock_name: str
    entry_price: float
    entry_date: str           # YYYY-MM-DD
    shares: int               # 股数
    stop_loss: float          # 止损价
    take_profit: float        # 止盈价
    thesis: str               # 买入逻辑
    report_id: str = ""       # 关联的分析报告 ID
    position_id: str = ""     # 唯一标识
    status: str = "open"      # open / closed
    close_date: str = ""      # 平仓日期
    close_price: float = 0.0  # 平仓价
    close_reason: str = ""    # 平仓原因


@dataclass
class RiskAlert:
    """风险告警。"""
    level: str       # critical / warning / info
    stock_code: str
    stock_name: str
    message: str
    current_price: float = 0.0
    detail: str = ""
