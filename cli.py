"""LinDangAgent CLI — Claude Code 直接调用入口。

用法:
    python cli.py analyze 贵州茅台        # 单股分析（走指挥部模式）
    python cli.py kline 600519            # K线预测（同步）
    python cli.py top10-query             # 查看Top10
    python cli.py top10-generate          # 生成Top10（后台）
    python cli.py top10-progress          # Top10进度
    python cli.py top100-query            # 查看Top100
    python cli.py top100-review           # 生成Top100复盘（后台）
    python cli.py sentiment-query         # 查看舆情
    python cli.py sentiment-generate      # 生成舆情（后台）
    python cli.py stock-sentiment <股票>  # 单股舆情
    python cli.py reports [limit]         # 查看历史报告
    python cli.py read-report <id>        # 导出报告
    python cli.py health                  # 健康检查
    python cli.py token-balance [model]   # 余额查询
    python cli.py knowledge-stats         # 知识库统计
    python cli.py knowledge-update        # 手动触发知识库更新
    python cli.py set-model <模型名>
    python cli.py get-model
    python cli.py list-models
    python cli.py xueqiu-posts            # 爬取雪球热帖并保存本地 HTML
    python cli.py review-run [min_days]   # 复盘反思
    python cli.py review-cases [limit]    # 查看复盘教训
    python cli.py review-summary          # 复盘总览
    python cli.py intel-analyze <url> [model]  # 情报分析
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 国内 API（豆包/Tushare/AkShare）不走代理，外网留给各 client 自行设置
# 不再全局清除代理：CLI 子进程（Claude/Gemini/Codex）需要 Clash 代理
os.environ["NO_PROXY"] = "localhost,127.0.0.1,ark.cn-beijing.volces.com,dashscope.aliyuncs.com,open.bigmodel.cn,api.deepseek.com"

BASE_DIR = Path(__file__).resolve().parent

# 确保所有子模块可导入（无论从哪个目录执行）
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

TOP10_REPO_DIR = BASE_DIR / "Stock_top10"

from utils.app_config import get_secret


def _json_out(obj):
    sys.stdout.buffer.write(
        json.dumps(obj, ensure_ascii=False, default=str, indent=2).encode("utf-8")
    )
    sys.stdout.buffer.write(b"\n")


def _active_model() -> str:
    from config import get_active_model
    return get_active_model()


def _smart_model_select() -> str:
    """智能模型选择：scorecard 有推荐就用推荐，否则用用户设置的默认模型。"""
    try:
        from knowledge.analyst_scorecard import get_best_model_for_context
        from knowledge.regime_detector import get_current_regime
        regime = get_current_regime()
        regime_code = regime.get("regime", "") if regime else ""
        recommended = get_best_model_for_context(regime=regime_code)
        if recommended:
            logger.info("[smart_model] scorecard recommends: %s", recommended)
            return recommended
    except Exception:
        pass
    return _active_model()


# ── Markdown → HTML 转换 ─────────────────────────────────────────

def _md_to_html(md_text: str, title: str = "研报") -> str:
    from utils.html_render import md_to_html
    return md_to_html(md_text, title)


def _html_to_image(html_path: str, png_path: str, width: int = 750) -> None:
    from utils.html_render import html_to_image
    html_to_image(html_path, png_path, width)


def _send_email_with_image(to_addr: str, subject: str, body: str, image_path: str) -> None:
    """通过 QQ 邮箱 SMTP 发送内嵌长图的 HTML 邮件。"""
    from utils.email_sender import send_image_email
    send_image_email(subject, body, image_path, to_addr=to_addr)


def cmd_export_image(report_id: str, email: str | None = None):
    """把研报 HTML 截成长图，可选发送到邮箱。"""
    from repositories.report_repo import get_report, init_db

    init_db()
    report = get_report(report_id)
    if report is None:
        _json_out({"status": "not_found", "message": f"报告不存在: {report_id}"})
        return

    stock_name = report["stock_name"]
    safe_name = stock_name.replace(" ", "_")
    out_dir = BASE_DIR / "storage" / "export"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 先生成 HTML
    html = _md_to_html(report["markdown_text"], title=f"{stock_name} 研报")
    html_path = out_dir / f"{safe_name}_{report_id[:8]}.html"
    html_path.write_text(html, encoding="utf-8")

    # 截长图
    png_path = out_dir / f"{safe_name}_{report_id[:8]}.png"
    _html_to_image(str(html_path), str(png_path))

    result = {
        "status": "ok",
        "stock": stock_name,
        "png_path": str(png_path),
        "size_kb": round(png_path.stat().st_size / 1024),
    }

    # 发邮件
    if email:
        created = report.get("created_at", "")[:10]
        subject = f"【{stock_name}】投研报告 {created}"
        body = f"{stock_name} 深度研报（{created}）"
        _send_email_with_image(email, subject, body, str(png_path))
        result["email_sent"] = email

    _json_out(result)


def _save_and_open_html(md_text: str, stock_name: str, report_id: str) -> str:
    from utils.html_render import save_and_open_html
    return save_and_open_html(md_text, stock_name, report_id)


# ── 四野指挥部（多模型并行 + 辩论） ────────────────────────────

def cmd_war_room(stock: str, preset: str = "opus"):
    """指挥部分析：新版(opus/sonnet)两轮深度分析 或 旧版(balanced/max/gemini)多将领模式。"""
    from services.war_room import WAR_ROOM_PRESETS, run_war_room

    if preset not in WAR_ROOM_PRESETS:
        _json_out({"status": "error", "message": f"未知阵容: {preset}", "available": list(WAR_ROOM_PRESETS.keys())})
        return

    result = run_war_room(stock_name=stock, username="cli", preset=preset)

    # 自动生成 HTML 并打开
    html_path = _save_and_open_html(result.combined_markdown, result.stock_name, result.report_id)

    p = WAR_ROOM_PRESETS[preset]
    is_legacy = p.get("_legacy", False)

    if is_legacy:
        # 旧版多将领模式输出
        general_scores = []
        for i, g in enumerate(result.general_reports):
            s = g.get("scores", {})
            general_scores.append({
                "label": f"将领{chr(65+i)}",
                "基本面": s.get("基本面", "?"),
                "预期差": s.get("预期差", "?"),
                "资金面": s.get("资金面", "?"),
                "技术面": s.get("技术面", "?"),
                "综合": s.get("综合加权", "?"),
            })
        _json_out({
            "status": "ok",
            "report_id": result.report_id,
            "stock_name": result.stock_name,
            "stock_code": result.stock_code,
            "preset": preset,
            "preset_label": p["label"],
            "models": {
                "将领A": p["scouts"][0],
                "将领B": p["scouts"][1] if len(p["scouts"]) > 1 else p["scouts"][0],
                "将领C": p["scouts"][2] if len(p["scouts"]) > 2 else p["scouts"][0],
                "林彪": p["commander"],
            },
            "general_scores": general_scores,
            "final_scores": result.final_scores,
            "final_summary": result.final_summary,
            "html_path": html_path,
        })
    else:
        # 新版两轮深度分析输出
        _json_out({
            "status": "ok",
            "report_id": result.report_id,
            "stock_name": result.stock_name,
            "stock_code": result.stock_code,
            "preset": preset,
            "preset_label": p["label"],
            "model": p["analyst"],
            "final_scores": result.final_scores,
            "final_summary": result.final_summary,
            "html_path": html_path,
        })


# ── 单股分析 → 统一走指挥部模式 ──────────────────────────────────

def cmd_analyze(stock: str):
    """单股分析 — Opus两轮深度分析+决策树评分。"""
    cmd_war_room(stock, preset="opus")


# ── K线预测（同步） ──────────────────────────────────────────────

def cmd_kline(stock: str):
    """同步执行K线预测。"""
    from repositories.report_repo import init_db, save_report
    from services.prebuilt_kline_service import (
        build_kline_prediction_report,
        ensure_research_dataset,
    )

    init_db()
    ensure_research_dataset()
    result = build_kline_prediction_report(stock)
    report_id = str(uuid.uuid4())
    save_report(
        report_id=report_id,
        openid="cli",
        stock_name=result["stock_name"],
        stock_code=result["ts_code"],
        summary=result["summary"],
        markdown_text=result["markdown"],
    )
    _json_out({
        "status": "ok",
        "report_id": report_id,
        "stock_name": result["stock_name"],
        "summary": result["summary"],
    })


# ── Top10 ────────────────────────────────────────────────────────

def cmd_top10_query():
    from services import rank_service

    cache_dir = TOP10_REPO_DIR / "cache"
    try:
        snapshot = rank_service.get_latest_rank_snapshot(
            top10_cache_dir=cache_dir, base_url="", limit=10,
        )
    except Exception:
        snapshot = None
    if snapshot is None:
        _json_out({"status": "not_found", "message": "暂无 Top10 结果，可能正在生成中"})
    else:
        _json_out({"status": "ok", "data": snapshot})


def cmd_top10_generate():
    """后台启动 Top10 生成（耗时15-30分钟）。用 top10-progress 查进度。"""
    import subprocess
    from Stock_top10.top10.deep_runner import get_deep_status, is_deep_running

    if is_deep_running() or (get_deep_status() or {}).get("status") == "running":
        _json_out({"status": "running", "message": "Top10 任务已在运行中"})
        return

    # 保留代理环境变量：子进程中 Claude/Gemini CLI 需要 Clash 代理
    # 国内 API 由 NO_PROXY 和各 client 自行处理
    env = {**os.environ, "PYTHONUTF8": "1"}
    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "cli.py"), "_top10_sync"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=0x00000008 | 0x08000000,  # DETACHED_PROCESS | CREATE_NO_WINDOW
        env=env,
    )
    _json_out({
        "status": "accepted",
        "model": "全 Claude MAX 阵容（Sonnet将领+Opus裁决）",
        "message": "Top10 生成已启动（约15-30分钟）。用 top10-progress 查进度。",
    })


def cmd_top10_sync():
    """实际执行 Top10 生成（后台调用）— 使用四野指挥部模式。"""
    from Stock_top10.top10.deep_runner import run_deep_top10

    model = _active_model()
    run_deep_top10(model_name=model, candidate_count=100, username="cli", war_room_preset="max")


def cmd_top10_progress():
    from Stock_top10.top10.deep_runner import get_deep_status

    raw = get_deep_status()
    if not raw:
        _json_out({"status": "idle", "message": "没有运行中的 Top10 任务"})
        return
    _json_out({
        "status": raw.get("status"),
        "scored_count": raw.get("scored_count", 0),
        "total_count": raw.get("total_count", 0),
        "current_stock": raw.get("current_stock"),
        "phase": raw.get("phase"),
        "model": raw.get("model"),
        "started": raw.get("started"),
        "error": raw.get("error"),
    })


# ── Top100 ───────────────────────────────────────────────────────

def cmd_top100_query():
    from services import rank_service

    cache_dir = TOP10_REPO_DIR / "cache"
    try:
        snapshot = rank_service.get_latest_rank_snapshot(
            top10_cache_dir=cache_dir, base_url="", limit=100,
        )
    except Exception:
        snapshot = None
    if snapshot is None:
        _json_out({"status": "not_found", "message": "暂无 Top100 结果"})
    else:
        _json_out({"status": "ok", "data": snapshot})


def cmd_top100_review():
    """后台启动 Top100 复盘。"""
    import subprocess

    env = {**os.environ, "PYTHONUTF8": "1"}
    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "cli.py"), "_top100_review_sync"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=0x00000008 | 0x08000000,  # DETACHED_PROCESS | CREATE_NO_WINDOW
        env=env,
    )
    _json_out({
        "status": "accepted",
        "message": "Top100 复盘已启动（约2-3分钟）。",
    })


def cmd_top100_review_sync():
    """实际执行 Top100 复盘（后台调用）。"""
    from services.top100_review_service import build_latest_top100_review

    build_latest_top100_review()


# ── 舆情 ─────────────────────────────────────────────────────────

def cmd_sentiment_query():
    from services.sentiment_radar import build_radar_summary_text, get_latest_radar

    radar = get_latest_radar()
    if radar is None:
        _json_out({"status": "not_found", "message": "暂无舆情数据"})
    else:
        _json_out({"status": "ok", "summary": build_radar_summary_text(radar), "data": radar})


def cmd_sentiment_generate():
    """后台启动舆情生成。"""
    import subprocess

    env = {**os.environ, "PYTHONUTF8": "1"}
    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "cli.py"), "_sentiment_sync"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=0x00000008 | 0x08000000,  # DETACHED_PROCESS | CREATE_NO_WINDOW
        env=env,
    )
    _json_out({
        "status": "accepted",
        "message": "舆情雷达生成已启动，预计 3-5 分钟完成。",
    })


def cmd_sentiment_sync():
    """实际执行舆情生成（后台调用）。"""
    from services.sentiment_radar import run_sentiment_radar

    model = _active_model()
    run_sentiment_radar(model_name=model)


def cmd_stock_sentiment(stock: str):
    """爬取单只股票的雪球舆情（24h短期 + 2周中线），生成本地网页并输出分析摘要。"""
    from data.tushare_client import resolve_stock
    from data.stock_sentiment import fetch_stock_sentiment, format_sentiment_for_prompt

    ts_code, name, warn = resolve_stock(stock)
    if not ts_code:
        _json_out({"status": "error", "message": f"无法识别股票：{stock}"})
        return

    bundle = fetch_stock_sentiment(ts_code=ts_code, stock_name=name)

    # 生成本地网页
    html_path = None
    try:
        from scripts.archive.test_stock_sentiment import render_sentiment_html
        from data.stock_sentiment import _fetch_stock_posts

        posts_short = _fetch_stock_posts(ts_code, name, hours=24, midterm=False)
        posts_mid = _fetch_stock_posts(ts_code, name, hours=336, midterm=True)
        html = render_sentiment_html(bundle, posts_short, posts_mid)

        out_dir = BASE_DIR / "storage" / "sentiment_test"
        out_dir.mkdir(parents=True, exist_ok=True)
        html_path = out_dir / f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        html_path.write_text(html, encoding="utf-8")
    except Exception:
        pass

    short = bundle.short_term
    mid = bundle.mid_term

    _json_out({
        "status": "ok",
        "stock_name": name,
        "stock_code": ts_code,
        "fetched_at": bundle.fetched_at,
        "short_term": {
            "window": "24小时",
            "posts_count": short.posts_count if short else 0,
            "sentiment": short.sentiment_label if short else "未知",
            "confidence": short.confidence if short else "低",
            "bull_points": short.bull_points if short else [],
            "bear_points": short.bear_points if short else [],
            "catalysts": short.catalysts if short else [],
            "risks": short.risks if short else [],
            "one_liner": short.one_liner if short else "",
            "error": short.error if short else "",
        },
        "mid_term": {
            "window": "2周",
            "posts_count": mid.posts_count if mid else 0,
            "sentiment": mid.sentiment_label if mid else "未知",
            "confidence": mid.confidence if mid else "低",
            "bull_points": mid.bull_points if mid else [],
            "bear_points": mid.bear_points if mid else [],
            "catalysts": mid.catalysts if mid else [],
            "risks": mid.risks if mid else [],
            "one_liner": mid.one_liner if mid else "",
            "error": mid.error if mid else "",
        },
        "prompt_context": format_sentiment_for_prompt(bundle),
        "local_html": str(html_path) if html_path else "",
    })


# ── 报告查询 ─────────────────────────────────────────────────────

def cmd_reports(limit: int = 5):
    import re
    from repositories.report_repo import init_db, list_reports

    init_db()
    reports = list_reports(limit=limit)
    for r in reports:
        summary = r.get("summary", "")
        score_m = re.search(r"(\d+(?:\.\d+)?)\s*分\s*[（(]\s*(\S+?)\s*[）)]", summary)
        if score_m:
            r["score"] = f"{score_m.group(1)}分（{score_m.group(2)}）"
        else:
            r["score"] = "见摘要"
    _json_out({"status": "ok", "count": len(reports), "data": reports})


def cmd_health():
    from data.tushare_client import get_data_source, get_ts_error
    from repositories.report_repo import DB_PATH

    _json_out({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "data_source": get_data_source(),
        "tushare_error": get_ts_error() or None,
        "db_path": str(DB_PATH) if DB_PATH.exists() else "missing",
    })


def cmd_token_balance(model_name: str | None = None):
    from services.token_balance_service import get_token_balance_snapshot

    _json_out(get_token_balance_snapshot(model_name=model_name))


def cmd_read_report(report_id: str):
    from repositories.report_repo import get_report, init_db

    init_db()
    report = get_report(report_id)
    if report is None:
        _json_out({"status": "not_found", "message": f"报告不存在: {report_id}"})
        return

    html_path = _save_and_open_html(report["markdown_text"], report["stock_name"], report_id)

    _json_out({
        "status": "ok",
        "stock": report["stock_name"],
        "code": report["stock_code"],
        "created_at": report["created_at"],
        "html_path": html_path,
        "char_count": len(report["markdown_text"]),
    })


# ── 知识库 ───────────────────────────────────────────────────────

def cmd_knowledge_stats():
    from knowledge.analyst_scorecard import load_scorecard
    from knowledge.outcome_tracker import get_accuracy_summary, get_top100_accuracy
    from knowledge.regime_detector import get_current_regime

    _json_out({
        "regime": get_current_regime(),
        "accuracy": get_accuracy_summary(days=90),
        "top100_accuracy": get_top100_accuracy(days=90),
        "scorecard_summary": {
            k: v for k, v in load_scorecard().items()
            if k in ("sample_count", "directional_count", "overall", "last_updated")
        },
    })


def cmd_knowledge_update():
    """手动触发一次完整的知识库更新。"""
    from knowledge.scheduler import run_knowledge_update

    results = run_knowledge_update()
    _json_out({"status": "ok", "results": results})


# ── 模型管理 ─────────────────────────────────────────────────────

def cmd_set_model(name: str):
    from config import MODEL_CONFIGS, set_active_model

    name_lower = name.lower()
    matched = None
    for key in MODEL_CONFIGS:
        if name_lower in key.lower() or name_lower in MODEL_CONFIGS[key].get("model", "").lower():
            matched = key
            break
    if not matched:
        _json_out({"status": "error", "message": f"未找到匹配的模型: {name}", "available": list(MODEL_CONFIGS.keys())})
        return
    set_active_model(matched)
    cfg = MODEL_CONFIGS[matched]
    _json_out({"status": "ok", "model": matched, "model_id": cfg["model"], "note": cfg.get("note", "")})


def cmd_get_model():
    from config import MODEL_CONFIGS, get_active_model

    current = get_active_model()
    cfg = MODEL_CONFIGS.get(current, {})
    _json_out({"model": current, "model_id": cfg.get("model", "unknown"), "note": cfg.get("note", "")})


def cmd_list_models():
    from config import MODEL_CONFIGS, get_active_model

    current = get_active_model()
    models = []
    for key, cfg in MODEL_CONFIGS.items():
        models.append({
            "name": key,
            "model_id": cfg["model"],
            "note": cfg.get("note", ""),
            "active": key == current,
        })
    _json_out({"status": "ok", "current": current, "models": models})


def cmd_gpt_balance():
    import urllib.request

    url = "https://www.idea.apexmuz.com/api/temporary-token/query"
    gpt_token = get_secret("GPT_BALANCE_TOKEN", "")
    if not gpt_token:
        _json_out({"status": "error", "error": "GPT_BALANCE_TOKEN 未配置"})
        return
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {gpt_token}",
    })
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read())["data"]
    _json_out({
        "status": "ok",
        "remain_usd": data["remain_quota_usd"],
        "used_usd": data["used_quota_usd"],
        "daily_usd": data["daily_quota_usd"],
        "mode": data["quota_mode"],
    })


# ── 雪球热帖 ─────────────────────────────────────────────────────

def cmd_xueqiu_posts():
    """爬取雪球热帖，输出摘要。"""
    from data.xueqiu_radar import fetch_bigv_radar

    posts = fetch_bigv_radar(max_posts=100)
    stock_count = sum(1 for p in posts if p.get("mentioned_stocks"))

    _json_out({
        "status": "ok",
        "count": len(posts),
        "with_stocks": stock_count,
        "message": f"已爬取 {len(posts)} 条热帖（{stock_count} 条提及股票）",
        "top5": [
            {
                "user": p["user_name"],
                "stocks": p.get("mentioned_stocks", [])[:3],
                "likes": p["like_count"],
                "text": p["text"][:80],
            }
            for p in posts[:5]
        ],
    })


# ── 复盘 ─────────────────────────────────────────────────────────

def cmd_review_run(min_days: int = 5, max_batch: int = 50):
    """复盘反思：评估历史报告 + 生成 AI 教训。"""
    results = {}

    try:
        from knowledge.outcome_tracker import evaluate_pending, evaluate_top100_pending
        results["outcomes_evaluated"] = evaluate_pending(min_days=min_days)
        results["top100_outcomes_evaluated"] = evaluate_top100_pending(min_days=min_days)
    except Exception as exc:
        results["outcome_error"] = str(exc)

    try:
        from knowledge.regime_detector import detect_current_regime
        regime = detect_current_regime()
        results["regime"] = regime.get("regime_label", "unknown")
    except Exception as exc:
        results["regime_error"] = str(exc)

    # 循环补全反思（每轮20条，直到处理完或达max_batch上限）
    try:
        from knowledge.reflection import process_pending_reflections
        total_reflections = 0
        while total_reflections < max_batch:
            batch_size = min(20, max_batch - total_reflections)
            batch = process_pending_reflections(max_batch=batch_size)
            total_reflections += batch
            if batch < batch_size:  # 没有更多待处理的了
                break
        results["reflections_generated"] = total_reflections
    except Exception as exc:
        results["reflection_error"] = str(exc)

    try:
        from knowledge.case_memory import get_case_count
        results["total_cases"] = get_case_count()
    except Exception:
        pass

    # 发送复盘邮件汇报
    try:
        from knowledge.scheduler import _send_review_email
        _send_review_email(results)
        results["email_sent"] = True
    except Exception as exc:
        results["email_error"] = str(exc)

    _json_out({"status": "ok", "results": results})


def cmd_review_cases(limit: int = 10):
    """查看最近的复盘教训。"""
    import sqlite3
    from knowledge.kb_db import get_manager

    mgr = get_manager()
    with mgr.read("case_memory") as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT report_date, stock_name, stock_code, regime_label, "
            "score_weighted, direction, outcome_type, return_10d, lesson "
            "FROM cases WHERE lesson IS NOT NULL AND lesson != '' "
            "ORDER BY report_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.row_factory = None

    cases = []
    for r in rows:
        direction_cn = {"bullish": "看多", "bearish": "看空"}.get(r["direction"], "中性")
        mark = {"win": "✅", "loss": "❌", "draw": "➖"}.get(r["outcome_type"], "")
        cases.append({
            "date": r["report_date"],
            "stock": r["stock_name"],
            "regime": r["regime_label"],
            "score": r["score_weighted"],
            "direction": direction_cn,
            "outcome": f"{r['outcome_type']} {mark}",
            "return_10d": r["return_10d"],
            "lesson": r["lesson"],
        })

    _json_out({"status": "ok", "count": len(cases), "cases": cases})


def cmd_review_summary():
    """复盘总览。"""
    import sqlite3
    from knowledge.kb_db import get_manager
    from knowledge.case_memory import get_case_count
    from knowledge.outcome_tracker import get_accuracy_summary
    from knowledge.regime_detector import get_current_regime

    mgr = get_manager()
    total = get_case_count()
    accuracy = get_accuracy_summary(days=90)
    regime = get_current_regime()

    with mgr.read("case_memory") as conn:
        conn.row_factory = sqlite3.Row
        outcome_rows = conn.execute(
            "SELECT outcome_type, COUNT(*) as cnt FROM cases "
            "WHERE lesson IS NOT NULL GROUP BY outcome_type"
        ).fetchall()
        outcome_stats = {r["outcome_type"]: r["cnt"] for r in outcome_rows}

        recent_rows = conn.execute(
            "SELECT report_date, stock_name, score_weighted, direction, "
            "outcome_type, return_10d, lesson FROM cases "
            "WHERE lesson IS NOT NULL AND lesson != '' "
            "ORDER BY report_date DESC LIMIT 3"
        ).fetchall()
        conn.row_factory = None

    recent = []
    for r in recent_rows:
        recent.append({
            "date": r["report_date"],
            "stock": r["stock_name"],
            "score": r["score_weighted"],
            "return_10d": r["return_10d"],
            "lesson": r["lesson"],
        })

    _json_out({
        "status": "ok",
        "total_cases": total,
        "outcome_stats": {
            "win": outcome_stats.get("win", 0),
            "loss": outcome_stats.get("loss", 0),
            "draw": outcome_stats.get("draw", 0),
        },
        "accuracy_90d": accuracy,
        "regime": regime.get("regime_label", "未知") if regime else "未知",
        "recent_lessons": recent,
    })


def cmd_code_review(args: list):
    """三路交叉代码审查（Claude + Gemini + Codex 并行）。"""
    from services.code_review import run_cross_review

    if not args:
        args = ["services/war_room.py"]  # 默认审查指挥部核心文件

    focus = ""
    # 如果最后一个参数不是.py文件，当作审查重点描述
    files = []
    for a in args:
        if a.endswith(".py"):
            files.append(a)
        else:
            focus = a

    results = run_cross_review(files, focus=focus)

    # 输出每路结果
    for reviewer, text in results.items():
        print(f"\n{'='*60}")
        print(f"【{reviewer} 审查结果】")
        print(f"{'='*60}")
        print(text[:3000])

    _json_out({"status": "ok", "reviewers": list(results.keys()), "files": files})


def cmd_review_schedule(args: list):
    """管理每日自动复盘调度。"""
    from knowledge.scheduler import start_scheduled_review, stop_scheduled_review

    if args and args[0] == "stop":
        result = stop_scheduled_review()
    else:
        result = start_scheduled_review()

    _json_out(result)


def cmd_event_recon(event_desc: str):
    """战役侦察令：Phase 1-2（双路侦察+正宗性验证），返回候选清单供 Claude 决策。"""
    from services.event_recon import run_event_recon
    result = run_event_recon(event_desc)
    _json_out(result)


def cmd_event_recon_deep(stock_names_csv: str, preset: str = "balanced"):
    """对指定股票列表执行指挥部深度分析（Phase 4）。后台异步。"""
    import subprocess

    env = {**os.environ, "PYTHONUTF8": "1"}
    # 日志写入文件，便于排查后台进程问题
    log_dir = BASE_DIR / "storage" / "event_recon"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"deep_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_fh = open(str(log_file), "w", encoding="utf-8")
    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "cli.py"), "_event_deep_sync", stock_names_csv, preset],
        stdout=log_fh, stderr=log_fh,
        cwd=str(BASE_DIR),
        creationflags=0x00000008 | 0x08000000,
        env=env,
    )
    names = [n.strip() for n in stock_names_csv.split(",") if n.strip()]
    _json_out({
        "status": "accepted",
        "stocks": names,
        "preset": preset,
        "message": f"指挥部已接令，{len(names)} 只标的开始深度分析（约{len(names)*8}-{len(names)*13}分钟）",
    })


def cmd_event_deep_sync(stock_names_csv: str, preset: str = "balanced"):
    """后台执行指挥部批量分析 + 发邮件汇报。"""
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(message)s",
        handlers=[_logging.StreamHandler()],
    )

    from services.war_room import run_war_room

    print(f"[event_deep] 启动: {stock_names_csv}, preset={preset}", flush=True)

    names = [n.strip() for n in stock_names_csv.split(",") if n.strip()]
    results = []
    for i, name in enumerate(names):
        print(f"[event_deep] [{i+1}/{len(names)}] 指挥部分析: {name}", flush=True)
        try:
            wr = run_war_room(stock_name=name, username="event_recon", preset=preset, skip_extra_recon=True)
            score = wr.final_scores.get("综合加权", 0)
            rating = wr.final_scores.get("_rating", "")
            print(f"[event_deep] [{i+1}/{len(names)}] {name} 完成: {score}分 {rating}", flush=True)
            results.append({
                "stock": wr.stock_name,
                "code": wr.stock_code,
                "score": score,
                "rating": rating,
                "summary": wr.final_summary,
                "report_id": wr.report_id,
            })
        except Exception as exc:
            print(f"[event_deep] [{i+1}/{len(names)}] {name} 失败: {exc}", flush=True)
            import traceback
            traceback.print_exc()
            results.append({"stock": name, "score": 0, "rating": "分析失败", "summary": str(exc)})

    # 按评分排序
    results.sort(key=lambda x: x.get("score", 0), reverse=True)

    # 发邮件
    try:
        _send_event_recon_email(stock_names_csv, results)
    except Exception as exc:
        logger.warning("[event_deep] email failed: %r", exc)

    # 保存结果
    from pathlib import Path
    import json
    out_dir = BASE_DIR / "storage" / "event_recon"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"deep_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def _send_event_recon_email(event_desc: str, results: list):
    """发送战役侦察令深度分析邮件。"""
    from utils.email_sender import send_text_email, smtp_configured

    if not smtp_configured():
        return

    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"四野指挥部·战役侦察令战报", f"事件：{event_desc}", f"日期：{today}", "=" * 40, ""]

    for i, r in enumerate(results, 1):
        score = r.get("score", 0)
        rating = r.get("rating", "")
        lines.append(f"#{i} {r.get('stock', '?')}（{r.get('code', '')}）")
        lines.append(f"  综合评分：{score} — {rating}")
        lines.append(f"  摘要：{r.get('summary', '无')[:150]}")
        lines.append("")

    subject = f"【战役侦察令】{event_desc} Top{len(results)}战报 {today}"
    send_text_email(subject, "\n".join(lines))


# ── 龙头反抽 ─────────────────────────────────────────────────────

def cmd_dragon_scan():
    """Phase 1-3 量化扫描：龙头池构建 → 回调量化 → 止稳信号打分。"""
    from services.dragon_pullback import run_dragon_scan
    candidates = run_dragon_scan(progress_cb=lambda msg: logger.info(msg))
    if not candidates:
        _json_out({"status": "no_candidates", "message": "未找到符合龙头反抽条件的标的"})
        return
    # 输出精简信息
    summary = []
    for c in candidates:
        summary.append({
            "name": c.get("name", ""),
            "code": c["ts_code"].split(".")[0],
            "ts_code": c["ts_code"],
            "streak": c.get("max_streak"),
            "peak_date": c.get("peak_date"),
            "pullback_pct": round(c.get("pullback_pct", 0), 1),
            "days_since_peak": c.get("days_since_peak"),
            "stabilization_score": c.get("stabilization_score", 0),
            "score_detail": c.get("score_detail", ""),
        })
    _json_out({"status": "ok", "count": len(summary), "candidates": summary})


def cmd_dragon_deep(preset: str = "balanced"):
    """Phase 4 AI 深度分析（基于当日已有的扫描结果），后台执行。"""
    from services.dragon_pullback import get_latest_candidates, is_dragon_running

    if is_dragon_running():
        _json_out({"status": "running", "message": "龙头反抽任务已在运行中"})
        return

    candidates = get_latest_candidates()
    if not candidates:
        _json_out({"status": "error", "message": "无当日候选数据，请先执行 dragon-scan"})
        return

    env = {**os.environ, "PYTHONUTF8": "1"}
    log_dir = BASE_DIR / "storage" / "dragon_pullback"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"deep_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_fh = open(str(log_file), "w", encoding="utf-8")
    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "cli.py"), "_dragon_deep_sync", preset],
        stdout=log_fh, stderr=log_fh,
        cwd=str(BASE_DIR),
        creationflags=0x00000008 | 0x08000000,
        env=env,
    )
    _json_out({
        "status": "accepted",
        "candidates_count": len(candidates),
        "preset": preset,
        "message": f"龙头反抽 AI 深度分析已启动（{len(candidates)} 只候选），用 dragon-progress 查看进度",
    })


def cmd_dragon_scan_deep(preset: str = "balanced"):
    """完整 Phase 1-4 流水线，后台执行。"""
    from services.dragon_pullback import is_dragon_running

    if is_dragon_running():
        _json_out({"status": "running", "message": "龙头反抽任务已在运行中"})
        return

    env = {**os.environ, "PYTHONUTF8": "1"}
    log_dir = BASE_DIR / "storage" / "dragon_pullback"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"full_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_fh = open(str(log_file), "w", encoding="utf-8")
    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "cli.py"), "_dragon_full_sync", preset],
        stdout=log_fh, stderr=log_fh,
        cwd=str(BASE_DIR),
        creationflags=0x00000008 | 0x08000000,
        env=env,
    )
    _json_out({
        "status": "accepted",
        "preset": preset,
        "message": "龙头反抽完整扫描已启动（量化筛选+AI深度），用 dragon-progress 查看进度",
    })


def cmd_dragon_progress():
    """查看龙头反抽后台任务进度。"""
    from services.dragon_pullback import get_dragon_status
    status = get_dragon_status()
    if status is None:
        _json_out({"status": "idle", "message": "无运行中的龙头反抽任务"})
    else:
        _json_out(status)


def cmd_dragon_query():
    """查看最新龙头反抽结果。"""
    from services.dragon_pullback import get_latest_result, get_latest_candidates

    result = get_latest_result()
    if result:
        _json_out(result)
        return

    # 没有完整结果，尝试返回候选列表
    candidates = get_latest_candidates()
    if candidates:
        _json_out({
            "status": "partial",
            "message": "仅有量化扫描结果（AI 深度分析尚未完成）",
            "candidates": [
                {
                    "name": c.get("name", ""),
                    "code": c["ts_code"].split(".")[0],
                    "streak": c.get("max_streak"),
                    "pullback_pct": round(c.get("pullback_pct", 0), 1),
                    "stabilization_score": c.get("stabilization_score", 0),
                }
                for c in candidates
            ],
        })
        return

    _json_out({"status": "empty", "message": "无当日龙头反抽数据，请先执行 dragon-scan 或 dragon-scan-deep"})


def cmd_dragon_deep_sync(preset: str = "balanced"):
    """后台执行 Phase 4 AI 深度分析（内部命令）。"""
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from services.dragon_pullback import get_latest_candidates, run_ai_deep, _write_status, _send_dragon_email
    candidates = get_latest_candidates()
    if not candidates:
        _write_status({"status": "error", "phase": "无候选数据"})
        return
    _write_status({"status": "running", "phase": "Phase 4 AI 深度分析",
                    "candidates_count": len(candidates)})
    result = run_ai_deep(candidates, preset,
                         progress_cb=lambda msg: logger.info(msg))
    _send_dragon_email(result)
    _write_status({"status": "done", "phase": "完成",
                    "result_summary": {"ranking": result.get("ranking", [])},
                    "finished": datetime.now().isoformat()})


def cmd_dragon_full_sync(preset: str = "balanced"):
    """后台执行完整 Phase 1-4 流水线（内部命令）。"""
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from services.dragon_pullback import run_dragon_full
    run_dragon_full(preset, progress_cb=lambda msg: logger.info(msg))


def cmd_dragon_backtest(start: str = "", end: str = ""):
    """龙头反抽策略历史回测（后台执行）。"""
    from services.dragon_pullback import is_dragon_running
    if is_dragon_running():
        _json_out({"status": "running", "message": "龙头反抽任务已在运行中"})
        return

    env = {**os.environ, "PYTHONUTF8": "1"}
    log_dir = BASE_DIR / "storage" / "dragon_pullback"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_fh = open(str(log_file), "w", encoding="utf-8")
    args = [sys.executable, str(BASE_DIR / "cli.py"), "_dragon_backtest_sync"]
    if start:
        args.append(start)
    if end:
        args.append(end)
    subprocess.Popen(
        args, stdout=log_fh, stderr=log_fh,
        cwd=str(BASE_DIR),
        creationflags=0x00000008 | 0x08000000,
        env=env,
    )
    _json_out({
        "status": "accepted",
        "message": f"龙头反抽回测已启动{'(' + start + '~' + end + ')' if start else '(近6个月)'}，用 dragon-progress 查看进度",
    })


def cmd_dragon_backtest_sync(start: str = "", end: str = ""):
    """后台执行回测（内部命令）。"""
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from services.dragon_pullback import run_backtest, _write_status
    _write_status({"status": "running", "phase": "历史回测"})
    result = run_backtest(start, end, progress_cb=lambda msg: logger.info(msg))
    _write_status({"status": "done", "phase": "回测完成",
                    "result_summary": result.get("stats", {}),
                    "finished": datetime.now().isoformat()})
    _json_out(result.get("stats", {}))


def cmd_dragon_train(months: str = "6"):
    """龙头反抽四轮自学习（后台执行）。"""
    from services.dragon_pullback import is_dragon_running
    if is_dragon_running():
        _json_out({"status": "running", "message": "龙头反抽任务已在运行中"})
        return

    env = {**os.environ, "PYTHONUTF8": "1"}
    log_dir = BASE_DIR / "storage" / "dragon_pullback"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"train_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_fh = open(str(log_file), "w", encoding="utf-8")
    subprocess.Popen(
        [sys.executable, str(BASE_DIR / "cli.py"), "_dragon_train_sync", months],
        stdout=log_fh, stderr=log_fh,
        cwd=str(BASE_DIR),
        creationflags=0x00000008 | 0x08000000,
        env=env,
    )
    _json_out({
        "status": "accepted",
        "message": f"龙头反抽自学习已启动（{months}个月数据），用 dragon-progress 查看进度",
    })


def cmd_dragon_train_sync(months: str = "6"):
    """后台执行自学习（内部命令）。"""
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from services.dragon_pullback import run_dragon_training, _write_status
    _write_status({"status": "running", "phase": "四轮自学习"})
    result = run_dragon_training(int(months), progress_cb=lambda msg: logger.info(msg))
    _write_status({"status": "done", "phase": "自学习完成",
                    "result_summary": result.get("rounds", {}),
                    "finished": datetime.now().isoformat()})


def cmd_dragon_train_stats():
    """查看龙头反抽训练统计。"""
    from services.dragon_pullback import get_training_stats
    _json_out(get_training_stats())


# ── 抖音素材生成 ─────────────────────────────────────────────────

def cmd_douyin_generate(date: str | None = None):
    """生成抖音短视频素材（竖屏 1080×1920 PNG 图片 + 语音视频）。"""
    from douyin.generator import generate_materials
    result = generate_materials(date)
    _json_out(result)


def cmd_douyin_rebuild(date: str | None = None):
    """读取编辑后的 config.json，重新生成语音和视频。"""
    from douyin.generator import rebuild_video
    result = rebuild_video(date)
    _json_out(result)


# ── 抖音视频学习 ───────────────────────────────────────────────────

def cmd_douyin_learn(source: str, model: str | None = None):
    """学习抖音视频：下载 → 转录 → AI提炼 → 入库。"""
    from douyin_learner.pipeline import run_video_learn

    model_name = model or _active_model()
    print(json.dumps({"status": "processing", "message": "视频学习中（转录+提炼）..."}, ensure_ascii=False))
    sys.stdout.flush()

    result = run_video_learn(source, model_name)

    if result["status"] == "ok":
        # 截图+发邮件
        try:
            analysis = result.get("analysis", "")
            title = result.get("title", "视频学习")[:20]
            entry_id = result.get("entry_id", "")

            html_path = _save_and_open_html(analysis, f"[视频] {title}", entry_id)
            result["html_path"] = html_path

            out_dir = BASE_DIR / "storage" / "export"
            out_dir.mkdir(parents=True, exist_ok=True)
            png_path = out_dir / f"douyin_learn_{entry_id}.png"
            _html_to_image(html_path, str(png_path))

            today = datetime.now().strftime("%Y-%m-%d")
            subject = f"【视频学习】{title} {today}"
            body = f"视频来源：{source}"
            _send_email_with_image("290045045@qq.com", subject, body, str(png_path))
            result["email_sent"] = "290045045@qq.com"
            result["png_path"] = str(png_path)
        except Exception as e:
            logger.warning("视频学习邮件发送失败: %s", e)

    _json_out(result)


def cmd_douyin_learn_bg(source: str, model: str | None = None):
    """后台学习抖音视频（DETACHED_PROCESS）。"""
    args = [sys.executable, str(BASE_DIR / "cli.py"), "_douyin_learn_sync", source]
    if model:
        args.append(model)
    env = {**os.environ, "PYTHONUTF8": "1"}
    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=0x00000008 | 0x08000000,  # DETACHED_PROCESS | CREATE_NO_WINDOW
        env=env,
    )
    _json_out({"status": "accepted", "message": "视频学习任务已启动（后台）", "source": source})


def cmd_douyin_batch(file_path: str):
    """批量学习抖音视频（每行一个URL，后台执行）。"""
    p = Path(file_path)
    if not p.exists():
        _json_out({"status": "error", "message": f"文件不存在: {file_path}"})
        return
    urls = [line.strip() for line in p.read_text("utf-8").splitlines() if line.strip() and not line.startswith("#")]
    if not urls:
        _json_out({"status": "error", "message": "文件中无有效URL"})
        return
    for url in urls:
        cmd_douyin_learn_bg(url)
    _json_out({"status": "accepted", "message": f"已启动 {len(urls)} 个后台学习任务", "urls": urls})


def cmd_douyin_learn_history(days: int = 30):
    """查看已学习的抖音视频列表。"""
    from knowledge.intel_memory import query_recent_intel
    entries = query_recent_intel(days=days)
    videos = [e for e in entries if e.get("title", "").startswith("[视频]")]
    result = {
        "status": "ok",
        "total_videos": len(videos),
        "entries": [],
    }
    for e in videos[:20]:
        result["entries"].append({
            "date": e["analyzed_at"][:10],
            "title": e["title"],
            "themes": e.get("themes", []),
            "sectors": e.get("affected_sectors", []),
            "sentiment": e.get("sentiment", ""),
            "url": e.get("url", ""),
        })
    _json_out(result)


# ── 情报分析 ─────────────────────────────────────────────────────

def cmd_intel_analyze(url: str, model: str | None = None):
    """情报分析：抓取 URL 文章内容，AI 深度分析，生成报告并发邮件。"""
    from services.intel_analyze import run_intel_analyze

    model_name = model or _active_model()
    result = run_intel_analyze(url, model_name)

    if result["status"] == "ok":
        report_id = result["report_id"]
        title = result.get("title", "情报分析")[:20]

        # 生成 HTML 并打开浏览器
        html_path = _save_and_open_html(result["analysis"], title, report_id)
        result["html_path"] = html_path

        # 截图 + 发邮件
        try:
            out_dir = BASE_DIR / "storage" / "export"
            out_dir.mkdir(parents=True, exist_ok=True)
            png_path = out_dir / f"intel_{report_id}.png"
            _html_to_image(html_path, str(png_path))

            today = datetime.now().strftime("%Y-%m-%d")
            subject = f"【情报分析】{title} {today}"
            body = f"情报来源：{url}"
            _send_email_with_image("290045045@qq.com", subject, body, str(png_path))
            result["email_sent"] = "290045045@qq.com"
            result["png_path"] = str(png_path)
        except Exception as e:
            logger.warning("情报分析邮件发送失败: %s", e)

    _json_out(result)


# ── 情报知识库 ────────────────────────────────────────────────────

def cmd_intel_history(days: int = 30):
    """查看情报知识库中的历史条目和活跃主题。"""
    from knowledge.intel_memory import query_recent_intel, get_active_themes, get_intel_count

    total = get_intel_count()
    entries = query_recent_intel(days=days)
    themes = get_active_themes(days=14)

    result = {
        "status": "ok",
        "total_intel": total,
        "recent_entries": [],
        "active_themes": [],
    }

    for e in entries[:20]:
        result["recent_entries"].append({
            "date": e["analyzed_at"][:10],
            "title": e["title"][:40],
            "themes": e["themes"],
            "sentiment": e["sentiment"],
            "implications": e["implications"][:100],
        })

    for t in themes[:10]:
        trend_cn = {"strengthening": "↑增强", "weakening": "↓减弱", "stable": "→稳定",
                    "emerging": "🆕新兴"}.get(t["sentiment_trend"], t["sentiment_trend"])
        result["active_themes"].append({
            "theme": t["theme"],
            "articles": t["article_count"],
            "trend": trend_cn,
            "sectors": t["related_sectors"],
        })

    _json_out(result)


# ── 投资信念 ─────────────────────────────────────────────────────

def cmd_thesis():
    """展示当前投资信念体系。"""
    from knowledge.thesis_journal import get_active_beliefs, get_thesis_md, get_belief_count, BELIEF_CATEGORIES

    beliefs = get_active_beliefs()
    md = get_thesis_md()

    result = {
        "status": "ok",
        "total_beliefs": get_belief_count(),
        "beliefs": [],
        "thesis_md": md,
    }

    for b in beliefs:
        cat_cn = BELIEF_CATEGORIES.get(b["category"], b["category"])
        result["beliefs"].append({
            "category": cat_cn,
            "belief": b["belief"],
            "confidence": f"{b['confidence']*100:.0f}%",
            "evidence": b["evidence_count"],
            "counter": b["counter_evidence"],
            "formed": b["first_formed"][:10],
            "updated": b["last_updated"][:10],
        })

    _json_out(result)


# ── 深度反思 ─────────────────────────────────────────────────────

def cmd_reflection(rtype: str = ""):
    """查看或触发深度反思。rtype: weekly / monthly / 空=查看最新。"""
    from knowledge.deep_reflection import (
        run_weekly_reflection, run_monthly_reflection,
        get_latest_reflection, get_all_reflections,
    )

    if rtype == "weekly":
        result = run_weekly_reflection()
        if result:
            _json_out({"status": "ok", "type": "weekly", **result})
        else:
            _json_out({"status": "skip", "message": "案例不足或本周已有反思"})
        return

    if rtype == "monthly":
        result = run_monthly_reflection()
        if result:
            _json_out({"status": "ok", "type": "monthly", **result})
        else:
            _json_out({"status": "skip", "message": "案例不足或本月已有反思"})
        return

    # 默认：展示最近反思
    reflections = get_all_reflections(limit=5)
    _json_out({
        "status": "ok",
        "reflections": [
            {
                "type": r.get("type"),
                "period": r.get("period"),
                "grade": r.get("self_grade", "?"),
                "narrative": r.get("narrative", "")[:300],
                "biases": r.get("biases_identified", []),
                "focus": r.get("focus_areas", []),
            }
            for r in reflections
        ],
    })


# ── 盘感日记 ─────────────────────────────────────────────────────

def cmd_kline_diary(days: int = 7):
    """查看近期盘感观察记录和形态统计。"""
    from knowledge.kline_diary import get_recent_observations, get_diary_stats

    observations = get_recent_observations(days=days)
    stats = get_diary_stats()

    result = {
        "status": "ok",
        "stats": stats,
        "recent_observations": observations,
    }
    _json_out(result)


def cmd_kline_scan():
    """手动触发盘感扫描（识别关注股票的K线形态）。"""
    from knowledge.kline_diary import scan_and_observe, backtest_pending, rebuild_pattern_stats

    obs = scan_and_observe()
    bt = backtest_pending(days_ago=5)
    rebuild_pattern_stats()  # 内部自动触发 discover_combo_patterns

    _json_out({
        "status": "ok",
        "new_observations": obs,
        "backtested": bt,
        "message": f"扫描完成: {obs}个新观察, {bt}个回溯验证",
    })


def cmd_kline_discoveries():
    """查看自发现的形态组合。"""
    from knowledge.kline_diary import get_discovered_patterns
    discoveries = get_discovered_patterns()
    _json_out({
        "status": "ok",
        "count": len(discoveries),
        "discoveries": [
            {
                "name": d.get("ai_name") or "+".join(d["patterns"]),
                "patterns": d["patterns"],
                "regime": d["regime"], "position": d["position"],
                "volume": d["volume_state"],
                "samples": d["sample_count"],
                "win_rate": d["win_rate_5d"],
                "avg_return": d["avg_return_5d"],
                "explanation": d.get("ai_explanation", ""),
                "verified": d["verified"],
            }
            for d in discoveries
        ],
    })


# ── 投资智慧库 ────────────────────────────────────────────────────

def cmd_wisdom():
    """查看投资智慧库概览。"""
    from knowledge.wisdom import get_wisdom_stats, get_wisdom_md
    stats = get_wisdom_stats()
    md = get_wisdom_md()
    _json_out({"status": "ok", **stats, "wisdom_md": md})


def cmd_wisdom_add(source_type: str, source_name: str, category: str, wisdom_text: str):
    """手动添加一条投资智慧。"""
    from knowledge.wisdom import add_wisdom
    wid = add_wisdom(source_type, source_name, category, wisdom_text)
    _json_out({"status": "ok", "wisdom_id": wid, "message": f"已添加: {wisdom_text[:40]}"})


def cmd_wisdom_learn(url_or_text: str, source_name: str = "", source_type: str = "blog"):
    """从 URL 或文本中自动提炼投资智慧。"""
    if url_or_text.startswith("http"):
        from knowledge.wisdom import learn_from_url
        result = learn_from_url(url_or_text, source_name, source_type)
    else:
        from knowledge.wisdom import batch_extract_from_text
        name = source_name or "手动输入"
        count = batch_extract_from_text(url_or_text, name, source_type)
        result = {"status": "ok", "source_name": name, "extracted": count}

    _json_out(result)


def cmd_wisdom_search(query: str):
    """搜索智慧库。"""
    from knowledge.wisdom import search_wisdom
    results = search_wisdom(query)
    _json_out({
        "status": "ok",
        "count": len(results),
        "results": [
            {"source": r["source_name"], "category": r["category"],
             "wisdom": r["wisdom"], "used": r["used_count"]}
            for r in results
        ],
    })


# ── 模拟训练 ─────────────────────────────────────────────────────

def cmd_sim_train(count: int = 5, sector: str = ""):
    """AlphaGo式模拟训练：历史数据→分析→判卷→学习。"""
    from knowledge.simulation_training import run_simulation_training
    result = run_simulation_training(count=count, sector_focus=sector, delay_between=30)
    _json_out(result)


def cmd_sim_stats():
    """查看模拟训练累计统计。"""
    from knowledge.simulation_training import get_simulation_stats
    _json_out({"status": "ok", **get_simulation_stats()})


# ── 夜间学习 ─────────────────────────────────────────────────────

def cmd_night_learn(phase: str = "all"):
    """夜间自进化学习。phase: all/scan/ai/report"""
    from knowledge.night_learner import run_night_learning
    result = run_night_learning(phase=phase)
    _json_out({"status": "ok", **result})


# ── 新闻监控 ─────────────────────────────────────────────────────

def cmd_news_scan(max_analyze: int = 3):
    """扫描财经新闻源，对相关新闻自动执行情报分析。"""
    from data.news_monitor import scan_news_sources
    result = scan_news_sources(max_analyze=max_analyze)
    _json_out({"status": "ok", **result})


# ── 会话交接 ─────────────────────────────────────────────────────

def cmd_session_snapshot(summary: str = ""):
    """保存会话摘要并重新生成 STATE.md。"""
    from knowledge.session_handoff import save_session_summary, generate_state_md

    if summary:
        save_session_summary(summary)
        _json_out({"status": "ok", "message": f"会话摘要已保存，STATE.md 已更新"})
    else:
        # 无摘要时只重新生成 STATE.md
        content = generate_state_md()
        _json_out({"status": "ok", "message": "STATE.md 已重新生成", "chars": len(content)})


def cmd_regenerate_state():
    """强制重新生成 STATE.md（不保存会话摘要）。"""
    from knowledge.session_handoff import generate_state_md
    content = generate_state_md()
    _json_out({"status": "ok", "chars": len(content), "path": "data/knowledge/STATE.md"})


# ── 持仓与风控 ─────────────────────────────────────────────────────

def cmd_portfolio_add(stock: str, price: str, shares: str, stop_loss: str = "0", take_profit: str = "0"):
    """建仓记录。"""
    from data.tushare_client import resolve_stock
    from portfolio.models import Position
    from portfolio.store import add_position

    ts_code, name, warn = resolve_stock(stock)
    if not ts_code:
        _json_out({"status": "error", "message": f"无法识别股票：{stock}"})
        return

    pos = Position(
        stock_code=ts_code,
        stock_name=name,
        entry_price=float(price),
        entry_date=datetime.now().strftime("%Y-%m-%d"),
        shares=int(shares),
        stop_loss=float(stop_loss),
        take_profit=float(take_profit),
        thesis="",
    )
    pid = add_position(pos)
    _json_out({
        "status": "ok",
        "position_id": pid,
        "stock_name": name,
        "entry_price": pos.entry_price,
        "shares": pos.shares,
        "stop_loss": pos.stop_loss,
        "take_profit": pos.take_profit,
    })


def cmd_portfolio_list():
    """查看当前持仓。"""
    from portfolio.risk import check_portfolio_risks
    from portfolio.store import get_open_positions

    positions = get_open_positions()
    if not positions:
        _json_out({"status": "ok", "message": "当前无持仓", "positions": [], "alerts": []})
        return

    result = check_portfolio_risks(positions)
    _json_out({"status": "ok", **result})


def cmd_portfolio_risk():
    """风险扫描。"""
    from portfolio.risk import check_portfolio_risks
    from portfolio.store import get_open_positions

    positions = get_open_positions()
    if not positions:
        _json_out({"status": "ok", "message": "当前无持仓，无风险", "alerts": []})
        return

    result = check_portfolio_risks(positions)

    # 有 critical 告警时发邮件
    if result["critical_count"] > 0:
        try:
            from utils.email_sender import send_text_email, smtp_configured
            if smtp_configured():
                lines = ["【风控告警】持仓风险扫描", "=" * 40, ""]
                for a in result["alerts"]:
                    icon = {"critical": "🔴", "warning": "🟡", "info": "🟢"}.get(a["level"], "")
                    lines.append(f"{icon} [{a['level']}] {a['stock']}: {a['message']}")
                    if a.get("detail"):
                        lines.append(f"    {a['detail']}")
                    lines.append("")
                send_text_email(f"【风控告警】{result['critical_count']}条严重告警", "\n".join(lines))
                result["email_sent"] = True
        except Exception:
            pass

    _json_out({"status": "ok", **result})


def cmd_portfolio_close(stock: str, price: str = "0", reason: str = ""):
    """平仓记录。"""
    from portfolio.store import close_position, get_open_positions

    positions = get_open_positions()
    matched = None
    for p in positions:
        if stock in p["stock_name"] or stock in p["stock_code"] or stock == p["position_id"]:
            matched = p
            break

    if not matched:
        _json_out({"status": "error", "message": f"未找到持仓：{stock}"})
        return

    try:
        close_price = float(price) if price else 0.0
    except ValueError:
        _json_out({"status": "error", "message": f"平仓价格格式错误：{price}"})
        return
    if close_price <= 0:
        # 未指定价格时尝试获取最新市价，失败则用建仓价
        try:
            from portfolio.risk import _get_latest_price
            latest = _get_latest_price(matched["stock_code"])
            close_price = latest if latest else matched["entry_price"]
        except Exception:
            close_price = matched["entry_price"]
    ok = close_position(
        matched["position_id"], close_price,
        datetime.now().strftime("%Y-%m-%d"), reason,
    )

    pnl = (close_price - matched["entry_price"]) * matched["shares"]
    pnl_pct = (close_price - matched["entry_price"]) / matched["entry_price"] * 100

    _json_out({
        "status": "ok" if ok else "error",
        "position_id": matched["position_id"],
        "stock_name": matched["stock_name"],
        "entry_price": matched["entry_price"],
        "close_price": close_price,
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 1),
        "reason": reason,
    })


def cmd_portfolio_history(limit: int = 20):
    """查看持仓历史（含已平仓）。"""
    from portfolio.store import get_all_positions

    positions = get_all_positions(limit=limit)
    _json_out({"status": "ok", "count": len(positions), "positions": positions})


def cmd_review(args: list):
    """批量复盘：对比分析预测与实际走势，生成经验条目。

    Usage:
        python cli.py review                         # 最近7天
        python cli.py review --from 2026-04-01 --to 2026-04-10
        python cli.py review 宁德时代                 # 单只股票
    """
    from knowledge.batch_reviewer import run_batch_review

    date_from = None
    date_to = None
    stock_name = None

    i = 0
    while i < len(args):
        if args[i] == "--from" and i + 1 < len(args):
            date_from = args[i + 1]
            i += 2
        elif args[i] == "--to" and i + 1 < len(args):
            date_to = args[i + 1]
            i += 2
        else:
            stock_name = args[i]
            i += 1

    result = run_batch_review(
        stock_name=stock_name,
        date_from=date_from,
        date_to=date_to,
    )
    _json_out(result)


def cmd_apply_weights(args: list):
    """应用最新的权重调节建议。"""
    import json as _json
    from pathlib import Path as _Path
    reports_dir = _Path("C:/LinDangAgent/data/knowledge/evolution_reports")
    if not reports_dir.exists():
        _json_out({"status": "no_reports_dir"})
        return
    reports = sorted(reports_dir.glob("*_health.json"), reverse=True)
    for rp in reports:
        with open(rp, encoding="utf-8") as f:
            report = _json.load(f)
        proposal = report.get("weight_proposal")
        if proposal and proposal.get("status") == "pending_approval":
            print(f"找到待审批建议: {rp.name}")
            print(_json.dumps(proposal, ensure_ascii=False, indent=2))
            from knowledge.evolution_engine import apply_weight_change
            apply_weight_change(proposal["new_weights"])
            proposal["status"] = "applied"
            with open(rp, "w", encoding="utf-8") as f:
                _json.dump(report, f, ensure_ascii=False, indent=2)
            _json_out({"status": "applied", "new_weights": proposal["new_weights"]})
            return
    _json_out({"status": "no_pending_proposal"})


# ── 命令路由 ─────────────────────────────────────────────────────

COMMANDS = {
    "analyze": lambda args: cmd_analyze(args[0]) if args else print("用法: python cli.py analyze <股票名>"),
    "war-room": lambda args: cmd_war_room(args[0], args[1] if len(args) > 1 else "gemini") if args else print("用法: python cli.py war-room <股票名> [阵容: gemini/codex/doubao/mixed]"),
    "kline": lambda args: cmd_kline(args[0]) if args else print("用法: python cli.py kline <股票名或代码>"),
    "top10-query": lambda args: cmd_top10_query(),
    "top10-generate": lambda args: cmd_top10_generate(),
    "top10-progress": lambda args: cmd_top10_progress(),
    "top100-query": lambda args: cmd_top100_query(),
    "top100-review": lambda args: cmd_top100_review(),
    "sentiment-query": lambda args: cmd_sentiment_query(),
    "sentiment-generate": lambda args: cmd_sentiment_generate(),
    "stock-sentiment": lambda args: cmd_stock_sentiment(args[0]) if args else print("用法: python cli.py stock-sentiment <股票名>"),
    "reports": lambda args: cmd_reports(int(args[0]) if args else 5),
    "health": lambda args: cmd_health(),
    "token-balance": lambda args: cmd_token_balance(args[0] if args else None),
    "knowledge-stats": lambda args: cmd_knowledge_stats(),
    "knowledge-update": lambda args: cmd_knowledge_update(),
    "read-report": lambda args: cmd_read_report(args[0]) if args else print("用法: python cli.py read-report <report_id>"),
    "export-image": lambda args: cmd_export_image(args[0], args[1] if len(args) > 1 else None) if args else print("用法: python cli.py export-image <report_id> [email]"),
    "set-model": lambda args: cmd_set_model(" ".join(args)) if args else print("用法: python cli.py set-model <模型名>"),
    "get-model": lambda args: cmd_get_model(),
    "list-models": lambda args: cmd_list_models(),
    "gpt-balance": lambda args: cmd_gpt_balance(),
    "xueqiu-posts": lambda args: cmd_xueqiu_posts(),
    "review": lambda args: cmd_review(args),
    "review-run": lambda args: cmd_review_run(int(args[0]) if args else 5),
    "review-cases": lambda args: cmd_review_cases(int(args[0]) if args else 10),
    "review-summary": lambda args: cmd_review_summary(),
    "review-schedule": lambda args: cmd_review_schedule(args),
    "code-review": lambda args: cmd_code_review(args) if args else cmd_code_review(["services/war_room.py"]),
    "event-recon": lambda args: cmd_event_recon(" ".join(args)) if args else print("用法: python cli.py event-recon <事件描述>"),
    "event-recon-deep": lambda args: cmd_event_recon_deep(args[0], args[1] if len(args) > 1 else "gemini") if args else print("用法: python cli.py event-recon-deep <股票1,股票2,...> [阵容]"),
    "douyin-generate": lambda args: cmd_douyin_generate(args[0] if args else None),
    "douyin-rebuild": lambda args: cmd_douyin_rebuild(args[0] if args else None),
    "douyin-learn": lambda args: cmd_douyin_learn(args[0], args[1] if len(args) > 1 else None) if args else print("用法: python cli.py douyin-learn <url|path> [model]"),
    "douyin-learn-bg": lambda args: cmd_douyin_learn_bg(args[0], args[1] if len(args) > 1 else None) if args else print("用法: python cli.py douyin-learn-bg <url|path> [model]"),
    "douyin-batch": lambda args: cmd_douyin_batch(args[0]) if args else print("用法: python cli.py douyin-batch <url_list_file>"),
    "douyin-learn-history": lambda args: cmd_douyin_learn_history(int(args[0]) if args else 30),
    "intel-analyze": lambda args: cmd_intel_analyze(args[0], args[1] if len(args) > 1 else None) if args else print("用法: python cli.py intel-analyze <url> [model]"),
    "intel-history": lambda args: cmd_intel_history(int(args[0]) if args else 30),
    "thesis": lambda args: cmd_thesis(),
    "reflection": lambda args: cmd_reflection(args[0] if args else ""),
    "kline-diary": lambda args: cmd_kline_diary(int(args[0]) if args else 7),
    "kline-scan": lambda args: cmd_kline_scan(),
    "kline-discoveries": lambda args: cmd_kline_discoveries(),
    "wisdom": lambda args: cmd_wisdom(),
    "wisdom-add": lambda args: cmd_wisdom_add(args[0], args[1], args[2], " ".join(args[3:])) if len(args) >= 4 else print("用法: wisdom-add <类型:book/blog/video> <来源名> <分类:valuation/timing/risk/psychology/sector/general> <智慧内容>"),
    "wisdom-learn": lambda args: cmd_wisdom_learn(args[0], args[1] if len(args) > 1 else "", args[2] if len(args) > 2 else "blog") if args else print("用法: wisdom-learn <URL或文本> [来源名] [类型]"),
    "wisdom-search": lambda args: cmd_wisdom_search(" ".join(args)) if args else print("用法: wisdom-search <关键词>"),
    "sim-train": lambda args: cmd_sim_train(int(args[0]) if args else 5, args[1] if len(args) > 1 else ""),
    "sim-stats": lambda args: cmd_sim_stats(),
    "night-learn": lambda args: cmd_night_learn(args[0] if args else "all"),
    "news-scan": lambda args: cmd_news_scan(int(args[0]) if args else 3),
    "session-snapshot": lambda args: cmd_session_snapshot(" ".join(args) if args else ""),
    "regenerate-state": lambda args: cmd_regenerate_state(),
    "apply-weights": lambda args: cmd_apply_weights(args),
    # ── 持仓与风控 ──
    "portfolio-add": lambda args: cmd_portfolio_add(args[0], args[1], args[2], args[3] if len(args) > 3 else "0", args[4] if len(args) > 4 else "0") if len(args) >= 3 else print("用法: portfolio-add <股票> <价格> <数量> [止损] [止盈]"),
    "portfolio-list": lambda args: cmd_portfolio_list(),
    "portfolio-risk": lambda args: cmd_portfolio_risk(),
    "portfolio-close": lambda args: cmd_portfolio_close(args[0], args[1] if len(args) > 1 else "0", args[2] if len(args) > 2 else "") if args else print("用法: portfolio-close <股票或ID> [平仓价] [原因]"),
    "portfolio-history": lambda args: cmd_portfolio_history(int(args[0]) if args else 20),
    # ── 龙头反抽 ──
    "dragon-scan": lambda args: cmd_dragon_scan(),
    "dragon-deep": lambda args: cmd_dragon_deep(args[0] if args else "balanced"),
    "dragon-scan-deep": lambda args: cmd_dragon_scan_deep(args[0] if args else "balanced"),
    "dragon-progress": lambda args: cmd_dragon_progress(),
    "dragon-query": lambda args: cmd_dragon_query(),
    "dragon-backtest": lambda args: cmd_dragon_backtest(args[0] if args else "", args[1] if len(args) > 1 else ""),
    "dragon-train": lambda args: cmd_dragon_train(args[0] if args else "6"),
    "dragon-train-stats": lambda args: cmd_dragon_train_stats(),
    # 内部后台命令（不对外暴露）
    "_top10_sync": lambda args: cmd_top10_sync(),
    "_top100_review_sync": lambda args: cmd_top100_review_sync(),
    "_sentiment_sync": lambda args: cmd_sentiment_sync(),
    "_event_deep_sync": lambda args: cmd_event_deep_sync(args[0], args[1] if len(args) > 1 else "gemini") if args else None,
    "_douyin_learn_sync": lambda args: cmd_douyin_learn(args[0], args[1] if len(args) > 1 else None) if args else None,
    "_dragon_deep_sync": lambda args: cmd_dragon_deep_sync(args[0] if args else "balanced"),
    "_dragon_full_sync": lambda args: cmd_dragon_full_sync(args[0] if args else "balanced"),
    "_dragon_backtest_sync": lambda args: cmd_dragon_backtest_sync(args[0] if args else "", args[1] if len(args) > 1 else ""),
    "_dragon_train_sync": lambda args: cmd_dragon_train_sync(args[0] if args else "6"),
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        visible = [k for k in COMMANDS if not k.startswith("_")]
        print("可用命令:", ", ".join(visible))
        sys.exit(1)
    try:
        COMMANDS[sys.argv[1]](sys.argv[2:])
    except Exception as exc:
        _json_out({"status": "error", "message": str(exc)})
        sys.exit(1)
