"""Async Top10 pipeline based on full research reports."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime


logger = logging.getLogger(__name__)

_STATUS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
os.makedirs(_STATUS_DIR, exist_ok=True)

_running_lock = threading.Lock()
_is_running = False
_STALE_STATUS_SECONDS = 20 * 60


def _status_file() -> str:
    return os.path.join(_STATUS_DIR, f"{date.today().isoformat()}_deep_status.json")


def _is_status_stale(fp: str, status: dict | None) -> bool:
    if not status or status.get("status") != "running":
        return False
    try:
        last_update = os.path.getmtime(fp)
    except OSError:
        return False
    return (time.time() - last_update) >= _STALE_STATUS_SECONDS


def _mark_status_stale(fp: str, status: dict | None) -> dict:
    stale_status = dict(status or {})
    stale_status["status"] = "error"
    stale_status["error"] = stale_status.get("error") or "Top10 task heartbeat timed out; previous run was interrupted"
    stale_status["phase"] = stale_status.get("phase") or "interrupted"
    stale_status["finished"] = datetime.now().isoformat()
    try:
        with open(fp, "w", encoding="utf-8") as handle:
            json.dump(stale_status, handle, ensure_ascii=False, default=str)
    except Exception:
        pass
    return stale_status


def _write_status(status: dict):
    try:
        with open(_status_file(), "w", encoding="utf-8") as handle:
            json.dump(status, handle, ensure_ascii=False, default=str)
    except Exception:
        pass


def get_deep_status() -> dict | None:
    fp = _status_file()
    if not os.path.exists(fp):
        return None
    try:
        with open(fp, "r", encoding="utf-8") as handle:
            status = json.load(handle)
    except Exception:
        return None
    if _is_status_stale(fp, status):
        logger.warning("[deep_top10] stale running status detected: %s", fp)
        return _mark_status_stale(fp, status)
    return status


def is_deep_running() -> bool:
    global _is_running
    with _running_lock:
        if _is_running:
            return True
    status = get_deep_status()
    return bool(status and status.get("status") == "running")


def run_deep_top10(
    model_name: str = "🟣 豆包 · Seed 2.0 Pro",
    candidate_count: int = 100,
    username: str = "auto_scheduler",
    progress_callback=None,
):
    global _is_running

    from core.ai_client import call_ai, get_ai_client, get_token_usage
    from top10.hot_rank import get_hot_rank, get_volume_rank, get_xueqiu_hot, merge_candidates
    from top10.prompts import SYSTEM_SUMMARY, build_summary_prompt
    from top10.runner import _send_top10_email, save_cached_result
    from top10.scorer import score_all
    from top10.stock_filter import apply_filters
    from top10.tushare_data import enrich_candidates, get_sector_rotation, ts_ok

    with _running_lock:
        if _is_running:
            logger.warning("[deep_top10] 已有任务在运行（进程内），跳过")
            return
        current = get_deep_status()
        if current and current.get("status") == "running":
            logger.warning("[deep_top10] 已有任务在运行（跨进程），跳过")
            return
        _is_running = True

    def _log(message: str):
        logger.info("[deep_top10] %s", message)
        if progress_callback:
            progress_callback(message)
        status["progress"].append(message)
        _write_status(status)

    status = {
        "status": "running",
        "started": datetime.now().isoformat(),
        "model": model_name,
        "username": username,
        "phase": "",
        "progress": [],
        "error": None,
    }
    _write_status(status)

    tokens_before = get_token_usage()["total"]

    try:
        status["phase"] = "获取候选池"
        _write_status(status)
        _log("📡 Phase 1: 获取候选池...")

        hot_df, _ = get_hot_rank(50)
        xq_df, _ = get_xueqiu_hot(50)
        vol_df, _ = get_volume_rank(50)
        merged = merge_candidates(hot_df, vol_df, xq_df)
        filtered = apply_filters(merged)
        candidates = filtered.head(candidate_count)
        _log(f"  候选池: 东财{len(hot_df)} + 雪球{len(xq_df)} + 成交额{len(vol_df)} -> {len(candidates)} 只")
        if candidates.empty:
            raise RuntimeError("候选池为空")

        status["phase"] = "数据增强"
        _write_status(status)
        _log("📊 Phase 2: Tushare 数据增强...")
        if ts_ok():
            enriched = enrich_candidates(candidates, progress_callback=lambda msg: _log(f"  {msg}"))
            _log("  ✅ 数据增强完成")
        else:
            enriched = candidates
            _log("  ⚠️ Tushare 不可用，使用基础数据")

        status["phase"] = "生成研报"
        _write_status(status)
        _log(f"🤖 Phase 3: 为 {len(enriched)} 只候选股生成价值投机研报...")

        client, cfg, err = get_ai_client(model_name)
        if err:
            raise RuntimeError(f"AI 客户端初始化失败: {err}")

        def score_progress(current, total, msg):
            _log(f"  [{current}/{total}] {msg}")

        scored = score_all(
            client,
            cfg,
            enriched,
            model_name=model_name,
            progress_callback=score_progress,
            max_workers=2,
            username=username,
        )
        _log(f"  ✅ 研报生成完成，共 {len(scored)} 只")

        status["phase"] = "总结保存"
        _write_status(status)
        _log("📋 Phase 4: 生成 Top10 总结并保存结果...")

        top10 = scored.head(10)
        stock_lines = []
        for _, row in top10.iterrows():
            line = (
                f"- {row['股票名称']}({row['代码']}) "
                f"行业:{row.get('行业', '未知')} "
                f"综合匹配度{row.get('综合匹配度', 0):.1f}分 "
                f"操作评级:{row.get('操作评级', '未知')} "
                f"链接:{row.get('报告链接', '')}"
            )
            if row.get("核心摘要"):
                line += f" 摘要:{row['核心摘要']}"
            stock_lines.append(line)
        stocks_text = "\n".join(stock_lines)

        try:
            sectors = get_sector_rotation()
            if sectors.get("概念板块"):
                stocks_text += "\n\n今日概念板块涨幅Top5：" + "、".join(sectors["概念板块"])
            if sectors.get("行业板块"):
                stocks_text += "\n今日行业板块涨幅Top5：" + "、".join(sectors["行业板块"])
        except Exception:
            pass

        summary_prompt = build_summary_prompt(stocks_text, len(candidates))
        summary, summary_err = call_ai(
            client,
            cfg,
            summary_prompt,
            system=SYSTEM_SUMMARY,
            max_tokens=4000,
            username=username,
        )
        if summary_err:
            summary = f"总结生成失败：{summary_err}"

        tokens_after = get_token_usage()["total"]
        tokens_used = tokens_after - tokens_before

        save_cached_result(
            model_name,
            scored,
            summary,
            triggered_by=username,
            tokens_used=tokens_used,
        )

        _log(f"✅ 全部完成！共消耗 {tokens_used:,} token")
        _log("📧 发送 Top10 报告邮件...")
        _send_top10_email(summary, scored, model_name, username, tokens_used)

        status["status"] = "done"
        status["phase"] = "完成"
        status["finished"] = datetime.now().isoformat()
        status["tokens_used"] = tokens_used
        status["scored_count"] = len(scored)
        status["top10_links"] = top10["报告链接"].tolist() if "报告链接" in top10.columns else []
        _write_status(status)

    except Exception as exc:
        logger.error("[deep_top10] 异常: %s", exc, exc_info=True)
        status["status"] = "error"
        status["error"] = str(exc)
        _write_status(status)
    finally:
        with _running_lock:
            _is_running = False


def start_deep_top10_async(
    model_name: str = "🟣 豆包 · Seed 2.0 Pro",
    candidate_count: int = 100,
    username: str = "auto_scheduler",
):
    if is_deep_running():
        return False
    thread = threading.Thread(
        target=run_deep_top10,
        kwargs={
            "model_name": model_name,
            "candidate_count": candidate_count,
            "username": username,
        },
        daemon=True,
    )
    thread.start()
    return True
