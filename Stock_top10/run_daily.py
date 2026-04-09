#!/usr/bin/env python3
"""独立入口 — GitHub Actions / cron 定时触发，不依赖 Streamlit"""

import logging
import sys
import os

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_daily")


def main():
    from config import DEFAULT_MODEL
    from Stock_top10.top10.deep_runner import run_deep_top10, get_deep_status

    # 检查是否已完成
    status = get_deep_status()
    if status and status.get("status") == "done":
        logger.info("今日深度分析已完成，跳过")
        return

    if status and status.get("status") == "running":
        logger.info("深度分析正在运行中，跳过")
        return

    model = os.environ.get("TOP10_MODEL", "").strip() or DEFAULT_MODEL
    count = int(os.environ.get("TOP10_CANDIDATES", "100"))

    logger.info("开始每日深度分析 — 模型: %s, 候选数: %d", model, count)

    run_deep_top10(
        model_name=model,
        candidate_count=count,
        username="github_actions",
    )

    # 验证结果
    status = get_deep_status()
    if status and status.get("status") == "done":
        logger.info("分析完成! tokens: %s", status.get("tokens_used", "N/A"))
    else:
        logger.error("分析未成功完成: %s", status)
        sys.exit(1)


if __name__ == "__main__":
    main()
