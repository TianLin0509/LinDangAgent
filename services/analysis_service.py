"""Reusable stock analysis service."""

from __future__ import annotations

import logging
import queue
import re
import threading
import time as _time
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from ai.client import call_ai_stream, get_ai_client
from ai.prompts_report import build_report_prompt
from data.indicators import compute_indicators, format_indicators_section
from data.report_data import build_report_context
from data.tushare_client import get_price_df, price_summary, resolve_stock, to_code6

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]
StreamCallback = Callable[[str], None]
StatusCallback = Callable[[str], None]

DEFAULT_WECHAT_MODEL = "🟡 豆包 · Seed 2.0 Lite"
SUMMARY_FALLBACK_TEXT = "⚠️ 摘要生成超时或失败，请直接点击下方链接查看完整深度研报。"


@dataclass
class ComprehensiveAnalysisResult:
    full_report: str
    summary: str
    scores: dict | None
    context: dict
    raw_data: dict
    stock_capital: str
    stock_northbound: str
    stock_margin: str


@dataclass
class GeneratedReportBundle:
    stock_name: str
    stock_code: str
    summary: str
    full_report: str
    combined_markdown: str
    scores: dict | None = None


def _cleanup_report_text(text: str) -> str:
    text = re.sub(r"<<<SCORES>>>.*?<<<END_SCORES>>>", "", text, flags=re.DOTALL)
    text = text.replace("<<<SCORES>>>", "").replace("<<<END_SCORES>>>", "")

    fixed_lines = []
    for line in text.splitlines():
        if line.count("**") % 2 == 1:
            line = line.rstrip() + "**"
        fixed_lines.append(line)

    return "\n".join(fixed_lines).strip()


# 权重配置（代码计算，不依赖 LLM 算数）
SCORE_WEIGHTS = {
    "基本面": 0.15,
    "预期差": 0.35,
    "资金面": 0.30,
    "技术面": 0.20,
}


def parse_scores(text: str) -> dict | None:
    """从 <<<SCORES>>>...<<<END_SCORES>>> 块提取评分并在代码中加权计算。

    如果标记块缺失（Gemini 等模型不遵守格式），回退到全文扫描。
    """
    match = re.search(r"<<<SCORES>>>(.*?)<<<END_SCORES>>>", text, re.DOTALL)
    block = match.group(1) if match else text  # 回退到全文扫描
    scores: dict[str, float] = {}
    flags: dict[str, str] = {}

    for line in block.strip().splitlines():
        line = line.strip()
        # 清除 markdown 粗体/斜体标记（Codex 等模型常用）
        line = re.sub(r"\*+", "", line)
        if not line or line == "---":
            continue
        # 解析三种格式：
        # "基本面: 75/100"（标准百分制）
        # "基本面: 7/10"（旧10分制）
        # "基本面: 75"（纯数字，视为百分制）
        parsed = re.match(r"(.+?)[:：]\s*(-?\d+(?:\.\d+)?)\s*(?:分)?\s*(?:/\s*(100|10))?$", line)
        if parsed:
            key = parsed.group(1).strip()
            val = float(parsed.group(2))
            scale = parsed.group(3)
            if scale == "10":
                val = val * 10  # 旧 10 分制 → 百分制
            elif scale is None and val <= 10:
                val = val * 10  # 纯数字≤10，推测为10分制
            # 百分制钳位：防止 AI 输出超范围评分
            val = max(0.0, min(100.0, val))
            scores[key] = val
            continue
        # 解析 "S级豁免: 是/否" 和 "致命缺陷: 有/无" 格式
        flag_parsed = re.match(r"(.+?)[:：]\s*(.+)", line)
        if flag_parsed:
            flags[flag_parsed.group(1).strip()] = flag_parsed.group(2).strip()

    if not scores:
        return None

    # 代码计算加权综合分
    weighted_sum = 0.0
    total_weight = 0.0
    for dim, weight in SCORE_WEIGHTS.items():
        if dim in scores:
            weighted_sum += scores[dim] * weight
            total_weight += weight

    if total_weight > 0:
        scores["综合加权"] = round(weighted_sum / total_weight, 1)

    # S级豁免与致命缺陷标记（兼容旧格式）
    scores["_s_exempt"] = flags.get("S级豁免", "否") in ("是", "yes", "Yes")
    scores["_has_fatal"] = flags.get("致命缺陷", "无") in ("有", "yes", "Yes")

    # v3.0 双轴输出 + AI给出的操作评级
    if "操作评级" in flags:
        scores["_ai_rating"] = flags["操作评级"]
    # 立场（将领报告）
    if "立场" in flags:
        scores["_stance"] = flags["立场"]

    return scores


