"""知识库定时更新调度器

每日 19:30 北京时间执行：
1. 评估待处理报告（outcome_tracker）
2. 检测市场环境（regime_detector）
3. 重算规律库（pattern_memory）
4. 重算模型绩效（analyst_scorecard）
"""

import asyncio
import logging
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_scheduler_started = False


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

    logger.info("[knowledge_scheduler] daily update complete: %s", results)
    return results


def _seconds_until_target(hour: int = 19, minute: int = 30) -> float:
    """计算距下一个目标时间点的秒数。"""
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _async_scheduler_loop():
    """异步调度循环，每天 19:30 执行。"""
    while True:
        wait = _seconds_until_target(19, 30)
        logger.info("[knowledge_scheduler] next update in %.0f seconds", wait)
        await asyncio.sleep(wait)
        try:
            await asyncio.to_thread(run_knowledge_update)
        except Exception as exc:
            logger.exception("[knowledge_scheduler] update failed: %r", exc)
        # 等 60 秒避免在同一分钟重复触发
        await asyncio.sleep(60)


def start_scheduler():
    """启动知识库定时调度（在 FastAPI 启动时调用）。"""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    loop = asyncio.get_event_loop()
    loop.create_task(_async_scheduler_loop())
    logger.info("[knowledge_scheduler] scheduler started, daily update at 19:30")
