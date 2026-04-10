# -*- coding: utf-8 -*-
"""夜间自进化引擎 — 林铛在你睡觉时自我提升

三轮夜间学习流水线：
  第一轮 22:00 数据扫描（零 AI 成本）
    - 全市场异动股 K 线形态扫描
    - 新闻抓取
    - 智慧验证（302条×历史案例交叉统计）

  第二轮 00:00 AI 学习（Claude Sonnet）
    - 重大新闻 intel-analyze
    - 弱项识别 → 搜索学习
    - 模拟复盘（历史失败案例用当前知识重新分析）

  第三轮 04:00 总结
    - 生成夜间学习报告
    - 更新 STATE.md
    - 发邮件

用法：
  python cli.py night-learn           # 手动触发完整夜间学习
  python cli.py night-learn scan      # 只跑第一轮（数据扫描）
  python cli.py night-learn ai        # 只跑第二轮（AI学习）
  python cli.py night-learn report    # 只跑第三轮（总结报告）
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from knowledge.kb_config import BASE_DIR, DIRECTION_CN, KNOWLEDGE_DIR, NIGHT_REPORT_DIR

logger = logging.getLogger(__name__)

REPORT_DIR = NIGHT_REPORT_DIR


# ══════════════════════════════════════════════════════════════════
# 第一轮：数据扫描（零 AI 成本）
# ══════════════════════════════════════════════════════════════════

def round1_scan() -> dict:
    """第一轮：全市场扫描 + 智慧验证。纯 Python，零 AI 成本。"""
    results = {"round": 1, "timestamp": datetime.now().isoformat(timespec="seconds")}

    # 1. 扩大盘感扫描：全市场异动股
    try:
        scan_count = _scan_market_movers()
        results["market_scan"] = scan_count
        logger.info("[night] market scan: %d stocks observed", scan_count)
    except Exception as exc:
        logger.warning("[night] market scan failed: %r", exc)
        results["market_scan_error"] = str(exc)

    # 2. 新闻抓取
    try:
        from data.news_monitor import scan_news_sources
        news = scan_news_sources(max_analyze=0)  # 只抓取不分析（AI留给第二轮）
        results["news_scanned"] = news.get("scanned", 0)
        results["news_relevant"] = news.get("new_relevant", 0)
        logger.info("[night] news: %d scanned, %d relevant", news.get("scanned", 0), news.get("new_relevant", 0))
    except Exception as exc:
        logger.warning("[night] news scan failed: %r", exc)

    # 3. 智慧验证
    try:
        validation = _validate_wisdom_entries()
        results["wisdom_validation"] = validation
        logger.info("[night] wisdom validation: %d validated", validation.get("validated", 0))
    except Exception as exc:
        logger.warning("[night] wisdom validation failed: %r", exc)

    # 4. 盘感回溯验证
    try:
        from knowledge.kline_diary import backtest_pending, rebuild_pattern_stats
        bt = backtest_pending(days_ago=5)
        rebuild_pattern_stats()
        results["kline_backtested"] = bt
    except Exception as exc:
        logger.debug("[night] kline backtest: %r", exc)

    # Channel B: Evolution engine backtesting
    try:
        from knowledge.evolution_engine import run_nightly_backtest, send_weight_proposal_email
        backtest_result = run_nightly_backtest()
        if backtest_result.get("weight_proposal"):
            send_weight_proposal_email(backtest_result["weight_proposal"])
            logger.info("Weight proposal generated and emailed")
        results["backtest"] = backtest_result
    except Exception as e:
        logger.warning("Nightly backtest failed: %s", e)

    return results


def _scan_market_movers() -> int:
    """扫描全市场异动股（涨幅前50+跌幅前50），识别K线形态。"""
    from knowledge.kb_db import get_manager
    from knowledge.kline_patterns import detect_all_patterns, classify_position, classify_volume_state
    import uuid

    # 尝试获取全市场行情
    movers = []
    try:
        from data.tushare_client import get_price_df
        import akshare as ak

        # 用 akshare 获取当日涨幅排名（不依赖 Tushare 积分）
        try:
            df_rise = ak.stock_zh_a_spot_em()
            if df_rise is not None and len(df_rise) > 0:
                # 取涨幅前 30 + 跌幅前 20
                df_rise = df_rise.sort_values("涨跌幅", ascending=False)
                top_rise = df_rise.head(30)[["代码", "名称"]].values.tolist()
                top_fall = df_rise.tail(20)[["代码", "名称"]].values.tolist()
                for code, name in top_rise + top_fall:
                    # 转换为 tushare 格式
                    if code.startswith("6"):
                        ts_code = f"{code}.SH"
                    elif code.startswith(("0", "3")):
                        ts_code = f"{code}.SZ"
                    else:
                        continue
                    movers.append((ts_code, str(name)))
        except Exception as exc:
            logger.debug("[night] akshare spot failed: %r", exc)
    except ImportError:
        pass

    if not movers:
        logger.info("[night] no market movers data available")
        return 0

    # 对每只异动股识别形态
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat(timespec="seconds")
    observed = 0

    regime = "shock"
    try:
        from knowledge.regime_detector import get_current_regime
        r = get_current_regime()
        regime = r.get("regime", "shock") if r else "shock"
    except Exception as exc:
        logger.debug("[night_learner] regime fetch failed: %r", exc)

    from knowledge.kline_diary import _generate_prediction
    from data.tushare_client import get_price_df

    mgr = get_manager()

    for ts_code, name in movers[:50]:  # 最多处理50只
        try:
            # 检查今日是否已观察
            with mgr.read("kline_diary") as conn:
                exists = conn.execute(
                    "SELECT 1 FROM kline_observations WHERE date=? AND stock_code=?",
                    (today, ts_code),
                ).fetchone()

            if exists:
                continue

            df, _err = get_price_df(ts_code, days=60)
            if df is None or len(df) < 10:
                continue

            patterns = detect_all_patterns(df)
            if not patterns:
                continue

            position = classify_position(df)
            volume_state = classify_volume_state(df)
            pattern_ids = [p.pattern_id for p in patterns]
            prediction, confidence = _generate_prediction(pattern_ids, regime, position)

            obs_id = str(uuid.uuid4())[:8]
            with mgr.write("kline_diary") as conn:
                conn.execute(
                    "INSERT INTO kline_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (obs_id, today, ts_code, name,
                     json.dumps(pattern_ids), regime, position, volume_state,
                     prediction, confidence, None, None, now),
                )

            observed += 1
        except Exception as exc:
            logger.debug("[night_learner] stock scan failed: %r", exc)
            continue

    return observed


def _validate_wisdom_entries() -> dict:
    """用历史案例验证智慧条目的有效性。"""
    from knowledge.wisdom import get_all_wisdom
    from knowledge.kb_db import get_manager

    all_wisdom = get_all_wisdom()
    if not all_wisdom:
        return {"validated": 0}

    # 加载所有有结果的案例（含板块标签）
    mgr = get_manager()
    with mgr.read("case_memory") as conn:
        cases = conn.execute(
            "SELECT c.case_id, c.stock_name, c.regime, c.direction, c.score_weighted, "
            "c.return_10d, c.outcome_type, c.lesson "
            "FROM cases c WHERE c.return_10d IS NOT NULL"
        ).fetchall()

        # 加载标签映射
        tag_map = {}
        tag_rows = conn.execute(
            "SELECT case_id, tag_value FROM case_tags WHERE tag_type='sector'"
        ).fetchall()
        for cid, tv in tag_rows:
            tag_map.setdefault(cid, []).append(tv)

    if len(cases) < 10:
        return {"validated": 0, "reason": "insufficient cases"}

    total_cases = len(cases)
    wins = sum(1 for c in cases if c[6] == "win")
    baseline_rate = wins / total_cases * 100 if total_cases > 0 else 50

    # 简单验证：检查智慧中提到的关键词是否与高胜率案例关联
    validated = 0
    high_value = []
    low_value = []

    for w in all_wisdom:
        tags = w.get("tags", [])
        if not tags:
            continue

        # 找包含该标签的案例
        relevant_cases = []
        for c in cases:
            case_id = c[0]
            case_tags = tag_map.get(case_id, [])
            case_text = f"{c[1]} {c[7] or ''} {' '.join(case_tags)}"
            if any(t in case_text for t in tags):
                relevant_cases.append(c)

        if len(relevant_cases) >= 3:
            case_wins = sum(1 for c in relevant_cases if c[6] == "win")
            case_rate = case_wins / len(relevant_cases) * 100

            validated += 1
            entry = {
                "wisdom_id": w["wisdom_id"],
                "wisdom": w["wisdom"][:50],
                "source": w["source_name"],
                "relevant_cases": len(relevant_cases),
                "win_rate": round(case_rate, 1),
                "vs_baseline": round(case_rate - baseline_rate, 1),
            }

            if case_rate > baseline_rate + 10:
                high_value.append(entry)
            elif case_rate < baseline_rate - 10:
                low_value.append(entry)

    return {
        "validated": validated,
        "baseline_rate": round(baseline_rate, 1),
        "high_value_count": len(high_value),
        "low_value_count": len(low_value),
        "top_high_value": sorted(high_value, key=lambda x: x["win_rate"], reverse=True)[:5],
        "top_low_value": sorted(low_value, key=lambda x: x["win_rate"])[:5],
    }


# ══════════════════════════════════════════════════════════════════
# 第二轮：AI 学习（Claude Sonnet）
# ══════════════════════════════════════════════════════════════════

def round2_ai_learn(round1_results: dict = None) -> dict:
    """第二轮：AI 驱动的学习。需要 Claude Sonnet。"""
    results = {"round": 2, "timestamp": datetime.now().isoformat(timespec="seconds")}

    # 1. 对重要新闻执行 intel-analyze
    try:
        from data.news_monitor import scan_news_sources
        news = scan_news_sources(max_analyze=3)  # 分析最多3篇
        results["news_analyzed"] = news.get("analyzed", 0)
        results["articles"] = news.get("articles", [])
    except Exception as exc:
        logger.warning("[night] news analyze failed: %r", exc)

    # 2. 弱项识别 + 搜索学习
    try:
        weak = _identify_and_study_weaknesses()
        results["weakness_study"] = weak
    except Exception as exc:
        logger.warning("[night] weakness study failed: %r", exc)

    # 3. 模拟复盘（旧版，保留兼容）
    try:
        replay = _simulate_replay(max_cases=2)
        results["replay"] = replay
    except Exception as exc:
        logger.warning("[night] replay failed: %r", exc)

    # 4. AlphaGo 式模拟训练（核心学习引擎）
    try:
        from knowledge.simulation_training import run_simulation_training
        sim = run_simulation_training(count=5, delay_between=60)  # 夜间节奏慢一些
        results["simulation"] = {
            "trained": sim.get("total_trained", 0),
            "hit_rate": sim.get("hit_rate", 0),
        }
        logger.info("[night] simulation training: %d trained, %.1f%% hit rate",
                    sim.get("total_trained", 0), sim.get("hit_rate", 0))
    except Exception as exc:
        logger.warning("[night] simulation training failed: %r", exc)

    return results


def _identify_and_study_weaknesses() -> dict:
    """识别近期弱项并尝试搜索学习。"""
    from knowledge.kb_db import get_manager

    # 找近30天亏损最多的案例（标签从 case_tags 表获取）
    mgr = get_manager()
    with mgr.read("case_memory") as conn:
        losses = conn.execute(
            "SELECT c.case_id, c.stock_name, c.regime_label, c.lesson, c.return_10d "
            "FROM cases c WHERE c.outcome_type='loss' AND c.created_at >= ? "
            "ORDER BY c.return_10d ASC LIMIT 10",
            ((datetime.now() - timedelta(days=30)).isoformat(),),
        ).fetchall()

        # 获取这些案例的板块标签
        loss_tags = {}
        if losses:
            case_ids = [l[0] for l in losses]
            placeholders = ",".join("?" * len(case_ids))
            tag_rows = conn.execute(
                f"SELECT case_id, tag_value FROM case_tags WHERE tag_type='sector' "
                f"AND case_id IN ({placeholders})",
                case_ids,
            ).fetchall()
            for cid, tv in tag_rows:
                loss_tags.setdefault(cid, []).append(tv)

    if not losses:
        return {"status": "no recent losses"}

    # 统计亏损板块
    sector_losses = {}
    for case_id, stock, regime, lesson, ret in losses:
        tags = loss_tags.get(case_id, [])
        for tag in tags:
            sector_losses.setdefault(tag, []).append(ret)

    # 找最弱板块
    worst_sectors = sorted(
        [(tag, len(rets), sum(rets) / len(rets))
         for tag, rets in sector_losses.items() if len(rets) >= 2],
        key=lambda x: x[2],
    )[:3]

    if not worst_sectors:
        return {"status": "no clear weak sector"}

    # 尝试搜索相关文章学习
    studied = []
    for tag, count, avg_ret in worst_sectors:
        try:
            from data.news_monitor import fetch_cls_telegraph
            articles = fetch_cls_telegraph(limit=10)
            relevant = [a for a in articles if tag in a.get("title", "") or tag in a.get("summary", "")]
            if relevant:
                studied.append({
                    "sector": tag,
                    "loss_count": count,
                    "avg_loss": round(avg_ret, 1),
                    "articles_found": len(relevant),
                })
        except Exception as exc:
            logger.debug("[night_learner] weakness study failed: %r", exc)

    return {
        "worst_sectors": [{"sector": s[0], "losses": s[1], "avg_return": round(s[2], 1)} for s in worst_sectors],
        "studied": studied,
    }


def _simulate_replay(max_cases: int = 2) -> dict:
    """模拟复盘：取历史失败案例用当前知识重新分析。"""
    import sqlite3
    from ai.client import call_ai, get_ai_client

    db_path = KNOWLEDGE_DIR / "case_memory.db"
    if not db_path.exists():
        return {"status": "no case database"}

    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        failed = conn.execute(
            "SELECT case_id, stock_name, stock_code, report_date, regime_label, "
            "score_weighted, direction, return_10d, lesson "
            "FROM cases WHERE outcome_type='loss' "
            "ORDER BY created_at DESC LIMIT ?",
            (max_cases,),
        ).fetchall()
    finally:
        conn.close()

    if not failed:
        return {"status": "no failed cases to replay"}

    replays = []
    model_name = "⚡ Claude Sonnet（MAX）"
    client, cfg, err = get_ai_client(model_name)
    if err and not cfg:
        return {"status": f"model unavailable: {err}"}

    cfg_no_search = {**cfg, "supports_search": False}

    for case in failed:
        case_id, name, code, date, regime, score, direction, ret, lesson = case
        dir_cn = DIRECTION_CN.get(direction, "中性")

        prompt = f"""我在 {date} 分析了 {name}（{code}），当时{regime}环境，给了{score}分{dir_cn}。
