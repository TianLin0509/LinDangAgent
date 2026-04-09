"""知识库更新 — 手动/定时触发

执行内容：
1. 评估待处理报告（outcome_tracker）
2. 检测市场环境（regime_detector）
3. 重算规律库（pattern_memory）
4. 重算模型绩效（analyst_scorecard）
5. 生成 AI 反思并存储案例卡片（reflection）— Claude Sonnet 主脑
6. 更新情报主题趋势（intel_memory）
7. 从案例教训中提炼/更新投资信念（thesis_journal）— Claude Sonnet
8. 周度深度反思（周日，Claude Sonnet）/ 月度复盘（月初，Claude Opus）

定时调度：
  python cli.py review-schedule         # 启动每日自动复盘（后台进程）
  python cli.py review-schedule stop    # 停止自动复盘
"""

import logging
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from knowledge.kb_config import BASE_DIR, DIRECTION_CN, SCHEDULE_PID_FILE, SCHEDULE_LOG_FILE

logger = logging.getLogger(__name__)


def run_knowledge_update():
    """执行一次完整的知识库更新。"""
    logger.info("[knowledge_scheduler] starting daily update")
    results = {}

    # 1a. 评估单股报告 outcome
    try:
        from knowledge.outcome_tracker import evaluate_pending
        n = evaluate_pending()
        results["outcomes_evaluated"] = n
        logger.info("[knowledge_scheduler] report outcomes evaluated: %d", n)
    except Exception as exc:
        logger.exception("[knowledge_scheduler] outcome_tracker failed: %r", exc)
        results["outcomes_error"] = str(exc)

    # 1b. 评估 Top100 推荐 outcome
    try:
        from knowledge.outcome_tracker import evaluate_top100_pending
        n100 = evaluate_top100_pending()
        results["top100_outcomes_evaluated"] = n100
        logger.info("[knowledge_scheduler] top100 outcomes evaluated: %d", n100)
    except Exception as exc:
        logger.exception("[knowledge_scheduler] top100 outcome_tracker failed: %r", exc)
        results["top100_outcomes_error"] = str(exc)

    # 2. 检测 regime
    try:
        from knowledge.regime_detector import detect_current_regime
        regime = detect_current_regime()
        results["regime"] = regime.get("regime_label", "unknown")
        logger.info("[knowledge_scheduler] regime: %s", results["regime"])
    except Exception as exc:
        logger.exception("[knowledge_scheduler] regime_detector failed: %r", exc)
        results["regime_error"] = str(exc)

    # 3. 重算 patterns
    try:
        from knowledge.pattern_memory import rebuild_patterns
        patterns = rebuild_patterns()
        results["patterns_count"] = len(patterns)
    except Exception as exc:
        logger.exception("[knowledge_scheduler] pattern_memory failed: %r", exc)
        results["patterns_error"] = str(exc)

    # 4. 重算 scorecard
    try:
        from knowledge.analyst_scorecard import rebuild_scorecard
        sc = rebuild_scorecard()
        results["scorecard_samples"] = sc.get("sample_count", 0)
    except Exception as exc:
        logger.exception("[knowledge_scheduler] analyst_scorecard failed: %r", exc)
        results["scorecard_error"] = str(exc)

    # 5. 生成 AI 反思并存储案例卡片（循环补全，每轮20条，最多5轮=100条）
    try:
        from knowledge.reflection import process_pending_reflections
        total_reflections = 0
        for _round in range(5):
            batch = process_pending_reflections(max_batch=20)
            total_reflections += batch
            if batch < 20:  # 没有更多待处理的了
                break
        results["reflections_generated"] = total_reflections
        logger.info("[knowledge_scheduler] reflections generated: %d", total_reflections)
    except Exception as exc:
        logger.exception("[knowledge_scheduler] reflection failed: %r", exc)
        results["reflection_error"] = str(exc)

    # 6. 报告案例库统计
    try:
        from knowledge.case_memory import get_case_count
        results["total_cases"] = get_case_count()
    except Exception as exc:
        logger.debug("[scheduler] case count failed: %r", exc)

    # 7. 更新情报主题趋势
    try:
        from knowledge.intel_memory import update_theme_stats, get_intel_count
        update_theme_stats()
        results["intel_count"] = get_intel_count()
        logger.info("[knowledge_scheduler] intel theme stats updated")
    except Exception as exc:
        logger.debug("[knowledge_scheduler] intel_memory update skipped: %r", exc)

    # 8. 从案例教训中提炼/更新投资信念（Claude Sonnet）
    try:
        from knowledge.thesis_journal import update_beliefs_from_cases, get_belief_count
        new_beliefs = update_beliefs_from_cases()
        results["beliefs_updated"] = new_beliefs
        results["total_beliefs"] = get_belief_count()
        logger.info("[knowledge_scheduler] thesis updated: %d new beliefs", new_beliefs)
    except Exception as exc:
        logger.debug("[knowledge_scheduler] thesis_journal update skipped: %r", exc)

    # 9. 深度反思（周日=周度反思，月初=月度复盘）
    try:
        now = datetime.now()
        if now.weekday() == 6:  # 周日
            from knowledge.deep_reflection import run_weekly_reflection
            weekly = run_weekly_reflection()
            if weekly:
                results["weekly_reflection"] = weekly.get("self_grade", "done")
                logger.info("[knowledge_scheduler] weekly reflection completed")
        if now.day <= 2:  # 月初（1日或2日）
            from knowledge.deep_reflection import run_monthly_reflection
            monthly = run_monthly_reflection()
            if monthly:
                results["monthly_reflection"] = monthly.get("self_grade", "done")
                logger.info("[knowledge_scheduler] monthly reflection completed")
            # 月度：AI 解释新发现的盘感形态
            try:
                from knowledge.kline_diary import ai_interpret_discoveries
                interpreted = ai_interpret_discoveries()
                if interpreted:
                    results["kline_ai_interpreted"] = interpreted
                    logger.info("[knowledge_scheduler] kline discoveries interpreted: %d", interpreted)
            except Exception as exc2:
                logger.debug("[knowledge_scheduler] kline AI interpret skipped: %r", exc2)
    except Exception as exc:
        logger.debug("[knowledge_scheduler] deep reflection skipped: %r", exc)

    # 10. 盘感训练：每日K线观察 + 回溯验证 + 统计更新
    try:
        from knowledge.kline_diary import scan_and_observe, backtest_pending, rebuild_pattern_stats
        obs_count = scan_and_observe()
        bt_count = backtest_pending(days_ago=5)
        stats_count = rebuild_pattern_stats()
        results["kline_observations"] = obs_count
        results["kline_backtests"] = bt_count
        results["kline_pattern_stats"] = stats_count
        logger.info("[knowledge_scheduler] kline diary: %d observed, %d backtested, %d stats",
                    obs_count, bt_count, stats_count)
    except Exception as exc:
        logger.debug("[knowledge_scheduler] kline diary skipped: %r", exc)

    # 11. 重新生成 STATE.md 工作记忆快照（所有数据更新完毕后）
    try:
        from knowledge.session_handoff import generate_state_md
        generate_state_md()
        logger.info("[knowledge_scheduler] STATE.md regenerated")
    except Exception as exc:
        logger.debug("[knowledge_scheduler] STATE.md generation skipped: %r", exc)

    # 11. 复盘结果邮件汇报
    try:
        _send_review_email(results)
    except Exception as exc:
        logger.warning("[knowledge_scheduler] email send failed: %r", exc)
        results["email_error"] = str(exc)

    logger.info("[knowledge_scheduler] daily update complete: %s", results)
    return results


