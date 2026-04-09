# -*- coding: utf-8 -*-
"""新闻源自动监控 — 定时抓取财经 RSS/网页，自动触发 intel-analyze

数据源（国内财经 RSS/API）：
  - 财联社 cls.cn 电报
  - 东方财富要闻
  - 36氪快讯

设计：
  - 每次拉取最新 N 条，与已处理的 URL 去重
  - 符合关键词过滤的文章自动送 intel-analyze
  - 结果存入 intel_memory
"""

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge"
SEEN_URLS_FILE = KNOWLEDGE_DIR / "news_seen_urls.json"

# 浏览器伪装
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}

# A 股投研相关关键词过滤（标题或摘要中包含这些词才处理）
RELEVANCE_KEYWORDS = [
    "A股", "沪指", "深指", "创业板", "科创板",
    "利好", "利空", "涨停", "跌停", "放量", "缩量",
    "北向资金", "外资", "融资", "减持", "增持",
    "业绩", "财报", "预增", "预减", "预亏",
    "政策", "降息", "降准", "LPR",
    "半导体", "芯片", "AI", "算力", "光伏", "新能源", "锂电",
    "白酒", "医药", "券商", "银行", "地产",
    "机器人", "低空", "军工",
]


def _load_seen_urls() -> set:
    if SEEN_URLS_FILE.exists():
        try:
            return set(json.loads(SEEN_URLS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def _save_seen_urls(urls: set):
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    # 只保留最近 500 条
    url_list = sorted(urls)[-500:]
    SEEN_URLS_FILE.write_text(json.dumps(url_list, ensure_ascii=False), encoding="utf-8")


def _is_relevant(title: str, summary: str = "") -> bool:
    text = (title + " " + summary).lower()
    return any(kw.lower() in text for kw in RELEVANCE_KEYWORDS)


# ── 数据源：财联社电报 ───────────────────────────────────────────

def fetch_cls_telegraph(limit: int = 20) -> list[dict]:
    """抓取财联社电报（最新快讯）。"""
    results = []
    try:
        url = "https://www.cls.cn/nodeapi/updateTelegraph"
        params = {"app": "CailianpressWeb", "os": "web", "sv": "8.4.6", "rn": str(limit)}
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("data", {}).get("roll_data", []):
            title = item.get("title", "") or item.get("brief", "")
            content = item.get("content", "")
            # 清理 HTML 标签
            content = re.sub(r"<[^>]+>", "", content)

            if not title and not content:
                continue

            # 财联社电报没有独立URL，用时间戳构造
            item_id = str(item.get("id", ""))
            fake_url = f"https://www.cls.cn/telegraph/{item_id}" if item_id else ""

            results.append({
                "title": title[:100] or content[:100],
                "url": fake_url,
                "summary": content[:300],
                "source": "财联社电报",
                "time": item.get("ctime", ""),
            })
    except Exception as exc:
        logger.warning("[news_monitor] cls telegraph failed: %s", exc)

    return results


def fetch_eastmoney_news(limit: int = 15) -> list[dict]:
    """抓取东方财富要闻。"""
    results = []
    try:
        url = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
        params = {
            "client": "web",
            "biz": "web_home_channel",
            "column": "important",
            "order": "1",
            "needInteractData": "0",
            "page_index": "1",
            "page_size": str(limit),
        }
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("data", {}).get("list", []):
            title = item.get("title", "")
            article_url = item.get("url", "")
            digest = item.get("digest", "")

            if not title:
                continue

            results.append({
                "title": title[:100],
                "url": article_url,
                "summary": digest[:300],
                "source": "东方财富",
                "time": item.get("showtime", ""),
            })
    except Exception as exc:
        logger.warning("[news_monitor] eastmoney failed: %s", exc)

    return results


# ── 主入口 ───────────────────────────────────────────────────────

def scan_news_sources(max_analyze: int = 3) -> dict:
    """扫描所有新闻源，对相关新闻自动执行 intel-analyze。

    max_analyze: 本次最多分析几篇（控制 Claude 调用次数）。
    返回 {scanned, relevant, analyzed, articles: [{title, url, intel_id}]}
    """
    seen = _load_seen_urls()
    all_articles = []

    # 抓取各数据源
    all_articles.extend(fetch_cls_telegraph(limit=20))
    all_articles.extend(fetch_eastmoney_news(limit=15))

    # 去重 + 过滤
    new_relevant = []
    for article in all_articles:
        url = article.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)

        if _is_relevant(article.get("title", ""), article.get("summary", "")):
            new_relevant.append(article)

    _save_seen_urls(seen)

    # 对最相关的文章执行 intel-analyze
    analyzed = []
    for article in new_relevant[:max_analyze]:
        url = article["url"]
        if not url.startswith("http"):
            continue

        try:
            from services.intel_analyze import run_intel_analyze
            result = run_intel_analyze(url, "")  # 用默认模型
            if result.get("status") == "ok":
                analyzed.append({
                    "title": article["title"],
                    "url": url,
                    "source": article.get("source", ""),
                    "intel_id": result.get("intel_id", result.get("report_id", "")),
                })
                logger.info("[news_monitor] analyzed: %s", article["title"][:40])
        except Exception as exc:
            logger.warning("[news_monitor] intel-analyze failed for %s: %s", url, exc)

        # 避免限流
        if len(analyzed) < max_analyze:
            time.sleep(2)

    result = {
        "scanned": len(all_articles),
        "new_relevant": len(new_relevant),
        "analyzed": len(analyzed),
        "articles": analyzed,
    }
    logger.info("[news_monitor] scan complete: %d scanned, %d relevant, %d analyzed",
                len(all_articles), len(new_relevant), len(analyzed))
    return result
