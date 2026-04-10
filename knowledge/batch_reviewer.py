"""批量复盘模块 — 对比 war_room_tracker 预测与实际行情，生成经验条目。

主入口::

    from knowledge.batch_reviewer import run_batch_review
    result = run_batch_review(date_from="2026-04-01", date_to="2026-04-10")
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TRACKER_FILE = Path(__file__).resolve().parent.parent / "data" / "knowledge" / "war_room_tracker.jsonl"


# ── 加载 tracker 条目 ──────────────────────────────────────────────

def _load_tracker_entries(
    stock_name: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
) -> list[dict]:
    """从 war_room_tracker.jsonl 加载并过滤条目。"""
    if not TRACKER_FILE.exists():
        logger.warning("[batch_reviewer] tracker file not found: %s", TRACKER_FILE)
        return []

    entries = []
    for line in TRACKER_FILE.read_text(encoding="utf-8").strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        # 日期过滤
        entry_date_str = entry.get("report_date", "")[:10]
        if date_from and entry_date_str < date_from:
            continue
        if date_to and entry_date_str > date_to:
            continue

        # 股票名过滤
        if stock_name and entry.get("stock_name", "") != stock_name:
            continue

        entries.append(entry)

    return entries


# ── 拉取实际行情 ───────────────────────────────────────────────────

def _fetch_actual_returns(ts_code: str, analysis_date: str) -> dict | None:
    """拉取 T+5 和 T+20 实际收益率及最大回撤。

    analysis_date: YYYY-MM-DD 格式
    Returns: {close_base, return_5d, return_20d, max_drawdown} 或 None
    """
    try:
        from data.tushare_client import get_price_df
        price_df, err = get_price_df(ts_code, days=60)
        if err or price_df is None or price_df.empty:
            logger.warning("[batch_reviewer] price fetch failed for %s: %s", ts_code, err)
            return None
    except Exception as exc:
        logger.warning("[batch_reviewer] price fetch error for %s: %r", ts_code, exc)
        return None

    try:
        df = price_df.copy()
        # 标准化日期列
        if "日期" in df.columns:
            df["_date"] = df["日期"].astype(str).str[:10]
        elif "trade_date" in df.columns:
            df["_date"] = df["trade_date"].astype(str).str[:10]
        else:
            logger.warning("[batch_reviewer] cannot find date column in price_df for %s", ts_code)
            return None

        df_sorted = df.sort_values("_date").reset_index(drop=True)
        after = df_sorted[df_sorted["_date"] >= analysis_date]
        if after.empty:
            logger.warning("[batch_reviewer] no price data after %s for %s", analysis_date, ts_code)
            return None

        base_idx = after.index[0]

        # 收盘价列名适配
        close_col = "收盘" if "收盘" in df_sorted.columns else "close"
        low_col = "最低" if "最低" in df_sorted.columns else "low"

        base_close = float(df_sorted.iloc[base_idx][close_col])
        if base_close <= 0:
            return None

        def _get_return(offset: int) -> float | None:
            target_idx = base_idx + offset
            if target_idx < len(df_sorted):
                future_close = float(df_sorted.iloc[target_idx][close_col])
                return round((future_close - base_close) / base_close * 100, 2)
            return None

        return_5d = _get_return(5)
        return_20d = _get_return(20)

        # 最大回撤：从 base_idx 到 base_idx+20
        end_idx = min(base_idx + 21, len(df_sorted))
        window = df_sorted.iloc[base_idx:end_idx]
        if low_col in window.columns and not window.empty:
            lows = window[low_col].astype(float)
            min_low = lows.min()
            max_drawdown = round((min_low - base_close) / base_close * 100, 2)
        else:
            max_drawdown = None

        return {
            "close_base": base_close,
            "return_5d": return_5d,
            "return_20d": return_20d,
            "max_drawdown": max_drawdown,
        }
    except Exception as exc:
        logger.warning("[batch_reviewer] return calc error for %s: %r", ts_code, exc)
        return None


# ── 评分提取 ───────────────────────────────────────────────────────

def _get_final_score(entry: dict) -> float:
    """从 tracker entry 中提取最终综合加权分（百分制）。"""
    # lin_biao 裁决分数优先
    lin_biao = entry.get("lin_biao", {})
    if lin_biao and "综合加权" in lin_biao:
        score = lin_biao["综合加权"]
        # 如果是 10 分制，转成百分制
        return float(score) if float(score) > 10 else float(score) * 10

    # 将领平均分
    generals = entry.get("generals", {})
    if generals:
        scores = [
            g.get("综合加权", 0)
            for g in generals.values()
            if isinstance(g, dict) and "综合加权" in g
        ]
        if scores:
            avg = sum(scores) / len(scores)
            return float(avg) if avg > 10 else avg * 10

    return 50.0


def _get_direction(score: float) -> str:
    """根据百分制评分推断方向。"""
    if score >= 60:
        return "bullish"
    elif score <= 40:
        return "bearish"
    return "neutral"


# ── 自动打标签 ─────────────────────────────────────────────────────

def _auto_tags(score: float, return_20d: float | None, max_drawdown: float | None, direction: str, actual_direction: str) -> list[str]:
    """根据结果自动生成标签。"""
    tags = []
    if return_20d is not None:
        if score >= 75 and return_20d < -5:
            tags.append("高分陷阱")
        if score < 45 and return_20d > 10:
            tags.append("低分逆袭")
    if max_drawdown is not None and max_drawdown < -15:
        tags.append("大幅回撤")
    if direction != "neutral" and actual_direction != "neutral" and direction != actual_direction:
        tags.append("方向错误")
    return tags


# ── 自动生成教训 ───────────────────────────────────────────────────

def _auto_lesson(score: float, return_20d: float | None, tags: list[str]) -> str:
    """根据评分和实际收益生成简单教训文本。"""
    if not tags:
        if return_20d is not None and return_20d > 5:
            return f"评分{score:.0f}分，20日实现{return_20d:+.1f}%正收益，预测准确。"
        elif return_20d is not None and return_20d < -5:
            return f"评分{score:.0f}分，20日亏损{return_20d:.1f}%，需复盘判断失误原因。"
        return f"评分{score:.0f}分，20日收益{return_20d:+.1f}%，表现中性。" if return_20d is not None else f"评分{score:.0f}分，数据不足。"

    parts = []
    if "高分陷阱" in tags:
        parts.append(f"高分（{score:.0f}）但实际大跌{return_20d:.1f}%，警惕市值/流动性/消息面高估风险。")
    if "低分逆袭" in tags:
        parts.append(f"低分（{score:.0f}）但实际大涨{return_20d:+.1f}%，需回查是否漏估催化剂。")
    if "大幅回撤" in tags:
        parts.append("出现超过15%的最大回撤，止损纪律至关重要。")
    if "方向错误" in tags:
        parts.append("方向判断失误，需检查基本面或技术面解读是否存在偏差。")
    return " ".join(parts)


# ── 保存经验条目 ───────────────────────────────────────────────────

def _save_experience_entry(review: dict) -> None:
    """将复盘结果保存为 case_memory 经验条目。"""
    try:
        from knowledge.case_memory import CaseCard, store_case, classify_outcome
        from knowledge.case_memory import extract_sector_tags, build_situation_summary

        score = review["score"]
        return_20d = review.get("return_20d") or 0.0
        return_5d = review.get("return_5d") or 0.0
        return_10d = return_20d  # 用 20d 近似 10d（tracker 里没有 10d）
        direction = review["direction"]

        case = CaseCard(
            case_id=f"batch_{review['report_id'][:16]}",
            report_date=review["report_date"],
            stock_code=review.get("ts_code", ""),
            stock_name=review["stock_name"],
            source="batch_review",
            score_weighted=score,
            direction=direction,
            return_5d=return_5d,
            return_10d=return_10d,
            return_20d=return_20d,
            hit_10d=(return_10d > 0) if direction == "bullish" else (return_10d < 0) if direction == "bearish" else None,
            outcome_type=classify_outcome(direction, return_10d),
            lesson=review.get("lesson", ""),
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        case.sector_tags = extract_sector_tags(review["stock_name"], review.get("ts_code", ""))
        case.situation_summary = build_situation_summary(case)
        store_case(case)
        logger.info("[batch_reviewer] saved experience entry for %s", review["stock_name"])
    except Exception as exc:
        logger.warning("[batch_reviewer] failed to save experience entry: %r", exc)


# ── 发送邮件 ───────────────────────────────────────────────────────

def _send_report_email(summary: dict, date_from: str, date_to: str) -> None:
    """发送批量复盘报告邮件。"""
    try:
        from utils.email_sender import send_text_email, smtp_configured
        if not smtp_configured():
            logger.info("[batch_reviewer] SMTP not configured, skip email")
            return

        subject = f"[LinDangAgent] 批量复盘报告 {date_from}~{date_to}"

        total = summary["total_reviewed"]
        acc = summary.get("direction_accuracy")
        avg_ret = summary.get("avg_return_20d")

        lines = [
            f"批量复盘报告 {date_from} ~ {date_to}",
            "=" * 40,
            f"复盘总数：{total}",
            f"方向准确率：{acc:.1%}" if acc is not None else "方向准确率：N/A",
            f"平均20日收益：{avg_ret:+.2f}%" if avg_ret is not None else "平均20日收益：N/A",
            "",
            "--- 明细 ---",
        ]
        for r in summary.get("reviews", []):
            tags_str = " ".join(r.get("tags", [])) or "无标签"
            ret20 = r.get("return_20d")
            ret20_str = f"{ret20:+.2f}%" if ret20 is not None else "N/A"
            lines.append(
                f"{r['stock_name']} ({r.get('ts_code','')}) "
                f"评分:{r['score']:.0f} 方向:{r['direction']} "
                f"20日:{ret20_str} 标签:[{tags_str}]"
            )
            if r.get("lesson"):
                lines.append(f"  教训: {r['lesson']}")

        body = "\n".join(lines)
        send_text_email(subject, body)
        logger.info("[batch_reviewer] email sent: %s", subject)
    except Exception as exc:
        logger.warning("[batch_reviewer] email send failed: %r", exc)


# ── 主函数 ────────────────────────────────────────────────────────

def run_batch_review(
    stock_name: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    """批量复盘：对比分析预测与实际走势，生成经验条目。

    1. 从 war_room_tracker.jsonl 按日期/股票过滤条目
    2. 拉取 T+5、T+20 实际收益率
    3. 对比预测与实际，自动打标签、生成教训
    4. 保存为 case_memory 经验条目
    5. 构建汇总报告
    6. 发送邮件
    """
    # 默认最近7天
    today = date.today()
    if date_from is None:
        date_from = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    if date_to is None:
        date_to = today.strftime("%Y-%m-%d")

    logger.info("[batch_reviewer] run_batch_review stock=%s from=%s to=%s", stock_name, date_from, date_to)

    entries = _load_tracker_entries(stock_name, date_from, date_to)
    logger.info("[batch_reviewer] loaded %d tracker entries", len(entries))

    reviews = []
    direction_hits = []

    for entry in entries:
        report_id = entry.get("report_id", "")
        s_name = entry.get("stock_name", "")
        ts_code = entry.get("stock_code", entry.get("ts_code", ""))
        report_date = entry.get("report_date", "")[:10]

        score = _get_final_score(entry)
        direction = _get_direction(score)

        # 拉取实际行情
        actual = _fetch_actual_returns(ts_code, report_date) if ts_code else None

        return_5d = actual["return_5d"] if actual else None
        return_20d = actual["return_20d"] if actual else None
        max_drawdown = actual["max_drawdown"] if actual else None

        # 计算实际方向
        if return_20d is not None:
            actual_direction = "bullish" if return_20d > 2 else ("bearish" if return_20d < -2 else "neutral")
        else:
            actual_direction = "neutral"

        # 方向是否命中
        direction_hit = None
        if direction != "neutral" and actual_direction != "neutral":
            direction_hit = direction == actual_direction
            direction_hits.append(direction_hit)

        tags = _auto_tags(score, return_20d, max_drawdown, direction, actual_direction)
        lesson = _auto_lesson(score, return_20d, tags)

        review = {
            "report_id": report_id,
            "stock_name": s_name,
            "ts_code": ts_code,
            "report_date": report_date,
            "score": score,
            "direction": direction,
            "actual_direction": actual_direction,
            "return_5d": return_5d,
            "return_20d": return_20d,
            "max_drawdown": max_drawdown,
            "direction_hit": direction_hit,
            "tags": tags,
            "lesson": lesson,
        }
        reviews.append(review)

        # 保存经验条目
        _save_experience_entry(review)

    # 汇总统计
    total_reviewed = len(reviews)
    direction_accuracy = (sum(direction_hits) / len(direction_hits)) if direction_hits else None
    ret20_valid = [r["return_20d"] for r in reviews if r["return_20d"] is not None]
    avg_return_20d = round(sum(ret20_valid) / len(ret20_valid), 2) if ret20_valid else None

    summary = {
        "total_reviewed": total_reviewed,
        "direction_accuracy": direction_accuracy,
        "avg_return_20d": avg_return_20d,
        "date_from": date_from,
        "date_to": date_to,
        "stock_filter": stock_name,
        "reviews": reviews,
    }

    # 发送邮件
    _send_report_email(summary, date_from, date_to)

    logger.info(
        "[batch_reviewer] done: total=%d acc=%s avg_ret20=%s",
        total_reviewed,
        f"{direction_accuracy:.1%}" if direction_accuracy is not None else "N/A",
        f"{avg_return_20d:+.2f}%" if avg_return_20d is not None else "N/A",
    )
    return summary