def apply_bucket_correction(scores: dict) -> dict:
    """木桶效应修正 + 熔断（百分制：基本面≤20且无S级豁免则触发熔断）。"""
    dims = ["基本面", "预期差", "技术面", "资金面"]
    dim_scores = [scores.get(dim, 50) for dim in dims]
    min_score = min(dim_scores)

    s_exempt = scores.get("_s_exempt", False)

    if min_score <= 20 and not s_exempt:
        if scores.get("基本面", 50) <= 20:
            scores["综合加权"] = min(scores.get("综合加权", 0), 30.0)
            scores["_fatal_flaw"] = "基本面评分≤20且无S级豁免，触发熔断"
        else:
            scores["综合加权"] = min(scores.get("综合加权", 0), 50.0)
            weakest = [dim for dim in dims if scores.get(dim, 50) <= 20]
            scores["_fatal_flaw"] = f"{'、'.join(weakest)}评分≤20，触发木桶修正"
        scores["_bucket_corrected"] = True
    else:
        scores["_bucket_corrected"] = False

    # 操作评级（v3.0：优先使用AI基于双轴给出的评级，否则回退到综合分判定）
    ai_rating = scores.get("_ai_rating", "")
    if ai_rating and ai_rating in ("总攻信号", "侦察待命", "按兵不动", "全线撤退"):
        scores["_rating"] = ai_rating
    else:
        # 尝试双轴判定
        attract = scores.get("机会吸引力", 0)
        confidence = scores.get("逻辑置信度", 0)
        all_dims_above_60 = all(scores.get(d, 0) >= 60 for d in dims)
        all_dims_above_50 = all(scores.get(d, 0) >= 50 for d in dims)
        all_dims_above_40 = all(scores.get(d, 0) >= 40 for d in dims)

        if attract >= 85 and confidence >= 80 and all_dims_above_60:
            scores["_rating"] = "总攻信号"
        elif attract >= 70 and confidence >= 65 and all_dims_above_50:
            scores["_rating"] = "侦察待命"
        elif attract >= 60 and confidence >= 50 and all_dims_above_40:
            scores["_rating"] = "按兵不动"
        else:
            # 回退到综合分
            composite = scores.get("综合加权", 50)
            if composite >= 75:
                scores["_rating"] = "总攻信号"
            elif composite >= 55:
                scores["_rating"] = "侦察待命"
            elif composite >= 30:
                scores["_rating"] = "按兵不动"
            else:
                scores["_rating"] = "全线撤退"

    return scores


def check_score_spread(scores: dict) -> str | None:
    dims = ["基本面", "预期差", "技术面", "资金面"]
    all_scores = [scores.get(dim, 50) for dim in dims]
    if all(60 <= score <= 80 for score in all_scores):
        return "评分区分度不足：四维评分均落在 60-80 分区间。"
    return None


def _split_report_and_summary(markdown_text: str) -> tuple[str, str]:
    if not isinstance(markdown_text, str):
        raise TypeError(f"markdown_text must be str, got {type(markdown_text)!r}")

    cleaned = markdown_text.strip()
    try:
        parts = cleaned.split("<<<REPORT_END>>>")
        if len(parts) >= 2:
            summary_text = parts[-1].strip()
            # 兼容新旧标题格式（带/不带 emoji）
            summary_text = re.sub(r"^\s*#\s*(?:💡\s*)?(?:核心摘要|战役总结)\s*", "", summary_text).strip()
            summary_text = re.sub(r"\s+", " ", summary_text)
            report_body = parts[0].strip()
            if summary_text and report_body:
                return summary_text, report_body
    except Exception as exc:
        logger.warning("[analysis_service] split report/summary failed: %r", exc)

    return SUMMARY_FALLBACK_TEXT, cleaned