def _send_review_email(results: dict):
    """将复盘结果通过邮件发送给用户。"""
    try:
        from utils.email_sender import send_text_email, smtp_configured
    except ImportError:
        logger.debug("[review_email] utils.email_sender not available")
        return

    if not smtp_configured():
        logger.debug("[review_email] SMTP not configured, skip")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    regime = results.get("regime", "未知")
    outcomes = results.get("outcomes_evaluated", 0)
    top100_outcomes = results.get("top100_outcomes_evaluated", 0)
    reflections = results.get("reflections_generated", 0)
    total_cases = results.get("total_cases", "?")
    patterns = results.get("patterns_count", "?")
    scorecard_samples = results.get("scorecard_samples", "?")

    # 获取最新反思教训（最多5条）
    lesson_section = ""
    try:
        from knowledge.kb_db import get_manager
        import sqlite3
        mgr = get_manager()
        with mgr.read("case_memory") as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT report_date, stock_name, score_weighted, direction, "
                "outcome_type, return_10d, lesson FROM cases "
                "WHERE lesson IS NOT NULL AND lesson != '' "
                "ORDER BY created_at DESC LIMIT 5"
            ).fetchall()
            conn.row_factory = None

        if rows:
            lessons = []
            for r in rows:
                mark = {"win": "✅", "loss": "❌", "draw": "➖"}.get(r["outcome_type"], "")
                dir_cn = DIRECTION_CN.get(r["direction"], "中性")
                lessons.append(
                    f"  {r['report_date']} {r['stock_name']} "
                    f"评{r['score_weighted']}分{dir_cn} → "
                    f"10日{r['return_10d']:+.1f}% {mark}\n"
                    f"    教训：{r['lesson'][:100]}"
                )
            lesson_section = "\n\n【最新复盘教训】\n" + "\n\n".join(lessons)
    except Exception as exc:
        logger.debug("[scheduler] lesson fetch failed: %r", exc)

    # 获取准确率统计
    accuracy_section = ""
    try:
        from knowledge.outcome_tracker import get_accuracy_summary
        acc = get_accuracy_summary(days=90)
        if acc.get("directional_count", 0) >= 5:
            accuracy_section = (
                f"\n\n【90天准确率统计】\n"
                f"  样本: {acc['directional_count']}条\n"
                f"  5日胜率: {acc.get('hit_rate_5d', 0):.1f}%\n"
                f"  10日胜率: {acc.get('hit_rate_10d', 0):.1f}%\n"
                f"  20日胜率: {acc.get('hit_rate_20d', 0):.1f}%\n"
                f"  10日平均收益: {acc.get('avg_return_10d', 0):+.2f}%"
            )
            # 超额胜率
            if acc.get("beat_market_rate_10d") is not None:
                accuracy_section += f"\n  超额胜率(vs沪深300): {acc['beat_market_rate_10d']:.1f}%"
    except Exception as exc:
        logger.debug("[scheduler] accuracy fetch failed: %r", exc)

    # 检查错误
    errors = [f"  - {k}: {v}" for k, v in results.items() if k.endswith("_error")]
    error_section = "\n\n⚠️ 执行异常：\n" + "\n".join(errors) if errors else ""

    subject = f"【林彪指挥部】每日复盘战报 {today}"
    body = f"""四野指挥部·每日知识库复盘战报
{'='*40}

【执行摘要】
  日期: {today}
  市场环境: {regime}
  新评估报告: {outcomes} 条
  新评估Top100: {top100_outcomes} 条
  新生成反思: {reflections} 条
  案例库总量: {total_cases} 条
  模式统计: {patterns} 个
  绩效卡样本: {scorecard_samples} 条{accuracy_section}{lesson_section}{error_section}

{'='*40}
LinDangAgent 自动复盘系统
"""

    send_text_email(subject, body)
    logger.info("[review_email] sent review report")


