"""Query the prebuilt K-line sample dataset by stock name."""

from __future__ import annotations

from analysis.kline_research import (
    DEFAULT_FEATURE_COLUMNS,
    ResearchConfig,
    build_stock_research_snapshot,
    summarize_rule_patterns,
)
from data.history_dataset_builder import (
    build_and_save_research_dataset,
    load_research_dataset,
    load_research_metadata,
)
from data.similarity import find_similar
from data.tushare_client import get_price_df, resolve_stock


TAG_LABELS = {
    "trend_tag": {
        "trend_up": "趋势偏强",
        "trend_down": "趋势偏弱",
        "trend_flat": "趋势走平",
    },
    "momentum_tag": {
        "momentum_up": "短线动能上行",
        "momentum_down": "短线动能回落",
        "momentum_flat": "短线动能走平",
    },
    "volume_tag": {
        "volume_expand": "量能放大",
        "volume_contract": "量能收缩",
        "volume_neutral": "量能平稳",
    },
    "rsi_tag": {
        "rsi_hot": "RSI偏热",
        "rsi_cold": "RSI偏冷",
        "rsi_mid": "RSI中性",
    },
    "candle_tag": {
        "lower_shadow": "下影较长，低位承接较强",
        "upper_shadow": "上影较长，上方抛压明显",
        "wide_bull": "实体偏强，多头主导",
        "wide_bear": "实体偏弱，空头主导",
        "neutral_body": "实体中性，多空暂时均衡",
    },
    "breakout_tag": {
        "near_high": "位置靠近阶段高位",
        "near_low": "位置靠近阶段低位",
        "mid_range": "位置处于区间中部",
    },
}

TAG_TITLES = {
    "trend_tag": "趋势状态",
    "momentum_tag": "动量状态",
    "volume_tag": "量能状态",
    "rsi_tag": "RSI状态",
    "candle_tag": "当日K线",
    "breakout_tag": "所处位置",
}


def ensure_research_dataset(
    *,
    refresh_history: bool = False,
    full_rebuild_history: bool = False,
    config: ResearchConfig | None = None,
) -> dict:
    dataset = load_research_dataset()
    if dataset.empty or refresh_history:
        stats = build_and_save_research_dataset(
            config=config,
            refresh_history=refresh_history,
            full_rebuild_history=full_rebuild_history,
        )
        return {
            "dataset_ready": True,
            "metadata": stats.__dict__,
        }

    return {
        "dataset_ready": True,
        "metadata": load_research_metadata(),
    }


def query_stock_pattern(stock_query: str, *, horizon: int = 5, min_rule_samples: int = 30) -> dict:
    ts_code, resolved_name, err = resolve_stock(stock_query)
    if not ts_code:
        raise ValueError(err or f"unable to resolve stock: {stock_query}")

    dataset = load_research_dataset()
    if dataset.empty:
        raise ValueError("research dataset is missing, run ensure_research_dataset first")

    snapshot = build_stock_research_snapshot(
        dataset,
        stock_code=ts_code,
        horizon=horizon,
        feature_names=DEFAULT_FEATURE_COLUMNS,
        min_rule_samples=min_rule_samples,
    )
    stock_rows = dataset[dataset["ts_code"] == ts_code].sort_values("trade_date")
    latest_row = stock_rows.tail(1).iloc[0]
    pattern_key = snapshot["pattern_key"]
    same_pattern = summarize_rule_patterns(dataset, horizon=horizon, min_samples=min_rule_samples)
    same_pattern = same_pattern[same_pattern["pattern_key"] == pattern_key].head(1)

    pattern_details = _build_pattern_details(latest_row)
    snapshot["pattern_details"] = pattern_details
    snapshot["pattern_summary"] = _pattern_summary_text(pattern_details)

    return {
        "stock_name": resolved_name,
        "ts_code": ts_code,
        "trade_date": snapshot["trade_date"],
        "pattern_key": pattern_key,
        "pattern_summary": snapshot["pattern_summary"],
        "up_probability": snapshot["up_probability"],
        "pattern_stats": same_pattern.iloc[0].to_dict() if not same_pattern.empty else snapshot.get("pattern_stats"),
        "metadata": load_research_metadata(),
    }


