"""Generate full research reports for each candidate and rank by match score."""

from __future__ import annotations

import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from core.ai_client import call_ai
from core.tushare_client import price_summary, to_ts_code
from top10.report_context import build_report_context
from top10.report_prompts import REPORT_SYSTEM, build_report_prompt
from top10.report_storage import save_top10_report


def _cleanup_report_text(text: str) -> str:
    fixed_lines = []
    for line in text.splitlines():
        if line.count("**") % 2 == 1:
            line = line.rstrip() + "**"
        fixed_lines.append(line)
    return "\n".join(fixed_lines).strip()


def _split_report_and_summary(markdown_text: str) -> tuple[str, str]:
    cleaned = markdown_text.strip()
    parts = cleaned.split("<<<REPORT_END>>>")
    if len(parts) >= 2:
        summary = parts[-1].strip()
        summary = re.sub(r"^\s*#\s*💡\s*核心摘要\s*", "", summary).strip()
        summary = re.sub(r"\s+", " ", summary)
        report_body = parts[0].strip()
        if summary and report_body:
            return summary, report_body
    return "", cleaned


def _parse_match_score(text: str) -> float:
    patterns = [
        r"综合匹配度[^0-9]{0,8}(\d+(?:\.\d+)?)\s*分",
        r"综合匹配度[^0-9]{0,8}(\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return 0.0


def _parse_subscore(text: str, label: str) -> float | None:
    escaped = re.escape(label)
    match = re.search(rf"{escaped}[^\n]*?(\d+(?:\.\d+)?)\s*分", text)
    if match:
        return float(match.group(1))
    return None


def _parse_rating(text: str) -> str:
    match = re.search(r"操作评级[：:]\s*\**\s*([^\n*]+)", text)
    if match:
        return match.group(1).strip()
    return ""


def _derive_advice(match_score: float) -> str:
    if match_score >= 80:
        return "强烈推荐"
    if match_score >= 65:
        return "推荐"
    if match_score >= 50:
        return "观望"
    return "回避"


def _safe_float(value):
    if value is None:
        return None
    try:
        number = float(value)
        return None if np.isnan(number) else number
    except (TypeError, ValueError):
        return None


def _compute_indicators(df: pd.DataFrame) -> dict:
    if df is None or df.empty or len(df) < 26:
        return {}

    close = df["收盘"].astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_14 = float((100 - 100 / (1 + rs)).iloc[-1])

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = float((ema12 - ema26).iloc[-1])
    dea = float((ema12 - ema26).ewm(span=9, adjust=False).mean().iloc[-1])
    hist = (dif - dea) * 2

    middle = close.rolling(20).mean()
    std = close.rolling(20).std()
    upper = float((middle + 2 * std).iloc[-1])
    mid = float(middle.iloc[-1])
    lower = float((middle - 2 * std).iloc[-1])
    width_pct = (upper - lower) / mid * 100 if mid else 0
    price = float(close.iloc[-1])

    if rsi_14 >= 70:
        rsi_signal = "超买"
    elif rsi_14 >= 55:
        rsi_signal = "中性偏强"
    elif rsi_14 >= 45:
        rsi_signal = "中性"
    elif rsi_14 >= 30:
        rsi_signal = "中性偏弱"
    else:
        rsi_signal = "超卖"

    prev_dif = float((ema12 - ema26).iloc[-2])
    prev_dea = float((ema12 - ema26).ewm(span=9, adjust=False).mean().iloc[-2])
    if prev_dif <= prev_dea and dif > dea:
        macd_signal = "金叉（DIF上穿DEA）"
    elif prev_dif >= prev_dea and dif < dea:
        macd_signal = "死叉（DIF下穿DEA）"
    elif dif > dea:
        macd_signal = "DIF>DEA多头"
    else:
        macd_signal = "DIF<DEA空头"

    band_width = upper - lower
    if band_width <= 0:
        bb_position = "数据异常"
    elif price > upper:
        bb_position = "上轨之上"
    elif price > upper - band_width * 0.1:
        bb_position = "上轨附近"
    elif price > mid + band_width * 0.05:
        bb_position = "中轨上方"
    elif price > mid - band_width * 0.05:
        bb_position = "中轨附近"
    elif price > lower + band_width * 0.1:
        bb_position = "中轨下方"
    elif price > lower:
        bb_position = "下轨附近"
    else:
        bb_position = "下轨之下"

    return {
        "rsi_14": round(rsi_14, 1),
        "rsi_signal": rsi_signal,
        "macd_dif": round(dif, 2),
        "macd_dea": round(dea, 2),
        "macd_hist": round(hist, 2),
        "macd_signal": macd_signal,
        "bb_upper": round(upper, 2),
        "bb_middle": round(mid, 2),
        "bb_lower": round(lower, 2),
        "bb_width_pct": round(width_pct, 1),
        "bb_position": bb_position,
    }


def _format_indicators_section(indicators: dict) -> str:
    if not indicators:
        return ""
    return (
        "## 技术指标\n"
        f"RSI(14): {indicators['rsi_14']}  信号: {indicators['rsi_signal']}\n"
        f"MACD: DIF={indicators['macd_dif']}  DEA={indicators['macd_dea']}  柱状={indicators['macd_hist']}  信号: {indicators['macd_signal']}\n"
        f"布林带(20,2): 上轨={indicators['bb_upper']}  中轨={indicators['bb_middle']}  下轨={indicators['bb_lower']}  带宽={indicators['bb_width_pct']}%  位置: {indicators['bb_position']}"
    )


def _build_single_report(client, cfg, row: pd.Series, model_name: str, username: str = "") -> dict:
    code = str(row.get("代码", ""))
    name = str(row.get("股票名称", ""))
    ts_code = to_ts_code(code)

    context, raw_data = build_report_context(ts_code)
    price_df = raw_data.get("_price_df", pd.DataFrame())
    price_snapshot = price_summary(price_df) if isinstance(price_df, pd.DataFrame) and not price_df.empty else "暂无K线数据"
    indicators = _compute_indicators(price_df)
    indicators_section = _format_indicators_section(indicators)

    user_prompt, system_prompt = build_report_prompt(name, code, context, price_snapshot, indicators_section)
    report_text, err = call_ai(
        client,
        cfg,
        user_prompt,
        system=system_prompt or REPORT_SYSTEM,
        max_tokens=12000,
        username=username,
    )
    if err:
        raise RuntimeError(err)

    cleaned_text = _cleanup_report_text(report_text)
    summary, report_body = _split_report_and_summary(cleaned_text)
    match_score = _parse_match_score(cleaned_text)
    fundamentals = _parse_subscore(cleaned_text, "基本面得分")
    catalyst = _parse_subscore(cleaned_text, "预期差与催化得分")
    capital = _parse_subscore(cleaned_text, "资金与身位得分")
    technical = _parse_subscore(cleaned_text, "技术面得分")
    rating = _parse_rating(cleaned_text)
    advice = _derive_advice(match_score)

    report_id = str(uuid.uuid4())
    markdown_path, report_url = save_top10_report(
        report_id=report_id,
        owner=f"top10:{username or 'system'}",
        stock_name=name,
        stock_code=ts_code,
        summary=summary or f"{name} 暂无摘要",
        markdown_text=cleaned_text,
    )

    theme_score = None
    if catalyst is not None or capital is not None:
        vals = [v for v in [catalyst, capital] if v is not None]
        if vals:
            theme_score = sum(vals) / len(vals)

    return {
        "代码": code,
        "股票名称": name,
        "最新价": _safe_float(row.get("最新价", 0)) or 0.0,
        "涨跌幅": _safe_float(row.get("涨跌幅", 0)) or 0.0,
        "行业": row.get("行业", "") or "",
        "综合匹配度": match_score,
        "综合评分": round(match_score / 10, 2),
        "基本面": round((fundamentals or 0) / 10, 1) if fundamentals is not None else None,
        "题材热度": round((theme_score or 0) / 10, 1) if theme_score is not None else None,
        "技术面": round((technical or 0) / 10, 1) if technical is not None else None,
        "短线建议": advice,
        "中期建议": advice,
        "操作评级": rating,
        "核心摘要": summary,
        "AI分析": report_body,
        "报告ID": report_id,
        "报告链接": report_url,
        "本地报告路径": markdown_path,
        "模型": model_name,
        "人气排名": row.get("人气排名"),
        "成交额排名": row.get("成交额排名"),
        "雪球排名": row.get("雪球排名"),
        "量化总分": row.get("量化总分"),
        "量化信号": row.get("量化信号", ""),
    }


def score_single_stock(client, cfg, row: pd.Series, model_name: str = "", username: str = "") -> dict:
    return _build_single_report(client, cfg, row, model_name, username)


def score_all(
    client,
    cfg,
    df: pd.DataFrame,
    model_name: str = "",
    progress_callback=None,
    max_workers: int = 3,
    username: str = "",
) -> pd.DataFrame:
    results = []
    total = len(df)
    completed_count = 0

    def _score_row(idx_row):
        _, row = idx_row
        return score_single_stock(client, cfg, row, model_name, username)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_score_row, (idx, row)): row["股票名称"]
            for idx, row in df.iterrows()
        }
        for future in as_completed(futures):
            name = futures[future]
            completed_count += 1
            try:
                result = future.result()
                results.append(result)
                if progress_callback:
                    progress_callback(
                        completed_count,
                        total,
                        f"✅ {name} → 模式匹配度 {result['综合匹配度']:.1f}分",
                    )
            except Exception as exc:
                if progress_callback:
                    progress_callback(completed_count, total, f"❌ {name} 研报生成失败：{exc}")

    if not results:
        return pd.DataFrame()

    result_df = pd.DataFrame(results)
    result_df = result_df.sort_values("综合匹配度", ascending=False).reset_index(drop=True)
    result_df.index = result_df.index + 1
    result_df.index.name = "推荐排名"
    return result_df


def get_top_n(scored_df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    if scored_df.empty:
        return scored_df
    return scored_df.head(n)