# ── 定时调度（每日自动复盘）────────────────────────────────────────

def start_scheduled_review(run_hour: int = 19, run_minute: int = 30):
    """启动每日定时复盘后台进程。

    默认每天 19:30（收盘后1.5小时）执行 run_knowledge_update()。
    以 DETACHED_PROCESS 方式运行，写入 PID 文件供停止。
    """
    import subprocess

    SCHEDULE_PID_FILE.parent.mkdir(parents=True, exist_ok=True)

    # 检查是否已在运行
    if SCHEDULE_PID_FILE.exists():
        try:
            old_pid = int(SCHEDULE_PID_FILE.read_text().strip())
            # 检查进程是否还活着
            import signal
            os.kill(old_pid, 0)
            logger.info("[scheduler] already running (PID %d)", old_pid)
            return {"status": "already_running", "pid": old_pid}
        except (OSError, ValueError):
            pass  # 旧进程已死，继续启动新的

    # 用子进程启动定时器
    script = BASE_DIR / "knowledge" / "scheduler.py"
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

    log_fh = open(str(SCHEDULE_LOG_FILE), "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(script), "--daemon", str(run_hour), str(run_minute)],
        cwd=str(BASE_DIR),
        creationflags=flags,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    log_fh.close()  # 子进程已获得句柄副本，父进程可安全关闭

    SCHEDULE_PID_FILE.write_text(str(proc.pid))
    logger.info("[scheduler] started daemon PID %d, will run daily at %02d:%02d", proc.pid, run_hour, run_minute)
    return {"status": "started", "pid": proc.pid, "schedule": f"{run_hour:02d}:{run_minute:02d}"}


def stop_scheduled_review():
    """停止定时复盘后台进程。"""
    if not SCHEDULE_PID_FILE.exists():
        return {"status": "not_running"}

    try:
        pid = int(SCHEDULE_PID_FILE.read_text().strip())
        if sys.platform == "win32":
            os.system(f"taskkill /F /PID {pid} >nul 2>&1")
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
        SCHEDULE_PID_FILE.unlink(missing_ok=True)
        return {"status": "stopped", "pid": pid}
    except Exception as exc:
        SCHEDULE_PID_FILE.unlink(missing_ok=True)
        return {"status": "error", "error": str(exc)}


def _daemon_loop(run_hour: int, run_minute: int):
    """后台定时循环（由 --daemon 参数触发）。

    时间表：
      run_hour:run_minute  — 每日知识库复盘（默认 19:30）
      22:00               — 夜间学习第一轮（数据扫描）
      00:30               — 夜间学习第二轮（AI学习，错开整点避限流）
      04:30               — 夜间学习第三轮（总结报告）
    """
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    SCHEDULE_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_PID_FILE.write_text(str(os.getpid()))

    # 确保 LinDangAgent 根目录在 sys.path 中（daemon 子进程需要）
    base_str = str(BASE_DIR)
    if base_str not in sys.path:
        sys.path.insert(0, base_str)

    logger.info("[scheduler_daemon] started, PID=%d, daily=%02d:%02d + night learning",
                os.getpid(), run_hour, run_minute)

    last_run_date = ""
    # 夜间轮次用 night_id（22:00那天的日期）做去重，跨午夜不混淆
    night_id_done = ""  # 当晚的标识（= 22:00 所在日期）
    night_r1_results = {}
    night_r2_results = {}
    night_phases_done = set()  # {"r1", "r2", "r3"}

    while True:
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # 计算"当晚"标识：22:00 之前属于昨晚，22:00 之后属于今晚
        if now.hour >= 22:
            current_night_id = today_str
        elif now.hour < 6:
            current_night_id = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            current_night_id = ""  # 白天不触发夜间任务

        # 如果进入了新的一夜，重置状态
        if current_night_id and current_night_id != night_id_done:
            night_id_done = current_night_id
            night_phases_done = set()
            night_r1_results = {}
            night_r2_results = {}

        # ── 每日复盘（19:30）─────────────────────────────────
        if run_hour <= now.hour and now.minute >= run_minute and now.hour < run_hour + 2 and today_str != last_run_date:
            logger.info("[scheduler_daemon] triggering daily knowledge update")
            try:
                results = run_knowledge_update()
                last_run_date = today_str
                logger.info("[scheduler_daemon] done: %s", json.dumps(results, ensure_ascii=False)[:500])
            except Exception as exc:
                logger.exception("[scheduler_daemon] update failed: %r", exc)
                last_run_date = today_str

        # ── 夜间学习第一轮 22:00（数据扫描，零AI）──────────────
        if now.hour >= 22 and "r1" not in night_phases_done and current_night_id:
            logger.info("[scheduler_daemon] night round 1: data scan")
            try:
                from knowledge.night_learner import round1_scan
                night_r1_results = round1_scan()
                logger.info("[scheduler_daemon] night r1 done: %s",
                            json.dumps(night_r1_results, ensure_ascii=False, default=str)[:300])
            except Exception as exc:
                logger.exception("[scheduler_daemon] night r1 failed: %r", exc)
            night_phases_done.add("r1")

        # ── 夜间学习第二轮 00:30（AI学习，错开整点）──────────
        if now.hour == 0 and now.minute >= 30 and "r2" not in night_phases_done and current_night_id and "r1" in night_phases_done:
            logger.info("[scheduler_daemon] night round 2: AI learning")
            try:
                from knowledge.night_learner import round2_ai_learn
                night_r2_results = round2_ai_learn(night_r1_results)
                logger.info("[scheduler_daemon] night r2 done: %s",
                            json.dumps(night_r2_results, ensure_ascii=False, default=str)[:300])
            except Exception as exc:
                logger.exception("[scheduler_daemon] night r2 failed: %r", exc)
            night_phases_done.add("r2")

        # ── 夜间学习第三轮 04:30（总结报告）────────────────────
        if now.hour >= 4 and now.minute >= 30 and now.hour < 6 and "r3" not in night_phases_done and current_night_id:
            logger.info("[scheduler_daemon] night round 3: report")
            try:
                from knowledge.night_learner import round3_report
                r3 = round3_report(night_r1_results, night_r2_results)
                logger.info("[scheduler_daemon] night r3 done, report sent")
            except Exception as exc:
                logger.exception("[scheduler_daemon] night r3 failed: %r", exc)
            night_phases_done.add("r3")

        time.sleep(60)  # 每分钟检查一次


if __name__ == "__main__":
    # python scheduler.py --daemon <hour> <minute>
    if len(sys.argv) >= 2 and sys.argv[1] == "--daemon":
        hour = int(sys.argv[2]) if len(sys.argv) > 2 else 19
        minute = int(sys.argv[3]) if len(sys.argv) > 3 else 30
        _daemon_loop(hour, minute)