实际结果：10日收益{ret:+.1f}%，判断失误。
当时的教训：{lesson or '无'}

请用 3-5 句话重新审视这个案例：
1. 以现在的认知来看，当时错在哪里？
2. 如果重来，应该关注什么信号？
3. 这个教训可以提炼成什么规则？"""

        system = "你是林铛，在做模拟复盘。简洁务实，每句话都要有具体指向。"

        text, call_err = call_ai(client, cfg_no_search, prompt, system=system, max_tokens=400)
        if not call_err and text:
            replays.append({
                "stock": name,
                "date": date,
                "original_score": score,
                "actual_return": ret,
                "replay_insight": text.strip()[:300],
            })

    return {"replayed": len(replays), "cases": replays}


# ══════════════════════════════════════════════════════════════════
# 第三轮：总结报告
# ══════════════════════════════════════════════════════════════════

def round3_report(round1: dict = None, round2: dict = None) -> dict:
    """第三轮：生成夜间学习报告 + 更新 STATE.md + 发邮件。"""
    results = {"round": 3, "timestamp": datetime.now().isoformat(timespec="seconds")}

    # 生成报告文本
    report = _build_night_report(round1 or {}, round2 or {})
    results["report_length"] = len(report)

    # 保存报告
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    report_path = REPORT_DIR / f"night_report_{today}.md"
    report_path.write_text(report, encoding="utf-8")
    results["report_path"] = str(report_path)

    # 更新 STATE.md
    try:
        from knowledge.session_handoff import generate_state_md
        generate_state_md()
        results["state_updated"] = True
    except Exception as exc:
        logger.debug("[night_learner] state update failed: %r", exc)

    # 发邮件
    try:
        _send_night_report_email(report, today)
        results["email_sent"] = True
    except Exception as exc:
        logger.warning("[night] email failed: %r", exc)
        results["email_error"] = str(exc)

    return results


def _build_night_report(r1: dict, r2: dict) -> str:
    """构建夜间学习报告 Markdown。"""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# 林铛夜间学习报告 {today}",
        "",
    ]

    # 第一轮结果
    lines.append("## 一、数据扫描")
    lines.append(f"- 全市场异动股扫描: {r1.get('market_scan', 0)} 只新观察")
    lines.append(f"- 新闻抓取: {r1.get('news_scanned', 0)} 条扫描, {r1.get('news_relevant', 0)} 条相关")
    lines.append(f"- 盘感回溯验证: {r1.get('kline_backtested', 0)} 条")

    wv = r1.get("wisdom_validation", {})
    if wv.get("validated"):
        lines.append(f"- 智慧验证: {wv['validated']} 条有足够样本验证")
        lines.append(f"  - 基线胜率: {wv.get('baseline_rate', '?')}%")
        lines.append(f"  - 高价值智慧（胜率>基线+10pp）: {wv.get('high_value_count', 0)} 条")
        lines.append(f"  - 低价值智慧（胜率<基线-10pp）: {wv.get('low_value_count', 0)} 条")
        for hv in wv.get("top_high_value", [])[:3]:
            lines.append(f"    ✅ [{hv['source']}] {hv['wisdom']}... (胜率{hv['win_rate']}%, +{hv['vs_baseline']}pp)")
        for lv in wv.get("top_low_value", [])[:3]:
            lines.append(f"    ⚠️ [{lv['source']}] {lv['wisdom']}... (胜率{lv['win_rate']}%, {lv['vs_baseline']}pp)")

    # 第二轮结果
    lines.append("")
    lines.append("## 二、AI 学习")
    lines.append(f"- 新闻分析: {r2.get('news_analyzed', 0)} 篇")

    ws = r2.get("weakness_study", {})
    if ws.get("worst_sectors"):
        lines.append("- 弱项识别:")
        for s in ws["worst_sectors"]:
            lines.append(f"  - {s['sector']}: {s['losses']}次亏损, 平均{s['avg_return']:+.1f}%")

    replay = r2.get("replay", {})
    if replay.get("cases"):
        lines.append("- 模拟复盘:")
        for c in replay["cases"]:
            lines.append(f"  - {c['date']} {c['stock']}: 原评{c['original_score']}分→实际{c['actual_return']:+.1f}%")
            lines.append(f"    复盘: {c['replay_insight'][:150]}")

    # 模拟训练结果
    sim = r2.get("simulation", {})
    if sim.get("trained"):
        lines.append(f"- 模拟训练(AlphaGo式): {sim['trained']}只, 命中率{sim.get('hit_rate', 0):.0f}%")

    lines.append("")
    lines.append("---")
    lines.append("*林铛夜间自进化引擎 自动生成*")

    return "\n".join(lines)


def _send_night_report_email(report: str, date: str):
    """发送夜间学习报告邮件。"""
    try:
        from utils.email_sender import send_text_email, smtp_configured
    except ImportError:
        return

    if not smtp_configured():
        return

    send_text_email(f"【林铛夜间学习报告】{date}", report)
    logger.info("[night] report email sent")


# ══════════════════════════════════════════════════════════════════
# 完整夜间学习流程
# ══════════════════════════════════════════════════════════════════

def run_night_learning(phase: str = "all") -> dict:
    """运行夜间学习。

    phase: "all" 完整流程, "scan" 仅扫描, "ai" 仅AI学习, "report" 仅报告
    """
    logger.info("[night] starting night learning, phase=%s", phase)
    results = {}

    if phase in ("all", "scan"):
        results["round1"] = round1_scan()

    if phase in ("all", "ai"):
        results["round2"] = round2_ai_learn(results.get("round1"))

    if phase in ("all", "report"):
        results["round3"] = round3_report(results.get("round1"), results.get("round2"))

    logger.info("[night] night learning complete")
    return results
