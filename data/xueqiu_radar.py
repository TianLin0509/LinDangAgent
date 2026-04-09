"""雪球大V舆情雷达 — Selenium 无头 Chrome 爬取热门帖子 + 大V动态

雪球使用阿里云 WAF（acw_sc__v2 JS 挑战），纯 requests 已无法访问。
改用 Selenium 无头 Chrome，真实浏览器自动通过 WAF。
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# 预设知名投资大V uid
# 可通过环境变量 XQ_BIGV_UIDS（逗号分隔）或 secrets 扩展
_DEFAULT_BIGV_UIDS: list[str] = [
    "1247347556",   # 闲来一坐s话投资（61万粉，价值投资大V）
    "5819606767",   # 雪月霜（15万粉，锂矿/周期股）
    "2227798950",   # 买股票的老木匠（18万粉，价值投机）
    "6784593966",   # 大隐无言（18万粉，医药/创新药）
    "1876906471",   # 飘仙的个人日记（24万粉，价值+波段）
    "5664463791",   # 鑫鑫-投资（22万粉，价值投资）
    "2638436285",   # ericwarn丁宁（21万粉，市赚率体系）
    "6868680848",   # 滇南王（19万粉，消费/饮料）
    "3081204011",   # 幻舞之尘（12万粉，地产研究）
    "5765498357",   # ETF大白（5万粉，指数/量化）
]

# ── Selenium 单例浏览器 ──────────────────────────────────────────

_driver = None
_driver_lock = threading.Lock()
_driver_ts: float = 0
_DRIVER_TTL = 3600  # 1 小时后重建浏览器（防内存泄漏）
_initialized = False


def _ensure_driver():
    """懒初始化无头 Chrome，整个进程生命周期复用。"""
    global _driver, _driver_ts, _initialized

    with _driver_lock:
        now = time.time()
        # 已有 driver 且未过期
        if _driver and (now - _driver_ts) < _DRIVER_TTL:
            return _driver

        # 关闭旧 driver
        if _driver:
            try:
                _driver.quit()
            except Exception:
                pass

        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        )

        _driver = webdriver.Chrome(options=opts)
        _driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
        })

        # 先访问首页拿 WAF cookie
        _driver.get("https://xueqiu.com/")
        time.sleep(2)
        _driver_ts = now
        _initialized = True
        logger.info("[xueqiu_radar] Chrome driver initialized")
        return _driver


_FETCH_JS = """
var cb = arguments[arguments.length - 1];
fetch(arguments[0], {credentials:'include'})
  .then(function(r){return r.text();})
  .then(function(t){cb(t);})
  .catch(function(e){cb('FETCH_ERR:'+e);});