def build_kline_prediction_report(
    stock_query: str,
    *,
    horizon: int = 5,
    min_rule_samples: int = 30,
    similar_top_n: int = 5,
    k_days: int = 7,
) -> dict:
    ts_code, resolved_name, err = resolve_stock(stock_query)
    if not ts_code:
        raise ValueError(err or f"unable to resolve stock: {stock_query}")

    dataset = load_research_dataset()
    if dataset.empty:
        raise ValueError("research dataset is missing, run ensure_research_dataset first")

    stock_rows = dataset[dataset["ts_code"] == ts_code].sort_values("trade_date")
    if stock_rows.empty:
        raise ValueError(f"no research samples found for {ts_code}")

    snapshot = build_stock_research_snapshot(
        dataset,
        stock_code=ts_code,
        horizon=horizon,
        feature_names=DEFAULT_FEATURE_COLUMNS,
        min_rule_samples=min_rule_samples,
    )
    latest_row = stock_rows.tail(1).iloc[0]
    pattern_stats = snapshot.get("pattern_stats")
    pattern_details = _build_pattern_details(latest_row)
    snapshot["pattern_details"] = pattern_details
    snapshot["pattern_summary"] = _pattern_summary_text(pattern_details)

    price_df, price_err = get_price_df(ts_code, days=160)
    similar_cases = []
    if price_df is not None and not price_df.empty:
        raw_cases = find_similar(
            price_df,
            k_days=k_days,
            top_n=similar_top_n,
            exclude_code=ts_code,
            exclude_recent_days=60,
        )
        for case in raw_cases:
            similar_cases.append(
                {
                    "stock_name": case.get("stock_name") or case.get("ts_code"),
                    "ts_code": case.get("ts_code"),
                    "similarity": case.get("similarity"),
                    "kline_similarity": case.get("kline_similarity"),
                    "vol_similarity": case.get("vol_similarity"),
                    "match_start_date": _fmt_match_date(case.get("match_start_date")),
                    "match_end_date": _fmt_match_date(case.get("match_end_date")),
                    "subsequent_return": case.get("subsequent_return"),
                    "max_drawdown": case.get("max_drawdown"),
                    "max_gain": case.get("max_gain"),
                }
            )

    metadata = load_research_metadata()
    reasoning = _build_reasoning_lines(
        snapshot=snapshot,
        pattern_stats=pattern_stats,
        similar_cases=similar_cases,
        horizon=horizon,
    )
    markdown = _build_markdown_report(
        stock_name=resolved_name,
        ts_code=ts_code,
        horizon=horizon,
        snapshot=snapshot,
        pattern_stats=pattern_stats,
        similar_cases=similar_cases,
        metadata=metadata,
        price_error=price_err,
        reasoning=reasoning,
    )
    summary = _build_summary_text(
        stock_name=resolved_name,
        ts_code=ts_code,
        snapshot=snapshot,
        pattern_stats=pattern_stats,
        similar_cases=similar_cases,
        horizon=horizon,
    )

    return {
        "stock_name": resolved_name,
        "ts_code": ts_code,
        "summary": summary,
        "markdown": markdown,
        "snapshot": snapshot,
        "pattern_stats": pattern_stats,
        "similar_cases": similar_cases,
        "reasoning": reasoning,
        "metadata": metadata,
    }


def _build_summary_text(
    *,
    stock_name: str,
    ts_code: str,
    snapshot: dict,
    pattern_stats: dict | None,
    similar_cases: list[dict],
    horizon: int,
) -> str:
    prob = snapshot["up_probability"]
    sample_count = int(pattern_stats["sample_count"]) if pattern_stats else 0
    avg_ret = f"{pattern_stats['avg_return'] * 100:.2f}%" if pattern_stats else "暂无"
    pattern_text = snapshot.get("pattern_summary") or "当前形态暂无文字解读"

    if similar_cases:
        top_case = similar_cases[0]
        top_case_text = (
            f"最相似案例是{top_case['stock_name']}，后{horizon}日收益"
            f"{_fmt_percent_case(top_case.get('subsequent_return'))}"
        )
    else:
        top_case_text = "暂未找到足够强的历史相似案例"

    return (
        f"{stock_name}({ts_code})未来{horizon}个交易日上涨概率约{prob:.2f}%。"
        f"\n当前形态：{pattern_text}。"
        f"\n历史同类样本{sample_count}个，平均收益{avg_ret}。"
        f"\n{top_case_text}。"
    )


