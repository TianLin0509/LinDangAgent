# knowledge/learning_backtester.py
# -*- coding: utf-8 -*-
"""统一学习引擎 — Round 1: 批量回测执行器。

选题(Z模式) → 时间锁定 war_room 分析 → 三级归因判卷 → holdout 分割。
"""

import json
import logging
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from knowledge.kb_config import BASE_DIR, KNOWLEDGE_DIR, DIRECTION_CN
from knowledge.learning_config import (
    FAMILIAR_RATIO, EXPLORE_RATIO, MIN_TURNOVER_20D,
    MIN_VOLATILITY_20D, MIN_ABS_CHANGE_20D,
    EXAM_DATE_RANGE, EXAM_DATE_SLOTS, HOLDOUT_RATIO,
)

logger = logging.getLogger(__name__)


# ── 判卷 ──────────────────────────────────────────────────────────

def grade_result(direction: str, excess_return: float) -> str:
    """三级归因判卷。返回 'hit' 或 'miss'。"""
    if direction == "bullish" and excess_return > 0:
        return "hit"
    if direction == "bearish" and excess_return < 0:
        return "hit"
    if direction == "neutral" and abs(excess_return) < 3.0:
        return "hit"
    return "miss"


def categorize_return(ret: float) -> str:
    """分类实际收益率。"""
    if ret > 10:
        return "big_rise"
    if ret > 3:
        return "rise"
    if ret > -3:
        return "flat"
    if ret > -8:
        return "fall"
    return "big_fall"


def split_holdout(items: list, ratio: float = HOLDOUT_RATIO) -> tuple[list, list]:
    """随机分割训练集和验证集。返回 (train, holdout)。"""
    shuffled = list(items)
    random.shuffle(shuffled)
    n_holdout = max(1, int(len(shuffled) * ratio))
    return shuffled[n_holdout:], shuffled[:n_holdout]


# ── 选题 ──────────────────────────────────────────────────────────

def _generate_exam_dates(count: int) -> list[str]:
    """生成分散的考试日期列表。"""
    lo, hi = EXAM_DATE_RANGE
    slots = min(EXAM_DATE_SLOTS, count)
    dates = []
    for _ in range(slots):
        offset = random.randint(lo, hi)
        d = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
        dates.append(d)
    return dates


def _fetch_familiar_pool() -> list[dict]:
    """从 reports.db + case_memory 获取已知领域股票。"""
    pool = []
    try:
        from repositories.report_repo import list_reports
        reports = list_reports(limit=200)
        seen = set()
        for r in reports:
            code = r.get("stock_code", "")
            if code and code not in seen:
                seen.add(code)
                pool.append({
                    "ts_code": code,
                    "stock_name": r.get("stock_name", code),
                    "source": "reports",
                })
    except Exception as exc:
        logger.warning("[learn] failed to load reports pool: %s", exc)

    # 弱项板块加权
    try:
        from knowledge.simulation_training import get_simulation_stats
        stats = get_simulation_stats()
        weak = {w["sector"] for w in stats.get("weak_sectors", [])}
        for item in pool:
            item["_weak_boost"] = 2.0 if any(w in item["stock_name"] for w in weak) else 1.0
    except Exception:
        pass

    return pool