"""


def _api_get(url: str, params: dict, retry: bool = True) -> dict | list | None:
    """通过 Selenium 无头 Chrome 的 JS fetch() 访问雪球 API，绕过 WAF。"""
    full_url = f"{url}?{urlencode(params)}" if params else url

    time.sleep(random.uniform(0.3, 1.0))

    def _do_fetch(drv):
        text = drv.execute_async_script(_FETCH_JS, full_url)
        if text and text.startswith("FETCH_ERR:"):
            raise RuntimeError(text)
        return json.loads(text)

    try:
        return _do_fetch(_ensure_driver())
    except Exception as exc:
        if retry:
            logger.info("[xueqiu_radar] fetch failed (%s), retrying with fresh driver", exc)
            global _driver_ts
            _driver_ts = 0
            try:
                return _do_fetch(_ensure_driver())
            except Exception as exc2:
                logger.warning("[xueqiu_radar] retry also failed: %s", exc2)
                return None
        return None


# ── 帖子质量过滤 ─────────────────────────────────────────────────

_LOW_VALUE_PATTERNS = re.compile(
    r"(回复@|//@|转发微博|^打卡|^签到|^早安|^晚安|^加油|^哈哈|^没什么经验|"
    r"^谢谢|^感谢|^不错|^好的|^是的|^对的|^赞$|^顶$|^mark$|^收藏$|"
    r"找工作|求职|招聘|租房|搬家|快递|外卖)",
    re.IGNORECASE,
)


# 投资相关关键词——命中任意一个说明帖子大概率有投资价值
_INVEST_KEYWORDS = re.compile(
    r"(股|基金|ETF|涨|跌|仓|买入|卖出|持有|估值|市盈|PE|PB|ROE|营收|净利|分红|回购|"
    r"板块|赛道|龙头|题材|催化|预期|利好|利空|财报|业绩|增长|亏损|扭亏|反转|"
    r"均线|支撑|压力|突破|止损|止盈|趋势|量价|资金|北向|融资|主力|"
    r"牛市|熊市|震荡|调整|反弹|回调|抄底|追高|割肉|套牢|解套|"
    r"A股|港股|美股|指数|大盘|上证|创业板|科创板|北交所)"
)


def _is_quality_post(post: dict) -> bool:
    """只过滤最垃圾的帖子（一句话闲聊、纯回复引用）。宽进严出。"""
    text = post.get("text", "")

    # 只过滤极短的（< 50 字）且没有股票标签
    if len(text) < 50 and not post.get("mentioned_stocks"):
        return False

    # 纯回复引用帖且很短
    if text.startswith("回复@") and len(text) < 100 and not post.get("mentioned_stocks"):
        return False

    return True


def _post_quality_score(p: dict) -> float:
    """帖子质量评分 — 聚焦于投资分析价值。

    核心目标：找到对具体股票有分析、对行情有判断、对投资有经验总结的好帖子。
    """
    score = 0.0
    text = p.get("text", "")

    # 1. 提及具体股票（$XXX$）：最核心的加分项，每只 +20，上限 +60
    stocks = p.get("mentioned_stocks", [])
    score += min(len(stocks) * 20, 60)

    # 2. 投资分析关键词密度：最多 +40
    #    越多关键词说明分析越深入
    keyword_hits = len(_INVEST_KEYWORDS.findall(text))
    score += min(keyword_hits * 4, 40)

    # 3. 正文深度（长度）：短帖很难有深度分析
    #    200字以下 +0，200-500字 +5~15，500字+ +15~25
    if len(text) >= 500:
        score += 25
    elif len(text) >= 200:
        score += 5 + (len(text) - 200) / 30
    # 短帖不加分也不扣分，靠其他维度决定

    # 4. 纯闲聊/回复引用 → 扣分（但不是一票否决）
    if text.startswith("回复@") or text.startswith("//@"):
        score -= 10

    return score


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

        post_id = str(raw.get("id") or "")
        user_id = str(user.get("id") or "")
        url = f"https://xueqiu.com/{user_id}/{post_id}" if user_id and post_id else ""

        return {
            "id": post_id,
            "url": url,
            "user_name": user.get("screen_name") or "",
            "user_id": user_id,
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
    min_followers: int = 0,
    hours: int = 24,
    min_likes: int = 5,
    min_replies: int = 0,
    max_pages: int = 30,
) -> list[dict]:
    """从雪球热门帖子流中抓取帖子（宽进严出，后续排序筛选）。

    优先使用 hots.json（稳定），listV2.json 已被 WAF 405 封禁。
    """
    cutoff = datetime.now(tz=timezone(timedelta(hours=8))) - timedelta(hours=hours)
    results: list[dict] = []

    for page_num in range(1, max_pages + 1):
        data = _api_get(
            "https://xueqiu.com/query/v1/status/hots.json",
            {"count": 50, "page": page_num, "type": 1},
        )
        if not data:
            break

        items = data.get("data") or []
        if not items:
            break

        for item in items:
            post = _parse_post(item)
            if not post:
                continue

            # 基础过滤
            if post["followers_count"] < min_followers:
                continue
            if post["created_dt"] and post["created_dt"] < cutoff:
                continue
            if post["like_count"] < min_likes:
                continue
            if post["reply_count"] < min_replies:
                continue
            if not _is_quality_post(post):
                continue

            results.append(post)

        # 翻页
        meta = data.get("meta") or {}
        if not meta.get("has_next_page"):
            break

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


def _post_sort_key(p: dict) -> float:
    """帖子排序：综合质量分。"""
    return _post_quality_score(p)


def fetch_bigv_radar(
    hours: int = 24,
    max_posts: int = 100,
    general_quota: int = 80,
    bigv_quota: int = 20,
    min_quality: float = 15,
) -> list[dict]:
    """从热帖列表爬取，分两池筛选：

    - 普通池（80条）：不限粉丝，点赞≥15，按质量排序
    - 大V池（20条）：粉丝≥1万，按质量排序
    两池合并去重，按质量分降序返回。
    """
    # 1. 大量爬取热帖（30页，宽松条件）
    try:
        raw_posts = fetch_hot_posts(
            min_followers=0, hours=hours,
            min_likes=5, min_replies=0, max_pages=30,
        )
        logger.info("[xueqiu_radar] raw posts fetched: %d", len(raw_posts))
    except Exception as exc:
        logger.warning("[xueqiu_radar] fetch_hot_posts failed: %s", exc)
        return []

    # 2. 去重
    seen_ids: set[str] = set()
    unique: list[dict] = []
    for p in raw_posts:
        if p["id"] and p["id"] not in seen_ids:
            seen_ids.add(p["id"])
            unique.append(p)

    # 3. 质量评分
    for p in unique:
        p["_quality"] = _post_quality_score(p)

    # 4. 分两池
    #    普通池：点赞≥15，质量分≥min_quality
    general_pool = [
        p for p in unique
        if p.get("like_count", 0) >= 15 and p["_quality"] >= min_quality
    ]
    general_pool.sort(key=lambda p: p["_quality"], reverse=True)

    #    大V池：粉丝≥1万，质量分≥min_quality
    bigv_pool = [
        p for p in unique
        if p.get("followers_count", 0) >= 10000 and p["_quality"] >= min_quality
    ]
    bigv_pool.sort(key=lambda p: p["_quality"], reverse=True)

    # 5. 合并：先取普通池 top N，再从大V池补充不重复的
    result_ids: set[str] = set()
    result: list[dict] = []

    for p in general_pool[:general_quota]:
        result.append(p)
        result_ids.add(p["id"])

    for p in bigv_pool:
        if len(result) >= max_posts:
            break
        if p["id"] not in result_ids:
            result.append(p)
            result_ids.add(p["id"])

    # 最终按质量分排序
    result.sort(key=lambda p: p["_quality"], reverse=True)

    stock_count = sum(1 for p in result if p.get("mentioned_stocks"))
    bigv_count = sum(1 for p in result if p.get("followers_count", 0) >= 10000)
    logger.info(
        "[xueqiu_radar] final: crawled %d → unique %d → result %d "
        "(%d with stocks, %d from bigV)",
        len(raw_posts), len(unique), len(result), stock_count, bigv_count,
    )
    return result