def _build_markdown_report(
    *,
    stock_name: str,
    ts_code: str,
    horizon: int,
    snapshot: dict,
    pattern_stats: dict | None,
    similar_cases: list[dict],
    metadata: dict,
    price_error: str | None,
    reasoning: list[str],
) -> str:
    lines: list[str] = [
        "# K线形态预测报告",
        "",
        "## 标的概览",
        f"- 股票：{stock_name}（{ts_code}）",
        f"- 最新样本日期：{_fmt_match_date(snapshot['trade_date'])}",
        f"- 预测窗口：未来 {horizon} 个交易日",
        f"- 上涨概率：{snapshot['up_probability']:.2f}%",
        f"- 形态解读：{snapshot['pattern_summary']}",
        "",
        "## 当前形态拆解",
    ]

    for item in snapshot["pattern_details"]:
        lines.append(f"- {item['title']}：{item['label']}")

    lines.extend(["", "## 历史同类形态统计"])
    if pattern_stats:
        lines.extend(
            [
                f"- 历史同类样本数：{int(pattern_stats['sample_count'])}",
                f"- 历史上涨概率：{pattern_stats['up_prob']:.2f}%",
                f"- 历史平均收益：{pattern_stats['avg_return'] * 100:.2f}%",
                f"- 历史中位收益：{pattern_stats['median_return'] * 100:.2f}%",
                f"- 历史平均最大回撤：{pattern_stats['avg_drawdown'] * 100:.2f}%",
                f"- 历史平均最大上冲：{pattern_stats['avg_max_up'] * 100:.2f}%",
            ]
        )
    else:
        lines.append("- 当前形态的历史样本仍偏少，暂时无法给出稳定统计。")

    lines.extend(["", "## 历史相似案例"])
    if similar_cases:
        for idx, case in enumerate(similar_cases, start=1):
            lines.append(
                f"{idx}. {case['stock_name']}（{case['ts_code']}），相似度 {case['similarity']}，"
                f"匹配区间 {case['match_start_date']} 至 {case['match_end_date']}，"
                f"后{horizon}日收益 {_fmt_percent_case(case.get('subsequent_return'))}，"
                f"最大回撤 {_fmt_percent_case(case.get('max_drawdown'))}，"
                f"最大上冲 {_fmt_percent_case(case.get('max_gain'))}。"
            )
    else:
        lines.append("- 暂未找到足够强的历史相似案例。")
    if price_error:
        lines.append(f"- 相似案例检索提示：{price_error}")

    lines.extend(["", "## 推导过程"])
    for line in reasoning:
        lines.append(f"- {line}")

    lines.extend(
        [
            "",
            "## 样本库信息",
            f"- 样本库构建时间：{metadata.get('built_at', 'N/A')}",
            f"- 历史覆盖区间：{metadata.get('history_start', 'N/A')} 至 {metadata.get('history_end', 'N/A')}",
            f"- 历史股票数：{metadata.get('history_stocks', 'N/A')}",
            f"- 研究样本数：{metadata.get('sample_rows', 'N/A')}",
        ]
    )
    return "\n".join(lines)


def _build_reasoning_lines(
    *,
    snapshot: dict,
    pattern_stats: dict | None,
    similar_cases: list[dict],
    horizon: int,
) -> list[str]:
    lines = [
        f"先把当前走势归纳为“{snapshot['pattern_summary']}”这类局部市场状态。",
    ]
    if pattern_stats:
        lines.append(
            f"在历史样本中找到 {int(pattern_stats['sample_count'])} 个同类状态，"
            f"统计得出未来{horizon}日上涨概率为 {pattern_stats['up_prob']:.2f}%。"
        )
    else:
        lines.append("当前形态的历史同类样本不足，因此规则统计的置信度相对有限。")

    if similar_cases:
        top_case = similar_cases[0]
        lines.append(
            f"进一步检索到最相似的案例是 {top_case['stock_name']}（{top_case['ts_code']}），"
            f"其后{horizon}日收益为 {_fmt_percent_case(top_case.get('subsequent_return'))}。"
        )
        positive_cases = sum(1 for item in similar_cases if (item.get("subsequent_return") or 0) > 0)
        lines.append(
            f"前 {len(similar_cases)} 个相似案例中，有 {positive_cases} 个在后续阶段取得正收益。"
        )
    else:
        lines.append("本次未找到足够强的相似案例，因此主要依赖规则统计和概率模型判断。")

    lines.append(
        f"最后再结合近期K线、量价、RSI、MACD、均线偏离等特征，给出未来{horizon}日上涨概率。"
    )
    return lines


def _build_pattern_details(latest_row) -> list[dict]:
    details = []
    for key in TAG_TITLES:
        raw_value = latest_row.get(key, "N/A")
        label = TAG_LABELS.get(key, {}).get(raw_value, raw_value)
        details.append(
            {
                "key": key,
                "raw": raw_value,
                "title": TAG_TITLES[key],
                "label": label,
            }
        )
    return details


def _pattern_summary_text(pattern_details: list[dict]) -> str:
    return "，".join(item["label"] for item in pattern_details)


def _fmt_match_date(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if len(text) >= 10 and "-" in text:
        return text[:10]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _fmt_percent_case(value) -> str:
    if value in (None, ""):
        return "暂无"
    try:
        return f"{float(value):.2f}%"
    except Exception:
        return str(value)
