# -*- coding: utf-8 -*-
"""侦察兵快速评估 prompt — 用于 Top100 初筛阶段

林彪打仗前先派侦察连摸一遍敌情：不需要写完整战报，但侦察情报必须够全，
判断框架必须够硬——否则就会漏掉真正的好战场。
"""

import pandas as pd


SCOUT_SYSTEM = (
    "你是四野侦察连长，负责快速评估一个阵地是否值得主力部队投入兵力。\n"
    "\n"
    "你的判断框架（一点两面简化版）：\n"
    "■ 一点：有没有一个清晰的主攻逻辑（题材催化/业绩拐点/资金共振）？\n"
    "■ 两面验证：估值面是否合理（PE/PB vs 行业）？技术面是否有进攻信号？\n"
    "\n"
    "��分标准：\n"
    "- 85-100：主攻逻辑锐利 + 估值/技术双面验证 + 资金共振 → 必须深度分析\n"
    "- 70-84：逻辑清晰，至少一面验证充分，另一面尚可 → 值得深度\n"
    "- 55-69：有一定亮点但信号不强或有矛盾 → 备选\n"
    "- 40-54：逻辑模糊，估值/技术至少一面明显不利 → 暂时观望\n"
    "- 0-39：估值严重高估 或 技术面破位 或 资金明确出逃 → 放弃\n"
    "注意：不要扎堆在60-70区间，充分利用0-100的区分度。\n"
    "\n"
    "严格按指定格式输出，不要多写。"
)


def build_scout_prompt(row: pd.Series) -> str:
    """用 enrich_candidates 已有数据构建侦察 prompt"""

    name = row.get("股票名称", "未知")
    code = row.get("代码", "")
    price = row.get("最新价", row.get("收盘", "?"))
    pct = row.get("涨跌幅", "?")
    amount = row.get("成交额(亿)", row.get("成交���", "?"))
    turnover = row.get("换手率", "?")
    volume_ratio = row.get("量比", "?")
    net_flow = row.get("主力净流入(万)", "?")

    pe = row.get("PE", "?")
    pb = row.get("PB", "?")
    mv = row.get("总市值(亿)", "?")
    industry = row.get("行业", "?")
    ind_pe = row.get("行业PE均值", "?")
    ind_pb = row.get("行业PB均值", "?")

    # 量化信号
    quant_signal = row.get("量化信号", "?")
    tech_score = row.get("技术面分", "?")
    flow_score = row.get("资金面分", "?")
    val_score = row.get("估值面分", "?")
    momentum = row.get("动量分", "?")
    quant_total = row.get("量化总分", "?")

    # K线摘要（包含RSI/MACD/均线/近5日行情）
    kline = row.get("K线摘要", "")
    kline_section = kline[:500] if kline else "K线数据暂无"

    # v3.0：K线形态标签
    kline_pattern = row.get("K线形态", "无明显形态")

    # v3.0：连板天数（来自涨停池）
    lb_days = row.get("连板天数")
    lb_info = f" | 连板{int(lb_days)}天" if pd.notna(lb_days) and lb_days and int(lb_days) > 0 else ""

    # 热度排名
    rank_parts = []
    for col in ["人气排名", "成交额排名", "雪球排名"]:
        v = row.get(col)
        if pd.notna(v):
            rank_parts.append(f"{col}第{int(v)}")
    rank_info = " | ".join(rank_parts) if rank_parts else "无排名数据"

    # 涨跌幅
    chg_5d = row.get("5日涨幅", row.get("近5日涨幅", "?"))
    chg_20d = row.get("20日涨幅", row.get("近20日涨幅", "?"))

    # v3.0：数据来源标记
    source = row.get("来源", "")
    source_info = f"  来源：{source}" if source else ""

    return f"""【侦察目标】{name}（{code}）

【价格与资金】
  价格 {price} 元 | 今日涨跌 {pct}% | 成交额 {amount} 亿 | 换手率 {turnover}% | 量比 {volume_ratio}{lb_info}
  主力净流入 {net_flow} 万

【估值】
  PE(TTM) = {pe}（行业均值 {ind_pe}）| PB = {pb}（行业均值 {ind_pb}）
  总市值 {mv} 亿 | 行业：{industry}

【量化多因子】
  综合 {quant_total} | 技术 {tech_score} | 资金 {flow_score} | 估值 {val_score} | 动量 {momentum}
  信号：{quant_signal}

【K线形态判断】{kline_pattern}

【技术面详情】
{kline_section}

【热度排名】{rank_info}
{source_info}

【近期走势】5日涨幅 {chg_5d}% | 20日涨幅 {chg_20d}%

---
请基于以上情报，用"一点两面"框架快速判断，严格按以下格式输出（只输出这三行，不要多写）：
评分：X/100
主攻逻辑：（≤20字，这只股票最锐利的一个核心逻辑）
风险点：（≤20字，最大的一个顾虑）"""
