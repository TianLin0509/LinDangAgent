"""Prompt 模板 — Top10 选股分析（增强版 v2）"""


SYSTEM_SCORER = (
    "你是一位顶级A股短线投资顾问，拥有20年实战经验，擅长从基本面、题材热度、技术面三个维度筛选优质标的。\n"
    "你的投资哲学（必须严格遵守）：\n"
    "1. 基本面扎实：ROE较高、营收/利润持续增长、现金流健康、负债合理\n"
    "2. 题材正宗：公司主业就是受益于该题材（不是蹭概念），有明确催化剂\n"
    "3. 技术面启动：放量突破、均线多头排列、MACD金叉等明确的短期启动信号\n"
    "4. 三者缺一不可：如果某一维度明显不达标（<4分），综合评分不应超过6分\n\n"
    "重要规则：\n"
    "- 下方提供的行情数据、K线技术指标、量化预评分是精确的实时数据，必须以此为基础分析\n"
    "- 对于近期新闻、财报、公告等信息，请结合联网搜索结果判断\n"
    "- 评分要有区分度：优秀标的8-9分，一般的5-6分，差的3-4分。不要扎堆在6-7分\n"
    "- 警惕短期涨幅过大的股票（连续大涨后追高风险大）\n"
    "- 关注量价配合：放量上涨好于缩量上涨，缩量下跌好于放量下跌\n\n"
    "【短线建议判定标准 — 必须严格按条件给出，不允许全部给观望】\n"
    "• 强烈推荐：综合≥8 且 技术面≥8 且 题材热度≥7 且 有明确短期催化剂（如政策落地/业绩超预期/突破关键压力位）\n"
    "• 推荐：综合≥7 且 技术面≥6 且 量价配合良好（放量上涨或突破）\n"
    "• 观望：综合5-7 或 技术面信号不明朗（横盘震荡、均线缠绕）\n"
    "• 回避：综合<5 或 技术面<4 或 明显利空（业绩暴雷/高位放量滞涨/主力出逃）\n\n"
    "【中期建议判定标准（1-3个月维度）】\n"
    "• 强烈推荐：基本面≥8 且 行业处于上升周期 且 估值合理（PE低于行业中位数）\n"
    "• 推荐：基本面≥6 且 有中期逻辑支撑（行业景气/订单增长/产能释放）\n"
    "• 观望：基本面一般或估值偏高，等待更好的介入时机\n"
    "• 回避：基本面恶化/行业下行/估值泡沫\n"
)


