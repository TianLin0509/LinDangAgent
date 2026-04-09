"""Generate full research reports for each candidate and rank by match score."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import numpy as np
import pandas as pd

from ai.client import call_ai
from data.tushare_client import price_summary, to_ts_code
from Stock_top10.top10.report_context import build_report_context
from Stock_top10.top10.report_prompts import REPORT_SYSTEM, build_report_prompt
from Stock_top10.top10.report_storage import save_top10_report

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# 增量保存 — 每完成一只股票立即持久化，支持断点续跑
# ══════════════════════════════════════════════════════════════════════════════

_incremental_lock = threading.Lock()
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")


def _incremental_path(model_name: str) -> str:
    return os.path.join(_CACHE_DIR, f"{date.today().isoformat()}_{model_name}_incremental.json")


def save_incremental_result(result: dict, model_name: str):
    """追加一只股票的结果到增量文件。线程安全。"""
    path = _incremental_path(model_name)
    with _incremental_lock:
        existing = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, Exception):
                existing = []
        existing.append(result)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2, default=str)


def load_incremental_results(model_name: str) -> list[dict]:
    """加载当日已保存的增量结果。"""
    path = _incremental_path(model_name)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("[scorer] loaded %d incremental results from %s", len(data), path)
        return data
    except Exception:
        return []


def clear_incremental_results(model_name: str):
    """全部完成后删除增量文件。"""
    path = _incremental_path(model_name)
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info("[scorer] cleared incremental file: %s", path)
    except Exception:
        pass


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
        summary = re.sub(r"^\s*#\s*(?:💡\s*)?(?:核心摘要|战役总结)\s*", "", summary).strip()
        summary = re.sub(r"\s+", " ", summary)
        report_body = parts[0].strip()
        if summary and report_body:
            return summary, report_body
    return "", cleaned


def _parse_match_score(text: str) -> float:
    """提取综合评分，兼容新旧两种格式：
    旧格式：综合匹配度 85 分（0-100制）
    新格式（林彪版）：<<<SCORES>>> 块中的综合加权，或正文中 X.X分(评级)（1-10制，×10 转换）
    """
    # 1. 尝试从 <<<SCORES>>> 块中提取
    scores_block = re.search(r"<<<SCORES>>>(.*?)<<<END_SCORES>>>", text, re.DOTALL)
    if scores_block:
        block = scores_block.group(1)
        dims = {}
        for dim in ["基本面", "预期差", "资金面", "技术面"]:
            m = re.search(rf"{dim}\s*[:：]\s*(\d+(?:\.\d+)?)\s*(?:/\s*(100|10))?(?:\s|$)", block)
            if m:
                val = float(m.group(1))
                scale = m.group(2)
                if scale == "10":
                    dims[dim] = val * 10
                elif scale is None and val <= 10:
                    dims[dim] = val * 10  # 纯数字≤10，推测10分制
                else:
                    dims[dim] = val
        if dims:
            weights = {"基本面": 0.15, "预期差": 0.35, "资金面": 0.30, "技术面": 0.20}
            total_w = sum(weights.get(d, 0) for d in dims)
            if total_w > 0:
                weighted = sum(dims[d] * weights.get(d, 0) for d in dims) / total_w
                return round(weighted, 1)  # 已经是百分制

    # 2. 尝试从正文中提取"X.X分(评级)"（新格式）
    m = re.search(r"(\d+(?:\.\d+)?)\s*分\s*[（(]\s*(?:总攻信号|侦察待命|按兵不动|全线撤退)", text)
    if m:
        score = float(m.group(1))
        if score <= 10:
            return round(score * 10, 1)
        return score

    # 3. 旧格式兜底
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
    """提取子维度评分，兼容新旧格式"""
    # 新格式：从 <<<SCORES>>> 块中提取 "基本面: X/10"
    scores_block = re.search(r"<<<SCORES>>>(.*?)<<<END_SCORES>>>", text, re.DOTALL)
    if scores_block:
        block = scores_block.group(1)
        # 映射新旧标签名
        label_map = {
            "基本面得分": "基本面",
            "预期差与催化得分": "预期差",
            "资金与身位得分": "资金面",
            "技术面得分": "技术面",
        }
        new_label = label_map.get(label, label)
        m = re.search(rf"{new_label}\s*[:：]\s*(\d+(?:\.\d+)?)\s*(?:/\s*(100|10))?(?:\s|$)", block)
        if m:
            val = float(m.group(1))
            scale = m.group(2)
            if scale == "10":
                return val * 10
            elif scale is None and val <= 10:
                return val * 10
            return val

    # 旧格式兜底
    escaped = re.escape(label)
    match = re.search(rf"{escaped}[^\n]*?(\d+(?:\.\d+)?)\s*分", text)
    if match:
        return float(match.group(1))
    return None


def _parse_rating(text: str) -> str:
    match = re.search(r"操作评级[：:]\s*\**\s*([^\n*]+)", text)
    if match:
        return match.group(1).strip()
    # 兜底：尝试从战役总结中提取
    m2 = re.search(r"(\d+(?:\.\d+)?)\s*分\s*[，,]?\s*(?:评级为|获|下达)?\s*(总攻信号|侦察待命|按兵不动|全线撤退)", text)
    if m2:
        return f"{m2.group(2)}[综合{m2.group(1)}]"
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
        "综合评分": round(match_score, 1),
        "基本面得分": round(fundamentals, 1) if fundamentals is not None else None,
        "预期差与催化得分": round(catalyst, 1) if catalyst is not None else None,
        "资金与身位得分": round(capital, 1) if capital is not None else None,
        "技术面得分": round(technical, 1) if technical is not None else None,
        "题材热度": round((theme_score or 0), 1) if theme_score is not None else None,
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


def scout_all(
    client,
    cfg,
    df: pd.DataFrame,
    progress_callback=None,
    max_workers: int = 3,
    username: str = "",
    batch_size: int = 5,
) -> pd.DataFrame:
    """Phase 1: 侦察兵快速评估（v3.0：批量模式，每批5只，减少API调用10倍）"""
    from Stock_top10.top10.scout_prompt import SCOUT_SYSTEM, build_scout_prompt

    results = []
    total = len(df)
    completed = 0

    def _parse_batch_response(text: str, batch_codes: list, batch_names: list) -> list:
        """解析批量Scout响应，提取每只股票的评分"""
        parsed = []
        # 按股票名/代码分割响应
        lines = text.strip().splitlines()
        current = {"代码": "", "股票名称": "", "scout_score": 50.0, "scout_logic": "", "scout_risk": ""}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 检测是否是新股票的开始
            for i, (code, name) in enumerate(zip(batch_codes, batch_names)):
                if code in line or name in line:
                    if current["代码"]:
                        parsed.append(current.copy())
                    current = {"代码": code, "股票名称": name,
                               "scout_score": 50.0, "scout_logic": "", "scout_risk": ""}
                    break
            # 解析评分
            m = re.search(r"评分[：:]\s*(\d+(?:\.\d+)?)\s*/\s*(100|10)", line)
            if m:
                score = float(m.group(1))
                if int(m.group(2)) == 10:
                    score *= 10
                current["scout_score"] = score
            m_logic = re.search(r"主攻逻辑[：:]\s*(.+)", line)
            if m_logic:
                current["scout_logic"] = m_logic.group(1).strip()[:40]
            m_risk = re.search(r"风险点[：:]\s*(.+)", line)
            if m_risk:
                current["scout_risk"] = m_risk.group(1).strip()[:40]
        if current["代码"]:
            parsed.append(current)
        return parsed

    def _scout_batch(batch_rows: list):
        """批量评估一组股票"""
        prompts = []
        codes = []
        names = []
        for _, row in batch_rows:
            prompts.append(build_scout_prompt(row))
            codes.append(row.get("代码", ""))
            names.append(row.get("股票名称", ""))

        # ★ 失败默认分=30（低于Scout门槛60，确保不会混入Top20）
        FAIL_SCORE = 30.0

        if len(prompts) == 1:
            # 单只模式（含1次重试）
            for attempt in range(2):
                text, err = call_ai(client, cfg, prompts[0], system=SCOUT_SYSTEM,
                                    max_tokens=100, username=username)
                if not err and text:
                    parsed = _parse_batch_response(text, codes, names)
                    if parsed:
                        return parsed
                if attempt == 0:
                    logger.debug("[scout] %s 首次失败，重试...", names[0])
            return [{"代码": codes[0], "股票名称": names[0],
                     "scout_score": FAIL_SCORE, "scout_logic": "", "scout_risk": "调用失败"}]

        # 批量模式：合并多只到一个 prompt
        combined = f"请逐只评估以下 {len(prompts)} 只股票，每只严格按三行格式输出（评分/主攻逻辑/风险点），股票之间用空行分隔。\n\n"
        for i, (p, name, code) in enumerate(zip(prompts, names, codes)):
            combined += f"--- 第{i+1}只：{name}（{code}）---\n{p}\n\n"

        text, err = call_ai(client, cfg, combined, system=SCOUT_SYSTEM,
                            max_tokens=100 * len(prompts), username=username)
        if not err and text:
            parsed = _parse_batch_response(text, codes, names)
            if len(parsed) >= len(codes) * 0.5:  # 至少解析出一半
                return parsed

        # 批量失败则逐只回退（每只含1次重试）
        fallback_results = []
        for prompt, code, name in zip(prompts, codes, names):
            r = {"代码": code, "股票名称": name,
                 "scout_score": FAIL_SCORE, "scout_logic": "", "scout_risk": "调用失败"}
            for attempt in range(2):
                text, err = call_ai(client, cfg, prompt, system=SCOUT_SYSTEM,
                                    max_tokens=100, username=username)
                if not err and text:
                    parsed = _parse_batch_response(text, [code], [name])
                    if parsed:
                        r = parsed[0]
                        break
                if attempt == 0:
                    logger.debug("[scout] %s 逐只回退首次失败，重试...", name)
            fallback_results.append(r)
        return fallback_results

    # 分批
    rows_list = list(df.iterrows())
    batches = [rows_list[i:i+batch_size] for i in range(0, len(rows_list), batch_size)]

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_scout_batch, batch): batch for batch in batches}
        for future in as_completed(futures):
            batch = futures[future]
            batch_names = [r.get("股票名称", "?") for _, r in batch]
            try:
                batch_results = future.result()
                results.extend(batch_results)
                completed += len(batch)
                if progress_callback:
                    progress_callback(completed, total,
                                      f"🔍 批量侦察完成 {len(batch)} 只（{', '.join(batch_names[:3])}...）")
            except Exception as exc:
                completed += len(batch)
                if progress_callback:
                    progress_callback(completed, total, f"❌ 批量侦察失败：{exc}")

    if not results:
        return df

    scout_df = pd.DataFrame(results)
    merged = df.merge(scout_df[["代码", "scout_score", "scout_logic", "scout_risk"]], on="代码", how="left")
    merged["scout_score"] = merged["scout_score"].fillna(30.0)  # 失败的不进Top20
    return merged.sort_values("scout_score", ascending=False).reset_index(drop=True)


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
                save_incremental_result(result, model_name)
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


# ══════════════════════════════════════════════════════════════════════════════
# 指挥部模式 — 用四野指挥部替代单模型分析（东野实战风格）
# ══════════════════════════════════════════════════════════════════════════════

def _adapt_war_room_to_top10(wr_result, row: pd.Series) -> dict:
    """将 WarRoomResult 转换为 Top10 scorer 兼容的 dict 格式。"""
    final_scores = wr_result.final_scores or {}
    match_score = final_scores.get("综合加权", 50.0)
    fundamentals = final_scores.get("基本面")
    catalyst = final_scores.get("预期差")
    capital = final_scores.get("资金面")
    technical = final_scores.get("技术面")

    theme_score = None
    vals = [v for v in [catalyst, capital] if v is not None]
    if vals:
        theme_score = sum(vals) / len(vals)

    return {
        "代码": str(row.get("代码", "")),
        "股票名称": wr_result.stock_name or str(row.get("股票名称", "")),
        "最新价": _safe_float(row.get("最新价", 0)) or 0.0,
        "涨跌幅": _safe_float(row.get("涨跌幅", 0)) or 0.0,
        "行业": row.get("行业", "") or "",
        "综合匹配度": match_score,
        "综合评分": round(match_score, 1),
        "基本面得分": round(fundamentals, 1) if fundamentals is not None else None,
        "预期差与催化得分": round(catalyst, 1) if catalyst is not None else None,
        "资金与身位得分": round(capital, 1) if capital is not None else None,
        "技术面得分": round(technical, 1) if technical is not None else None,
        "题材热度": round(theme_score, 1) if theme_score is not None else None,
        "短线建议": _derive_advice(match_score),
        "中期建议": _derive_advice(match_score),
        "操作评级": final_scores.get("_rating", "按兵不动"),
        "核心摘要": wr_result.final_summary or "指挥部分析完成",
        "AI分析": wr_result.combined_markdown,
        "报告ID": wr_result.report_id,
        "报告链接": "",
        "本地报告路径": "",
        "模型": "四野指挥部",
        "人气排名": row.get("人气排名"),
        "成交额排名": row.get("成交额排名"),
        "雪球排名": row.get("雪球排名"),
        "量化总分": row.get("量化总分"),
        "量化信号": row.get("量化信号", ""),
    }


def score_all_war_room(
    df: pd.DataFrame,
    preset: str = "gemini",
    progress_callback=None,
    max_workers: int = 1,
    username: str = "",
) -> pd.DataFrame:
    """用四野指挥部替代单模型分析 — 东野实战风格。

    每只股票经过完整的 Phase 0-5 指挥部流程：
    侦察科采集 → 三将领并行 → 追加侦察 → 刘亚楼汇总 → 林彪裁决

    max_workers 建议设为 1-2（指挥部内部已有三将领并行）。
    """
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
    from services.war_room import run_war_room

    results = []
    total = len(df)
    completed_count = 0

    def _analyze_one(idx_row):
        _, row = idx_row
        stock_name = str(row.get("股票名称", ""))
        wr_result = run_war_room(
            stock_name=stock_name,
            username=username,
            preset=preset,
            skip_extra_recon=True,  # 批量模式跳过追加侦察，省1-2分钟/只
        )
        return _adapt_war_room_to_top10(wr_result, row)

    # 指挥部内部已有三将领并行，外层并发不宜过高
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_analyze_one, (idx, row)): str(row.get("股票名称", ""))
            for idx, row in df.iterrows()
        }
        for future in as_completed(futures):
            name = futures[future]
            completed_count += 1
            try:
                result = future.result()
                results.append(result)
                save_incremental_result(result, f"war_room_{preset}")
                if progress_callback:
                    score = result.get("综合匹配度", 0)
                    rating = result.get("操作评级", "")
                    progress_callback(
                        completed_count, total,
                        f"✅ {name} → 综合 {score:.1f}分 {rating}",
                    )
            except Exception as exc:
                logger.error("[war_room_scorer] %s failed: %r", name, exc)
                if progress_callback:
                    progress_callback(completed_count, total, f"❌ {name} 指挥部分析失败：{exc}")

    if not results:
        return pd.DataFrame()

    result_df = pd.DataFrame(results)
    result_df = result_df.sort_values("综合匹配度", ascending=False).reset_index(drop=True)
    result_df.index = result_df.index + 1
    result_df.index.name = "推荐排名"
    return result_df