def _fetch_explore_pool(exclude_codes: set[str]) -> list[dict]:
    """从全市场随机抽样获取探索领域股票。"""
    pool = []
    try:
        from data.tushare_client import load_stock_list
        stock_list, _ = load_stock_list()
        if stock_list is None or stock_list.empty:
            return pool

        # 排除 ST 和已有的
        df = stock_list[~stock_list["name"].str.contains("ST|退", na=False)]
        df = df[~df["ts_code"].isin(exclude_codes)]

        # 按市值分层: 大盘/中盘/小盘 = 3:4:3
        n = len(df)
        if n < 30:
            sample = df
        else:
            sorted_df = df.sort_values("ts_code")  # proxy sort
            large = sorted_df.head(n // 3).sample(min(10, n // 3))
            mid = sorted_df.iloc[n // 3: 2 * n // 3].sample(min(13, n // 3))
            small = sorted_df.tail(n // 3).sample(min(10, n // 3))
            sample = pd.concat([large, mid, small])

        for _, row in sample.iterrows():
            pool.append({
                "ts_code": row["ts_code"],
                "stock_name": row.get("name", row["ts_code"]),
                "source": "explore",
                "_weak_boost": 1.0,
            })
    except Exception as exc:
        logger.warning("[learn] failed to load explore pool: %s", exc)

    return pool


def _apply_filters(stock: dict, exam_date: str) -> bool:
    """应用硬过滤门槛: 流动性、波动性、非ST、非停牌。"""
    from knowledge.simulation_training import _fetch_historical_kline

    ts_code = stock["ts_code"]
    try:
        df = _fetch_historical_kline(ts_code, exam_date, days=60)
        if df is None or len(df) < 10:
            return False

        # 日均成交额(简化: vol * close 近似)
        if "vol" in df.columns and "close" in df.columns:
            avg_turnover = (df["vol"] * df["close"]).mean()
            if avg_turnover < MIN_TURNOVER_20D / 100:  # vol 单位是手
                return False

        # 波动性
        if "pct_chg" in df.columns:
            avg_volatility = df["pct_chg"].abs().mean()
            total_change = abs(df["pct_chg"].sum())
            if avg_volatility < MIN_VOLATILITY_20D and total_change < MIN_ABS_CHANGE_20D:
                return False

        return True
    except Exception:
        return False


def select_exam_stocks(count: int) -> list[dict]:
    """Z 模式选题: 70% 已知 + 30% 探索。

    返回: [{ts_code, stock_name, exam_date, source}, ...]
    """
    from knowledge.simulation_training import _clear_proxy, _restore_proxy
    _clear_proxy()  # 国内数据源不走代理

    n_familiar = int(count * FAMILIAR_RATIO)
    n_explore = count - n_familiar

    familiar = _fetch_familiar_pool()
    random.shuffle(familiar)

    # 弱项加权排序
    familiar.sort(key=lambda x: x.get("_weak_boost", 1.0), reverse=True)
    familiar = familiar[:n_familiar * 3]  # 取 3 倍候选

    familiar_codes = {s["ts_code"] for s in familiar}
    explore = _fetch_explore_pool(familiar_codes)
    random.shuffle(explore)
    explore = explore[:n_explore * 3]

    # 生成考试日期
    exam_dates = _generate_exam_dates(count)

    # 组合选题 + 过滤
    # reports 来源：用户已分析过，默认符合标准，跳过过滤（节省时间）
    # explore 来源：全市场随机，需要 filter 验证流动性/波动性
    candidates = familiar + explore
    selected = []
    date_idx = 0
    tried = 0
    skipped_filter = 0

    for stock in candidates:
        if len(selected) >= count:
            break
        exam_date = exam_dates[date_idx % len(exam_dates)]
        tried += 1
        src = stock.get("source", "")

        # reports 来源跳过过滤
        if src == "reports":
            stock["exam_date"] = exam_date
            selected.append(stock)
            date_idx += 1
            skipped_filter += 1
            continue

        # explore 来源需要 filter
        try:
            if _apply_filters(stock, exam_date):
                stock["exam_date"] = exam_date
                selected.append(stock)
                date_idx += 1
        except Exception as exc:
            logger.debug("[learn] filter failed for %s: %s", stock.get("ts_code"), exc)

    logger.info("[learn] candidates=%d, tried=%d, selected=%d (skipped filter=%d)",
                len(candidates), tried, len(selected), skipped_filter)

    _restore_proxy()  # 恢复代理（Claude API 需要）

    logger.info("[learn] selected %d exam stocks (%d familiar, %d explore)",
                len(selected),
                sum(1 for s in selected if s.get("source") == "reports"),
                sum(1 for s in selected if s.get("source") == "explore"))
    return selected


# ── 单只回测 ──────────────────────────────────────────────────────

def run_single_backtest(exam: dict, progress_cb=None) -> dict | None:
    """对单只股票执行完整 war_room 回测。

    返回含评分、方向、判卷结果的 dict，失败返回 None。
    """
    from services.war_room import run_war_room
    from knowledge.simulation_training import (
        _fetch_historical_kline, _get_market_return, _get_sector_return,
        _calc_return_from_kline,
    )

    ts_code = exam["ts_code"]
    stock_name = exam["stock_name"]
    exam_date = exam["exam_date"]

    if progress_cb:
        progress_cb(f"回测 {stock_name} ({exam_date})...")

    # 获取实际 T+10 收益（国内数据源不走代理）
    from knowledge.simulation_training import _clear_proxy, _restore_proxy
    _clear_proxy()
    try:
        df = _fetch_historical_kline(ts_code, datetime.now().strftime("%Y%m%d"), days=120)
        actual_return = _calc_return_from_kline(df, exam_date)
        if actual_return is None:
            logger.warning("[learn] no future data for %s %s", stock_name, exam_date)
            return None
    except Exception as exc:
        logger.warning("[learn] return calc failed for %s: %s", stock_name, exc)
        _restore_proxy()
        return None

    _restore_proxy()  # 恢复代理（war_room 调用 Claude 需要）

    # 完整 war_room 分析（时间锁定）
    try:
        result = run_war_room(
            stock_name=stock_name,
            preset="opus",
            time_lock=exam_date,
            skip_report_save=True,
        )
    except Exception as exc:
        logger.warning("[learn] war_room failed for %s: %s", stock_name, exc)
        return None

    if not result or not result.final_scores:
        return None

    scores = result.final_scores
    weighted = scores.get("综合加权", 50)
    direction = "bullish" if weighted >= 55 else ("bearish" if weighted <= 45 else "neutral")

    # 三级归因（国内数据源不走代理）
    _clear_proxy()
    market_return = _get_market_return(exam_date)
    sector_return, sector_name = _get_sector_return(ts_code, exam_date)
    _restore_proxy()
    benchmark = max(market_return, sector_return) if sector_return != 0 else market_return
    excess_return = actual_return - benchmark
    stock_alpha = actual_return - (sector_return if sector_return != 0 else market_return)

    verdict = grade_result(direction, excess_return)

    return {
        "ts_code": ts_code,
        "stock_name": stock_name,
        "exam_date": exam_date,
        "source": exam.get("source", ""),
        "category": categorize_return(actual_return),
        "scores": scores,
        "weighted": weighted,
        "direction": direction,
        "direction_cn": DIRECTION_CN.get(direction, "中性"),
        "actual_return_10d": round(actual_return, 2),
        "market_return_10d": round(market_return, 2),
        "sector_name": sector_name,
        "sector_return_10d": round(sector_return, 2),
        "stock_alpha": round(stock_alpha, 2),
        "excess_return": round(excess_return, 2),
        "verdict": verdict,
        "analysis_summary": result.final_summary[:500] if result.final_summary else "",
        "combined_markdown": result.combined_markdown[:6000] if result.combined_markdown else "",
        "round1_scores": (result.general_reports[0].get("scores") if result.general_reports else {}),
    }


# ── 批量回测 ──────────────────────────────────────────────────────

def compute_stats(results: list[dict]) -> dict:
    """从回测结果列表计算汇总统计（分方向/板块/类别）。"""
    total = len(results)
    hits = sum(1 for r in results if r["verdict"] == "hit")
    hit_rate = hits / total * 100 if total > 0 else 0

    by_direction = {}
    for r in results:
        d = r["direction_cn"]
        by_direction.setdefault(d, {"total": 0, "hits": 0})
        by_direction[d]["total"] += 1
        if r["verdict"] == "hit":
            by_direction[d]["hits"] += 1
    for v in by_direction.values():
        v["hit_rate"] = round(v["hits"] / v["total"] * 100, 1) if v["total"] > 0 else 0

    by_sector = {}
    for r in results:
        s = r.get("sector_name", "未知") or "未知"
        by_sector.setdefault(s, {"total": 0, "hits": 0})
        by_sector[s]["total"] += 1
        if r["verdict"] == "hit":
            by_sector[s]["hits"] += 1
    for v in by_sector.values():
        v["hit_rate"] = round(v["hits"] / v["total"] * 100, 1) if v["total"] > 0 else 0

    by_category = {}
    for r in results:
        c = r["category"]
        by_category.setdefault(c, {"total": 0, "hits": 0})
        by_category[c]["total"] += 1
        if r["verdict"] == "hit":
            by_category[c]["hits"] += 1
    for v in by_category.values():
        v["hit_rate"] = round(v["hits"] / v["total"] * 100, 1) if v["total"] > 0 else 0

    return {
        "total": total, "hits": hits, "hit_rate": round(hit_rate, 1),
        "by_direction": by_direction, "by_sector": by_sector, "by_category": by_category,
    }


def run_backtest_stage(
    session_id: str,
    count: int = 50,
    delay_between: int = 30,
    progress_cb=None,
) -> dict:
    """Stage 1: 批量回测（支持断点续跑）。

    - 首次调用: 选题 → 存入 session，逐只回测（结果追加到 results.jsonl）
    - 断点续跑: 从 session 加载 exams + 已完成 results，跳过已完成的继续

    返回: {status, train_results, holdout_exams, stats}
    """
    from knowledge.learning_session import (
        load_exams, save_exams, append_result, load_completed_results,
        completed_codes, save_backtest_stats, update_stage,
        STATE_IN_PROGRESS, STATE_DONE,
    )

    update_stage(session_id, "backtest", STATE_IN_PROGRESS)

    # 加载已有 exams 或新选题
    train_exams, holdout_exams = load_exams(session_id)
    if not train_exams:
        if progress_cb:
            progress_cb("Stage 1: 选题中...")
        exams = select_exam_stocks(count)
        if not exams:
            return {"status": "no_exams", "message": "选题失败，请检查数据源"}
        train_exams, holdout_exams = split_holdout(exams, HOLDOUT_RATIO)
        save_exams(session_id, train_exams, holdout_exams)
        if progress_cb:
            progress_cb(f"Stage 1: 新建选题 {len(train_exams)} 只训练 + {len(holdout_exams)} 只验证")
    else:
        if progress_cb:
            progress_cb(f"Stage 1: 恢复 session，训练 {len(train_exams)} / 验证 {len(holdout_exams)}")

    # 找出还未完成的
    done_codes = completed_codes(session_id)
    pending = [e for e in train_exams if e["ts_code"] not in done_codes]

    if progress_cb and done_codes:
        progress_cb(f"已完成 {len(done_codes)} 只，待跑 {len(pending)} 只")

    # 继续跑剩下的
    for i, exam in enumerate(pending):
        if progress_cb:
            progress_cb(f"[{i+1}/{len(pending)}] {exam['stock_name']} ({exam.get('exam_date', '?')})")

        result = run_single_backtest(exam, progress_cb)
        if result:
            append_result(session_id, result)

        if i < len(pending) - 1 and delay_between > 0:
            time.sleep(delay_between)

    # 汇总所有结果
    results = load_completed_results(session_id)
    stats = compute_stats(results)
    save_backtest_stats(session_id, stats)
    update_stage(session_id, "backtest", STATE_DONE)

    return {
        "status": "ok",
        "train_results": results,
        "holdout_exams": holdout_exams,
        "stats": stats,
    }


# 保留旧接口兼容（不持久化，一次性跑完）
def run_backtest_round(count: int = 50, delay_between: int = 30, progress_cb=None) -> dict:
    """[兼容] 一次性批量回测，不持久化。新代码请用 run_backtest_stage。"""
    exams = select_exam_stocks(count)
    if not exams:
        return {"status": "no_exams", "message": "选题失败，请检查数据源"}

    train_exams, holdout_exams = split_holdout(exams, HOLDOUT_RATIO)

    if progress_cb:
        progress_cb(f"Round 1: {len(train_exams)} 只训练 + {len(holdout_exams)} 只验证")

    results = []
    for i, exam in enumerate(train_exams):
        if progress_cb:
            progress_cb(f"[{i+1}/{len(train_exams)}] {exam['stock_name']}")
        r = run_single_backtest(exam, progress_cb)
        if r:
            results.append(r)
        if i < len(train_exams) - 1 and delay_between > 0:
            time.sleep(delay_between)

    return {
        "status": "ok", "train_results": results, "holdout_exams": holdout_exams,
        "stats": compute_stats(results),
    }