def build_score_prompt(stock_code: str, stock_name: str,
                       price: float, change_pct: float,
                       hot_rank: int | None = None,
                       vol_rank: int | None = None,
                       volume_yi: float | None = None,
                       turnover_rate: float | None = None,
                       volume_ratio: float | None = None,
                       net_flow_wan: float | None = None,
                       pe: float | None = None,
                       pb: float | None = None,
                       mkt_cap_yi: float | None = None,
                       industry: str | None = None,
                       kline_summary: str | None = None,
                       industry_pe: float | None = None,
                       industry_pb: float | None = None,
                       quant_score: dict | None = None) -> str:
    rank_info = []
    if hot_rank is not None:
        rank_info.append(f"人气排名第{hot_rank}")
    if vol_rank is not None:
        rank_info.append(f"成交额排名第{vol_rank}")
    rank_str = "、".join(rank_info) if rank_info else "无排名信息"

    data_lines = [f"最新价：{price}元，涨跌幅：{change_pct}%"]
    if industry:
        data_lines.append(f"所属行业：{industry}")
    if volume_yi:
        data_lines.append(f"成交额：{volume_yi}亿元")
    if turnover_rate:
        data_lines.append(f"换手率：{turnover_rate}%")
    if volume_ratio:
        data_lines.append(f"量比：{volume_ratio}")
    if pe and pe > 0:
        pe_line = f"市盈率(PE_TTM)：{pe:.1f}"
        if industry_pe:
            pe_vs = "低于" if pe < industry_pe else "高于"
            pe_line += f"（行业中位数：{industry_pe}，{pe_vs}行业）"
        data_lines.append(pe_line)
    if pb and pb > 0:
        pb_line = f"市净率(PB)：{pb:.2f}"
        if industry_pb:
            pb_vs = "低于" if pb < industry_pb else "高于"
            pb_line += f"（行业中位数：{industry_pb}，{pb_vs}行业）"
        data_lines.append(pb_line)
    if mkt_cap_yi and mkt_cap_yi > 0:
        data_lines.append(f"总市值：{mkt_cap_yi:.0f}亿元")
    if net_flow_wan is not None:
        flow_desc = f"+{net_flow_wan}" if net_flow_wan >= 0 else f"{net_flow_wan}"
        data_lines.append(f"今日主力净流入：{flow_desc}万元")

    data_block = "\n- ".join(data_lines)

    kline_block = ""
    if kline_summary:
        kline_block = f"""

## K线技术指标（精确计算值，请重点参考）
{kline_summary}
"""

    quant_block = ""
    if quant_score:
        quant_block = f"""

## 量化预评分（系统自动计算，仅供参考）
- 技术面分：{quant_score.get('技术面分', 'N/A')}/100
- 资金面分：{quant_score.get('资金面分', 'N/A')}/100
- 估值面分：{quant_score.get('估值面分', 'N/A')}/100
- 动量分：{quant_score.get('动量分', 'N/A')}/100
- 量化总分：{quant_score.get('量化总分', 'N/A')}/100 → {quant_score.get('量化信号', '')}
（注意：量化分仅基于数值计算，你需要结合基本面定性分析、题材逻辑、最新消息等综合判断）
"""

    return f"""请对以下A股标的进行深度分析和评分：

## 股票信息
- 股票：{stock_name}（{stock_code}）
- 今日排名：{rank_str}

## 实时行情数据
- {data_block}
{kline_block}{quant_block}
## 请从以下三个维度分析并评分（每项1-10分）：

### 1. 基本面（权重35%）
请搜索该公司最新信息，评估主营业务、近期业绩、盈利质量、估值合理性、财务风险。

### 2. 题材热度（权重35%）
请搜索最新新闻和政策，评估题材正宗程度、催化剂、市场关注度、想象空间。

### 3. 技术面/短线动能（权重30%）
基于上方精确技术指标数据分析均线系统、量价配合、MACD/RSI、关键位置、主力资金。

## 输出格式要求（严格按此格式）：

**综合评分：X.X/10**

| 维度 | 评分 | 关键依据 |
|------|------|----------|
| 基本面 | X/10 | 一句话总结（含具体数据） |
| 题材热度 | X/10 | 一句话总结（指出具体题材和催化剂） |
| 技术面 | X/10 | 一句话总结（引用具体技术指标） |

**核心逻辑：**（2-3句话，必须包含具体的数据或事实支撑）

**主要风险：**（2个风险点，具体化）

**短线建议：**（严格按系统提示中的判定标准，明确给出：强烈推荐/推荐/观望/回避）
**中期建议：**（1-3个月维度，严格按系统提示中的判定标准，明确给出：强烈推荐/推荐/观望/回避）
**建议仓位：**（轻仓试探/标准仓位/重仓参与）"""


SYSTEM_SUMMARY = (
    "你是一位资深A股投资策略师，正在为客户撰写每日精选股票报告。"
    "报告要求：专业但易懂，重点突出，结论明确。"
)


def build_summary_prompt(top_stocks_text: str, total_candidates: int) -> str:
    return f"""以下是今日从{total_candidates}只热门候选股中，经AI深度分析后筛选出的Top 10推荐标的及其分析摘要：

{top_stocks_text}

请撰写一份简洁的【今日Top10精选报告】，包含：

1. **市场概览**（2-3句话概括今日热门股票的整体特征和市场情绪）

2. **今日主线题材**（总结Top10中出现的主要投资主线，1-2个，每个主线点明代表个股）

3. **风险提醒**（整体市场风险或需要注意的共性风险）

【严格禁止】：
- 禁止输出任何表格（不要用 | 排名 | 股票 | 这种格式），下方已有详细卡片展示
- 禁止逐只罗列Top10股票，不要出现"1. xxx 2. xxx"这种逐只列举
- 只写市场概览、主线题材、风险提醒三部分即可，保持简洁
- 这是投资参考，不构成投资建议。请在报告末尾加上一句简短免责声明。"""
