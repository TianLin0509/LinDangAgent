"""雪球大V舆情雷达 — 爬取热门帖子 + 大V动态"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from html import unescape

import requests

logger = logging.getLogger(__name__)

_session: requests.Session | None = None
_session_lock = threading.Lock()
_session_ts: float = 0
_SESSION_TTL = 1800  # 30 分钟刷新 cookie
_request_sem = threading.Semaphore(1)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://xueqiu.com/",
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
}

# 预设知名投资大V uid（可通过 secrets 扩展）
_DEFAULT_BIGV_UIDS: list[str] = []


def _get_xq_cookie() -> str:
    """从环境变量或 secrets 读取雪球 cookie。"""
    import os
    cookie = os.environ.get("XQ_COOKIE", "")
    if cookie:
        return cookie
    try:
        from utils.app_config import get_secret
        return get_secret("XQ_COOKIE", "")
    except Exception:
        return ""


def _get_session(force_refresh: bool = False) -> requests.Session:
    global _session, _session_ts
    with _session_lock:
        now = time.time()
        if _session and not force_refresh and (now - _session_ts) < _SESSION_TTL:
            return _session
        s = requests.Session()
        s.headers.update(_HEADERS)

        # 优先使用预设 cookie（绕过 WAF）
        xq_cookie = _get_xq_cookie()
        if xq_cookie:
            s.headers["Cookie"] = xq_cookie
            logger.info("[xueqiu_radar] using pre-set XQ_COOKIE")
        else:
            # 尝试自动获取 cookie（可能被 WAF 拦截）
            try:
                resp = s.get("https://xueqiu.com/", timeout=10)
                resp.raise_for_status()
                logger.info("[xueqiu_radar] cookie from homepage, status=%d", resp.status_code)
            except Exception as exc:
                logger.warning("[xueqiu_radar] cookie refresh failed: %s", exc)

        _session = s
        _session_ts = now
        return s


def _api_get(url: str, params: dict, retry: bool = True) -> dict | list | None:
    """统一请求入口，带自动重试。"""
    with _request_sem:
        time.sleep(random.uniform(0.3, 1.0))
        s = _get_session()
        try:
            resp = s.get(url, params=params, timeout=15)
            if resp.status_code in (400, 403, 401) and retry:
                logger.info("[xueqiu_radar] got %d, refreshing cookie and retrying", resp.status_code)
                s = _get_session(force_refresh=True)
                resp = s.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("[xueqiu_radar] request failed: %s %s -> %s", url, params, exc)
            return None


def _strip_html(text: str) -> str:
    """去除 HTML 标签。"""
    if not text:
        return ""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_mentioned_stocks(text: str) -> list[str]:
    """提取帖子中 $股票名$ 格式的股票标签。"""
    return re.findall(r"\$([^$]+?)\$", text or "")


def _parse_post(raw: dict) -> dict | None:
    """将雪球 API 返回的帖子原始数据解析为标准格式。"""
    try:
        user = raw.get("user") or {}
        text_raw = raw.get("text") or raw.get("description") or ""
        text_clean = _strip_html(text_raw)
        if not text_clean:
            return None

        created_ms = raw.get("created_at") or 0
        if isinstance(created_ms, (int, float)) and created_ms > 1e12:
            created_dt = datetime.fromtimestamp(created_ms / 1000, tz=timezone(timedelta(hours=8)))
        else:
            created_dt = None

        return {
            "id": str(raw.get("id") or ""),
            "user_name": user.get("screen_name") or "",
            "user_id": str(user.get("id") or ""),
            "followers_count": user.get("followers_count") or 0,
            "verified": user.get("verified") or False,
            "verified_description": user.get("verified_description") or "",
            "text": text_clean,
            "text_raw": text_raw,
            "created_at": created_dt.isoformat() if created_dt else "",
            "created_dt": created_dt,
            "like_count": raw.get("like_count") or 0,
            "reply_count": raw.get("reply_count") or 0,
            "retweet_count": raw.get("retweet_count") or 0,
            "mentioned_stocks": _extract_mentioned_stocks(text_raw),
        }
    except Exception as exc:
        logger.debug("[xueqiu_radar] parse_post error: %s", exc)
        return None


def fetch_hot_posts(
    min_followers: int = 50000,
    hours: int = 24,
    min_likes: int = 10,
    max_pages: int = 3,
) -> list[dict]:
    """从雪球热门帖子流中抓取大V帖子。"""
    cutoff = datetime.now(tz=timezone(timedelta(hours=8))) - timedelta(hours=hours)
    results: list[dict] = []
    max_id = -1

    for page in range(max_pages):
        data = _api_get(
            "https://xueqiu.com/statuses/hot/listV2.json",
            {"since_id": -1, "max_id": max_id, "size": 50},
        )
        if not data:
            break

        items = data.get("items") or []
        if not items:
            break

        for item in items:
            original = item.get("original_status") or item
            post = _parse_post(original)
            if not post:
                continue

            # 过滤条件
            if post["followers_count"] < min_followers:
                continue
            if post["created_dt"] and post["created_dt"] < cutoff:
                continue
            if post["like_count"] < min_likes:
                continue
            if len(post["text"]) < 50:
                continue

            results.append(post)

        # 翻页
        next_max = data.get("next_max_id")
        if not next_max or next_max == max_id:
            break
        max_id = next_max

    logger.info("[xueqiu_radar] hot posts: %d posts matched filters", len(results))
    return results


def fetch_user_posts(user_id: str, count: int = 10) -> list[dict]:
    """抓取指定用户的最新帖子。"""
    data = _api_get(
        "https://xueqiu.com/v4/statuses/user_timeline.json",
        {"user_id": user_id, "page": 1, "page_size": count},
    )
    if not data:
        return []

    statuses = data.get("statuses") or data.get("list") or []
    if isinstance(data, list):
        statuses = data

    results = []
    for raw in statuses:
        post = _parse_post(raw)
        if post:
            results.append(post)
    return results


def fetch_bigv_radar(
    bigv_uids: list[str] | None = None,
    min_followers: int = 50000,
    hours: int = 24,
    min_likes: int = 10,
) -> list[dict]:
    """组合入口：热门帖子流 + 预设大V列表。返回去重、按点赞排序的帖子列表。"""
    all_posts: list[dict] = []

    # 1. 热门帖子流
    try:
        hot = fetch_hot_posts(min_followers=min_followers, hours=hours, min_likes=min_likes)
        all_posts.extend(hot)
    except Exception as exc:
        logger.warning("[xueqiu_radar] fetch_hot_posts failed: %s", exc)

    # 2. 预设大V时间线
    uids = bigv_uids or _DEFAULT_BIGV_UIDS
    cutoff = datetime.now(tz=timezone(timedelta(hours=8))) - timedelta(hours=hours)
    for uid in uids:
        try:
            posts = fetch_user_posts(uid, count=5)
            for p in posts:
                if p["created_dt"] and p["created_dt"] >= cutoff:
                    all_posts.append(p)
        except Exception as exc:
            logger.debug("[xueqiu_radar] user %s failed: %s", uid, exc)

    # 去重
    seen_ids: set[str] = set()
    unique: list[dict] = []
    for p in all_posts:
        if p["id"] and p["id"] not in seen_ids:
            seen_ids.add(p["id"])
            unique.append(p)

    # 按点赞数降序
    unique.sort(key=lambda x: x.get("like_count", 0), reverse=True)

    logger.info("[xueqiu_radar] bigv_radar: %d unique posts", len(unique))
    return unique