def run_comprehensive_analysis(
    *,
    client,
    cfg: dict,
    selected_model: str,
    username: str,
    name: str,
    ts_code: str,
    price_df: pd.DataFrame | None = None,
    data_progress_cb: ProgressCallback | None = None,
    status_cb: StatusCallback | None = None,
    stream_cb: StreamCallback | None = None,
) -> ComprehensiveAnalysisResult:
    """Generate the comprehensive stock report without UI coupling."""
    code6 = to_code6(ts_code)
    if not name or not ts_code:
        raise ValueError("请先选择股票")

    # 舆情分析与数据采集同步并行启动（最大化时间重叠）
    _sentiment_result = [None]

    def _sentiment_worker():
        try:
            from data.stock_sentiment import fetch_stock_sentiment
            _sentiment_result[0] = fetch_stock_sentiment(
                ts_code=ts_code,
                stock_name=name,
            )
        except Exception as exc:
            logger.debug("[analysis_service] sentiment worker failed: %s", exc)

    sentiment_thread = threading.Thread(target=_sentiment_worker, daemon=True)
    sentiment_thread.start()

    if status_cb:
        status_cb(f"正在采集 {name}（{code6}）全量数据（舆情并行中）...")

    context, raw_data = build_report_context(
        ts_code,
        name,
        progress_cb=data_progress_cb,
    )

    if status_cb:
        status_cb(f"{name} 数据采集完成")

    report_price_df = raw_data.get("_price_df")
    if report_price_df is None or (
        isinstance(report_price_df, pd.DataFrame) and report_price_df.empty
    ):
        report_price_df = price_df if price_df is not None else pd.DataFrame()

    price_snap = price_summary(report_price_df) if not report_price_df.empty else "暂无K线数据"
    indicators = compute_indicators(report_price_df)
    ind_section = format_indicators_section(indicators)

    # 知识库注入 v2 — AI 策展架构（不影响主流程，异常时静默跳过）
    knowledge_ctx = ""
    try:
        from knowledge.injector import build_knowledge_context
        knowledge_ctx = build_knowledge_context(
            stock_code=ts_code,
            stock_name=name,
            model_name=selected_model,
            price_snapshot=price_snap,
            indicators=indicators,
        )
    except Exception:
        pass

    # 等待舆情线程完成，最多 30s（并行已跑了数据采集的时间，通常只剩几秒）
    sentiment_ctx = ""
    if sentiment_thread.is_alive():
        if status_cb:
            status_cb(f"等待 {name} 舆情分析完成...")
        sentiment_thread.join(timeout=30)

    if _sentiment_result[0]:
        try:
            from data.stock_sentiment import format_sentiment_for_prompt
            sentiment_ctx = format_sentiment_for_prompt(_sentiment_result[0])
            if sentiment_ctx:
                logger.info("[analysis_service] %s 舆情已就绪，注入 prompt", name)
        except Exception as exc:
            logger.debug("[analysis_service] sentiment format failed: %s", exc)

    # 宏观战局信息
    macro_brief = ""
    try:
        from data.macro_intel import get_macro_context
        _, macro_brief = get_macro_context()
    except Exception as exc:
        logger.debug("[analysis_service] macro context failed: %s", exc)

    user_prompt, system_prompt = build_report_prompt(
        name,
        ts_code,
        context,
        price_snap,
        ind_section,
        knowledge_context=knowledge_ctx,
        sentiment_context=sentiment_ctx,
        macro_context=macro_brief,
    )

    heartbeat_tips = [
        f"正在连接 {selected_model}...",
        "正在发送分析请求...",
        "AI 正在联网搜索最新资讯...",
        "AI 正在深度思考中...",
        "正在整理多维度数据...",
        "即将开始输出报告...",
        "AI 仍在思考，请耐心等待...",
        "分析内容较多，稍等片刻...",
        "正在交叉验证各维度信号...",
        "报告即将生成，请稍候...",
    ]

    chunk_queue: queue.Queue = queue.Queue()
    sentinel = object()
    stream_error = [None]

    def _stream_worker():
        try:
            raw_stream = call_ai_stream(
                client,
                cfg,
                user_prompt,
                system=system_prompt,
                max_tokens=12000,
                username=username,
            )
            for chunk in raw_stream:
                chunk_queue.put(chunk)
            if getattr(raw_stream, "error", None):
                stream_error[0] = raw_stream.error
        except Exception as exc:
            stream_error[0] = str(exc)
        finally:
            chunk_queue.put(sentinel)

    worker = threading.Thread(target=_stream_worker, daemon=True)
    worker.start()

    tip_idx = 0
    start_time = _time.time()
    got_first = False
    full_text = ""

    while not got_first:
        try:
            chunk = chunk_queue.get(timeout=3)
            if chunk is sentinel:
                break
            full_text += chunk
            got_first = True
            if stream_cb:
                stream_cb(full_text)
        except queue.Empty:
            if status_cb:
                elapsed = int(_time.time() - start_time)
                tip = heartbeat_tips[min(tip_idx, len(heartbeat_tips) - 1)]
                status_cb(f"{tip}（已等待 {elapsed}s）")
                tip_idx += 1

    while got_first:
        try:
            chunk = chunk_queue.get(timeout=120)
        except queue.Empty:
            break
        if chunk is sentinel:
            break
        full_text += chunk
        if stream_cb:
            stream_cb(full_text)

    if stream_error[0]:
        raise RuntimeError(f"报告生成出错：{stream_error[0]}")

    if not full_text or len(full_text) < 100:
        raise RuntimeError("报告生成内容过短，模型可能响应异常")

    scores = parse_scores(full_text)
    if scores:
        scores = apply_bucket_correction(scores)
        spread_warn = check_score_spread(scores)
        if spread_warn:
            logger.info("[analysis_service] %s %s", name, spread_warn)

        # 用实际评分做模式匹配，追加到报告（修复注入时机问题）
        try:
            from knowledge.injector import build_pattern_context
            pattern_ctx = build_pattern_context(scores)
            if pattern_ctx:
                if "<<<REPORT_END>>>" in full_text:
                    full_text = full_text.replace(
                        "<<<REPORT_END>>>",
                        f"\n\n{pattern_ctx}\n\n<<<REPORT_END>>>",
                    )
                else:
                    full_text += f"\n\n{pattern_ctx}"
                logger.info("[analysis_service] %s 模式匹配已追加", name)
        except Exception as exc:
            logger.debug("[analysis_service] pattern context failed: %s", exc)

    cleaned_report = _cleanup_report_text(full_text)
    summary_text, report_body = _split_report_and_summary(cleaned_report)

    logger.info("[analysis_service] %s 综合报告完成，评分=%s", name, scores)
    return ComprehensiveAnalysisResult(
        full_report=report_body,
        summary=summary_text,
        scores=scores,
        context=context,
        raw_data=raw_data,
        stock_capital=context.get("capital", ""),
        stock_northbound=context.get("northbound", ""),
        stock_margin=context.get("margin", ""),
    )


def generate_report_bundle(
    stock_name: str,
    model_name: str = DEFAULT_WECHAT_MODEL,
    username: str = "wechat_user",
) -> GeneratedReportBundle:
    """Resolve a stock and return summary + full report for non-UI channels."""
    ts_code, resolved_name, resolve_warn = resolve_stock(stock_name)
    if not ts_code:
        raise ValueError(f"无法识别股票：{stock_name}")

    if (
        not resolve_warn
        and not re.search(r"\d", stock_name)
        and ts_code == "000001.SZ"
        and resolved_name == stock_name
    ):
        raise ValueError(f"未识别到股票：{stock_name}")

    client, cfg, ai_err = get_ai_client(model_name)
    if ai_err or not cfg:
        raise RuntimeError(ai_err or "AI 模型暂不可用")
    # CLI 模式下 client=None 是正常的
    if not client and cfg.get("provider") not in ("gemini_cli", "codex_cli", "claude_cli"):
        raise RuntimeError("AI 客户端初始化失败")

    price_df, price_err = get_price_df(ts_code)
    if price_err:
        logger.info("[analysis_service] get_price_df warning for %s: %s", stock_name, price_err)

    result = run_comprehensive_analysis(
        client=client,
        cfg=cfg,
        selected_model=model_name,
        username=username,
        name=resolved_name,
        ts_code=ts_code,
        price_df=price_df,
    )

    parts = []
    if resolve_warn:
        parts.append(f"> 提示：{resolve_warn}")
    parts.append(result.full_report)

    return GeneratedReportBundle(
        stock_name=resolved_name,
        stock_code=ts_code,
        summary=result.summary or SUMMARY_FALLBACK_TEXT,
        full_report=result.full_report,
        combined_markdown="\n\n".join(parts).strip(),
        scores=result.scores,
    )


def generate_report(
    stock_name: str,
    model_name: str = DEFAULT_WECHAT_MODEL,
    username: str = "wechat_user",
) -> str:
    """Backward-compatible string wrapper."""
    return generate_report_bundle(
        stock_name=stock_name,
        model_name=model_name,
        username=username,
    ).combined_markdown
