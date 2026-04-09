# -*- coding: utf-8 -*-
"""单股舆情分析 — 雪球讨论爬取 + LLM 提炼

两个时间窗口：
- 短期（24h）：捕捉突发事件、情绪拐点、当日资金动向
- 中线（2周）：主流叙事演变、大V态度、持续关注度

爬取策略：
1. 雪球股票动态流 API（stock_timeline）
2. 热门帖子优先，最新帖子补充
3. 中线窗口额外提高质量门槛（点赞≥20 或 粉丝≥5000）
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

logger = logging.getLogger(__name__)

# 中线窗口质量门槛（降低要求，确保有足够样本）
_MIDTERM_MIN_LIKES = 10  # 从 20 降到 10
_MIDTERM_MIN_FOLLOWERS = 2000  # 从 5000 降到 2000

# 每个窗口最多送给 LLM 的帖子数
_SHORT_MAX_POSTS = 30
_MID_MAX_POSTS = 40


# ── 数据结构 ─────────────────────────────────────────────────────

@dataclass
class SentimentResult:
    window: str                    # "short" | "mid"
    window_label: str              # "24小时" | "2周"
    stock_code: str
    stock_name: str
    posts_count: int               # 实际采集到的帖子数
    sentiment_label: str           # 强烈看多/偏多/中性/偏空/强烈看空
    confidence: str                # 高/中/低
    bull_points: list[str] = field(default_factory=list)
    bear_points: list[str] = field(default_factory=list)
    key_concerns: list[str] = field(default_factory=list)
    catalysts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    one_liner: str = ""
    # 量化字段（新增）
    bull_ratio: int = -1           # 看多帖子占比 0-100，-1 表示未知
    bear_ratio: int = -1           # 看空帖子占比 0-100，-1 表示未知
    bigv_direction: str = ""       # 大V主导方向：看多/看空/分歧/无大V
    raw_analysis: str = ""         # LLM 原始输出，供调试
    error: str = ""                # 非空表示该窗口失败


@dataclass
class StockSentimentBundle:
    stock_code: str
    stock_name: str
    short_term: SentimentResult | None = None   # 24h
    mid_term: SentimentResult | None = None     # 2周
    fetched_at: str = ""
    em_hot_signal: str = ""   # 东方财富热度补充信号（文本摘要）


# ── 雪球 symbol 转换 ─────────────────────────────────────────────

def _to_xueqiu_symbol(ts_code: str) -> str:
    """000001.SZ → SZ000001，600519.SH → SH600519"""
    ts_code = ts_code.strip()
    if "." in ts_code:
        code, market = ts_code.split(".", 1)
        return f"{market.upper()}{code}"
    # 纯6位数字，猜市场
    if ts_code.startswith("6"):
        return f"SH{ts_code}"
    return f"SZ{ts_code}"


# ── 帖子质量评分（复用 xueqiu_radar 逻辑，针对单股调整） ──────────

_INVEST_KEYWORDS = re.compile(
    r"(涨|跌|仓|买入|卖出|持有|估值|市盈|PE|PB|ROE|营收|净利|分红|回购|"
    r"板块|赛道|龙头|题材|催化|预期|利好|利空|财报|业绩|增长|亏损|扭亏|反转|"
    r"均线|支撑|压力|突破|止损|止盈|趋势|量价|资金|北向|融资|主力|"
    r"牛市|熊市|震荡|调整|反弹|回调|抄底|追高|割肉|套牢|解套)"
)


def _quality_score(post: dict) -> float:
    score = 0.0
    text = post.get("text", "")

    # 投资关键词密度
    hits = len(_INVEST_KEYWORDS.findall(text))
    score += min(hits * 5, 50)

    # 正文深度
    if len(text) >= 500:
        score += 25
    elif len(text) >= 200:
        score += 5 + (len(text) - 200) / 30

    # 互动热度
    score += min(post.get("like_count", 0) * 0.5, 20)
    score += min(post.get("reply_count", 0) * 1.0, 15)

    # 大V加成（基础粉丝数）
    followers = post.get("followers_count", 0)
    if followers >= 100000:
        score += 20
    elif followers >= 10000:
        score += 10
    elif followers >= 5000:
        score += 5

    # 投资认证大V额外加成
    verified = post.get("verified", False)
    verified_desc = post.get("verified_description", "") or ""
    if verified and any(kw in verified_desc for kw in ("投资", "基金", "分析师", "研究", "资管", "私募", "公募")):
        score += 30
    elif followers >= 100000 and len(_INVEST_KEYWORDS.findall(text)) >= 5:
        score += 15

    # 纯回复引用扣分
    if text.startswith("回复@") or text.startswith("//@"):
        score -= 15

    return score


def _is_valid_post(post: dict, cutoff: datetime, midterm: bool = False) -> bool:
    """基础有效性过滤。"""
    # 时间窗口
    created_dt = post.get("created_dt")
    if created_dt and created_dt < cutoff:
        return False

    text = post.get("text", "")

    # 太短且无投资价值
    if len(text) < 30:
        return False

    # 中线窗口额外质量门槛
    if midterm:
        likes = post.get("like_count", 0)
        followers = post.get("followers_count", 0)
        if likes < _MIDTERM_MIN_LIKES and followers < _MIDTERM_MIN_FOLLOWERS:
            return False

    return True


# ── 东方财富股吧爬取（独立 Selenium driver，不复用雪球的）────────

def _fetch_guba_posts(ts_code: str, stock_name: str, hours: int) -> list[dict]:
    """用 requests 直接爬取东方财富股吧帖子列表（无需 Selenium）。"""
    import requests as _req
    from data.tushare_client import to_code6
    from html import unescape

    code6 = to_code6(ts_code)
    cutoff = datetime.now(tz=timezone(timedelta(hours=8))) - timedelta(hours=hours)
    results: list[dict] = []

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://guba.eastmoney.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    for page in range(1, 6):  # 最多5页，覆盖更长时间窗口
        url = f"https://guba.eastmoney.com/list,{code6},{page}.html"
        try:
            r = _req.get(url, headers=headers, timeout=10)
            if r.status_code != 200:
                break
            src = r.text
        except Exception as exc:
            logger.debug("[stock_sentiment] guba requests failed p%d: %s", page, exc)
            break

        items = re.findall(r'<tr class="listitem[^"]*">(.*?)</tr>', src, re.DOTALL)
        if not items:
            break

        page_has_new = False
        for item in items:
            title_m = re.search(r'data-cntitle="([^"]+)"', item)
            if not title_m:
                # 尝试从 title 属性取
                title_m = re.search(r'title="([^"]+)"', item)
            if not title_m:
                continue
            title = unescape(title_m.group(1)).strip()
            if not title or len(title) < 4:
                continue

            href_m = re.search(r'href="(/news,[^"]+)"', item)
            post_id = href_m.group(1) if href_m else title[:20]

            time_m = re.search(r'class="update[^"]*">([^<]+)</div>', item)
            created_dt = None
            if time_m:
                raw_time = time_m.group(1).strip()
                try:
                    now = datetime.now(tz=timezone(timedelta(hours=8)))
                    if len(raw_time) <= 11:
                        raw_time = f"{now.year}-{raw_time}"
                    created_dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M").replace(
                        tzinfo=timezone(timedelta(hours=8))
                    )
                except Exception:
                    pass

            if created_dt and created_dt < cutoff:
                continue

            page_has_new = True

            read_m = re.search(r'class="read">(\d+)</div>', item)
            read_count = int(read_m.group(1)) if read_m else 0

            reply_m = re.search(r'class="reply">(\d+)</div>', item)
            reply_count = int(reply_m.group(1)) if reply_m else 0

            author_m = re.search(r'class="nametext[^"]*"[^>]*>([^<]+)</a>', item)
            author = unescape(author_m.group(1).strip()) if author_m else ""

            results.append({
                "id": f"em_{post_id}",
                "url": f"https://guba.eastmoney.com{post_id}" if post_id.startswith("/") else "",
                "user_name": author,
                "user_id": "",
                "followers_count": 0,
                "verified": False,
                "verified_description": "",
                "text": title,
                "created_at": created_dt.isoformat() if created_dt else "",
                "created_dt": created_dt,
                "like_count": read_count // 100,
                "reply_count": reply_count,
                "retweet_count": 0,
                "mentioned_stocks": [stock_name],
                "_source": "eastmoney",
            })

        if not page_has_new:
            break
        time.sleep(0.5)

    logger.info("[stock_sentiment] %s 东财股吧: %d 条", stock_name, len(results))
    return results


def _fetch_em_hot_signal(ts_code: str) -> str:
    """从东方财富获取热度补充信号（热词 + 投资意愿），返回文本摘要。

    不依赖帖子文本，而是用量化热度指标作为雪球舆情的补充验证。
    失败时静默返回空字符串。
    """
    from data.tushare_client import to_code6
    code6 = to_code6(ts_code)
    # 东方财富热度接口用 SH/SZ 前缀格式
    market = "SH" if ts_code.endswith(".SH") else "SZ"
    em_symbol = f"{market}{code6}"

    parts = []
    try:
        import akshare as ak
        # 热词排名（反映当前市场关注的概念）
        df_kw = ak.stock_hot_keyword_em(symbol=em_symbol)
        if df_kw is not None and not df_kw.empty and "板块名称" in df_kw.columns:
            keywords = df_kw["板块名称"].dropna().head(3).tolist()
            if keywords:
                parts.append(f"东财热词：{'、'.join(keywords)}")
    except Exception:
        pass

    try:
        import akshare as ak
        # 投资意愿（近5日均值，反映散户情绪趋势）
        df_desire = ak.stock_comment_detail_scrd_desire_em(symbol=code6)
        if df_desire is not None and not df_desire.empty:
            col = [c for c in df_desire.columns if "意愿" in str(c)]
            if col:
                latest = float(df_desire[col[0]].iloc[-1])
                avg5 = float(df_desire[col[0]].tail(5).mean())
                trend = "上升" if latest > avg5 else "下降"
                parts.append(f"东财投资意愿：{latest:.1f}（5日均{avg5:.1f}，趋势{trend}）")
    except Exception:
        pass

    return "；".join(parts) if parts else ""


# ── 雪球 API 爬取 ────────────────────────────────────────────────

def _fetch_by_search(
    query: str,
    max_pages: int = 5,
) -> list[dict]:
    """通过雪球搜索 API 爬取提及该股票的帖子。

    使用 /query/v1/search/status.json，按时间倒序翻页。
    """
    from data.xueqiu_radar import _api_get, _parse_post

    results: list[dict] = []
    for page in range(1, max_pages + 1):
        data = _api_get(
            "https://xueqiu.com/query/v1/search/status.json",
            {"q": query, "count": 20, "page": page},
        )
        if not data:
            break

        items = data.get("list") or []
        if not items:
            break

        for raw in items:
            post = _parse_post(raw)
            if post:
                results.append(post)

        time.sleep(0.8)

    return results


def _fetch_stock_posts(
    ts_code: str,
    stock_name: str,
    hours: int,
    midterm: bool = False,
    status_cb: Callable[[str], None] | None = None,
) -> list[dict]:
    """采集单股帖子：雪球两路 + 东方财富股吧，合并去重后按质量排序。"""
    import threading
    symbol = _to_xueqiu_symbol(ts_code)
    cutoff = datetime.now(tz=timezone(timedelta(hours=8))) - timedelta(hours=hours)

    if status_cb:
        status_cb(f"搜索 {stock_name} {'中线' if midterm else '短期'}舆情...")

    # 雪球两路 + 东方财富股吧并行采集
    max_pages = 5 if midterm else 3
    _xq_name, _xq_sym, _em = [], [], []

    def _fetch_xq_name():
        _xq_name.extend(_fetch_by_search(stock_name, max_pages=max_pages))

    def _fetch_xq_sym():
        _xq_sym.extend(_fetch_by_search(symbol, max_pages=max_pages))

    def _fetch_em():
        _em.extend(_fetch_guba_posts(ts_code, stock_name, hours=hours))

    threads = [
        threading.Thread(target=_fetch_xq_name, daemon=True),
        threading.Thread(target=_fetch_xq_sym, daemon=True),
        threading.Thread(target=_fetch_em, daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    # 合并去重
    seen: set[str] = set()
    all_posts: list[dict] = []
    for p in _xq_name + _xq_sym + _em:
        pid = p.get("id", "")
        if pid and pid not in seen:
            seen.add(pid)
            all_posts.append(p)

    # 时间 + 质量过滤
    filtered = [p for p in all_posts if _is_valid_post(p, cutoff, midterm=midterm)]

    # 质量评分 + 排序
    for p in filtered:
        p["_quality"] = _quality_score(p)
    filtered.sort(key=lambda p: p["_quality"], reverse=True)

    max_posts = _MID_MAX_POSTS if midterm else _SHORT_MAX_POSTS
    result = filtered[:max_posts]

    logger.info(
        "[stock_sentiment] %s %s: xq=%d em=%d filtered=%d selected=%d",
        stock_name, "mid" if midterm else "short",
        len(_xq_name) + len(_xq_sym), len(_em), len(filtered), len(result),
    )
    return result


# ── LLM 提炼 ────────────────────────────────────────────────────

def _build_sentiment_prompt(
    stock_name: str,
    stock_code: str,
    window_label: str,
    posts: list[dict],
) -> str:
    lines = []
    for i, p in enumerate(posts, 1):
        fans = p.get("followers_count", 0)
        fans_label = f"{fans // 10000}万粉" if fans >= 10000 else f"{fans}粉"
        lines.append(
            f"[{i}] {p.get('user_name', '?')}（{fans_label}）"
            f"点赞{p.get('like_count', 0)} 评论{p.get('reply_count', 0)}\n"
            f"{p.get('text', '')[:400]}"
        )

    posts_text = "\n---\n".join(lines)

    return f"""以下是雪球社区关于【{stock_name}】（{stock_code}）最近{window_label}的讨论帖子（共{len(posts)}条，已按质量排序）。

{posts_text}

请站在"中线价值投机"操盘手视角，提炼该股舆情要点。严格按以下格式输出，每行一项，不要多余说明：

情绪方向: [强烈看多/偏多/中性/偏空/强烈看空]
置信度: [高/中/低]
看多帖子占比: [0-100的整数，估算看多倾向帖子的百分比]
看空帖子占比: [0-100的整数，估算看空倾向帖子的百分比]
大V主导方向: [看多/看空/分歧/无大V，基于粉丝≥1万的用户观点]
看多逻辑1: [一句话，无则填"无"]
看多逻辑2: [一句话，无则填"无"]
看多逻辑3: [一句话，无则填"无"]
看空逻辑1: [一句话，无则填"无"]
看空逻辑2: [一句话，无则填"无"]
看空逻辑3: [一句话，无则填"无"]
关键争议1: [一句话，无则填"无"]
关键争议2: [一句话，无则填"无"]
催化预期1: [一句话，无则填"无"]
催化预期2: [一句话，无则填"无"]
风险提示1: [一句话，无则填"无"]
风险提示2: [一句话，无则填"无"]
一句话总结: [操盘手口吻，20字以内]

【纪律】
1. 只基于帖子内容，不编造
2. 帖子数量不足5条时，情绪方向填"中性"，置信度填"低"，占比均填0，其余填"无"
3. 不要输出任何额外内容，严格按格式
"""


_SENTIMENT_SYSTEM = (
    "你是一位深谙A股生态的资深操盘手兼舆情分析师，擅长从社交媒体讨论中提取真正有价值的交易信号，"
    "过滤噪音，直击要害。输出严格按指定格式，语气冷峻客观。"
)


def _parse_sentiment_response(text: str, window: str, stock_code: str, stock_name: str, posts_count: int) -> SentimentResult:
    """解析 LLM 结构化输出。"""
    def _get(key: str) -> str:
        m = re.search(rf"^{re.escape(key)}[:：]\s*(.+)$", text, re.MULTILINE)
        val = m.group(1).strip() if m else ""
        return "" if val in ("无", "[无]", "无。") else val

    def _get_list(prefix: str) -> list[str]:
        items = []
        for i in range(1, 4):
            v = _get(f"{prefix}{i}")
            if v:
                items.append(v)
        return items

    def _get_ratio(key: str) -> int:
        raw = _get(key)
        m = re.search(r"(\d+)", raw)
        if m:
            return min(100, max(0, int(m.group(1))))
        return -1

    return SentimentResult(
        window=window,
        window_label="24小时" if window == "short" else "2周",
        stock_code=stock_code,
        stock_name=stock_name,
        posts_count=posts_count,
        sentiment_label=_get("情绪方向") or "中性",
        confidence=_get("置信度") or "低",
        bull_ratio=_get_ratio("看多帖子占比"),
        bear_ratio=_get_ratio("看空帖子占比"),
        bigv_direction=_get("大V主导方向") or "无大V",
        bull_points=_get_list("看多逻辑"),
        bear_points=_get_list("看空逻辑"),
        key_concerns=_get_list("关键争议"),
        catalysts=_get_list("催化预期"),
        risks=_get_list("风险提示"),
        one_liner=_get("一句话总结"),
        raw_analysis=text,
    )


def _call_sentiment_model(prompt: str, model_name: str) -> tuple[str, str]:
    """调用 AI 模型做舆情分析，支持 API 和 CLI 模型，带降级链。

    返回 (response_text, error_msg)。
    降级顺序：指定模型 → Gemini CLI → DeepSeek → Codex CLI
    """
    from ai.client import call_ai, call_ai_stream, get_ai_client

    candidates = [model_name]
    # 降级链（去重）
    for fallback in ["🔮 Gemini CLI（免费）", "⚫ DeepSeek", "🤖 Codex CLI（Plus）"]:
        if fallback not in candidates:
            candidates.append(fallback)

    last_err = ""
    for model in candidates:
        try:
            client, cfg, err = get_ai_client(model)
            if err or not client:
                last_err = err or "client unavailable"
                continue

            # CLI 模型走 call_ai_stream
            if cfg.get("provider") in ("gemini_cli", "codex_cli", "claude_cli"):
                stream = call_ai_stream(
                    client, cfg, prompt,
                    system=_SENTIMENT_SYSTEM,
                    max_tokens=800,
                )
                for _ in stream:
                    pass
                text = stream.full_text
                if text and text.strip():
                    return text, ""
                last_err = f"{model} 返回空内容"
            else:
                text, ai_err = call_ai(
                    client, cfg, prompt,
                    system=_SENTIMENT_SYSTEM,
                    max_tokens=800,
                    username="stock_sentiment",
                )
                if ai_err:
                    last_err = f"{model}: {ai_err}"
                    continue
                if text and text.strip():
                    return text, ""
                last_err = f"{model} 返回空内容"
        except Exception as exc:
            last_err = f"{model}: {exc}"
            continue

    return "", f"所有模型均失败: {last_err}"


def _analyze_posts(
    posts: list[dict],
    stock_name: str,
    ts_code: str,
    window: str,
    window_label: str,
    model_name: str,
) -> SentimentResult:
    """调用 LLM 提炼帖子观点，支持 API/CLI 模型，带降级链。"""
    if len(posts) < 3:
        return SentimentResult(
            window=window,
            window_label=window_label,
            stock_code=ts_code,
            stock_name=stock_name,
            posts_count=len(posts),
            sentiment_label="中性",
            confidence="低",
            one_liner="讨论帖子不足，无法判断",
            error="posts_insufficient",
        )

    prompt = _build_sentiment_prompt(stock_name, ts_code, window_label, posts)
    response, ai_err = _call_sentiment_model(prompt, model_name)

    if ai_err or not response:
        return SentimentResult(
            window=window,
            window_label=window_label,
            stock_code=ts_code,
            stock_name=stock_name,
            posts_count=len(posts),
            sentiment_label="中性",
            confidence="低",
            error=f"LLM分析失败: {ai_err}",
        )

    return _parse_sentiment_response(response, window, ts_code, stock_name, len(posts))


# ── 主入口 ───────────────────────────────────────────────────────

# 轻量模型即可，不需要大模型
_DEFAULT_MODEL = "⚫ DeepSeek"


def fetch_stock_sentiment(
    ts_code: str,
    stock_name: str,
    model_name: str = _DEFAULT_MODEL,
    status_cb: Callable[[str], None] | None = None,
) -> StockSentimentBundle:
    """采集并分析单股舆情，返回短期+中线两个窗口的结果。

    设计为在独立线程中运行，不抛出异常（内部捕获并记录到 error 字段）。
    """
    bundle = StockSentimentBundle(
        stock_code=ts_code,
        stock_name=stock_name,
        fetched_at=datetime.now().isoformat(timespec="seconds"),
    )

    # ── 短期（24h）──────────────────────────────────────────────
    try:
        if status_cb:
            status_cb(f"采集 {stock_name} 短期舆情（24h）...")
        short_posts = _fetch_stock_posts(ts_code, stock_name, hours=24, midterm=False, status_cb=status_cb)
        bundle.short_term = _analyze_posts(
            short_posts, stock_name, ts_code,
            window="short", window_label="24小时",
            model_name=model_name,
        )
        logger.info(
            "[stock_sentiment] %s short: %d posts → %s(%s)",
            stock_name, len(short_posts),
            bundle.short_term.sentiment_label, bundle.short_term.confidence,
        )
    except Exception as exc:
        logger.warning("[stock_sentiment] short term failed for %s: %s", stock_name, exc)
        bundle.short_term = SentimentResult(
            window="short", window_label="24小时",
            stock_code=ts_code, stock_name=stock_name,
            posts_count=0, sentiment_label="中性", confidence="低",
            error=str(exc),
        )

    # ── 中线（2周）──────────────────────────────────────────────
    try:
        if status_cb:
            status_cb(f"采集 {stock_name} 中线舆情（2周）...")
        mid_posts = _fetch_stock_posts(ts_code, stock_name, hours=336, midterm=True, status_cb=status_cb)
        bundle.mid_term = _analyze_posts(
            mid_posts, stock_name, ts_code,
            window="mid", window_label="2周",
            model_name=model_name,
        )
        logger.info(
            "[stock_sentiment] %s mid: %d posts → %s(%s)",
            stock_name, len(mid_posts),
            bundle.mid_term.sentiment_label, bundle.mid_term.confidence,
        )
    except Exception as exc:
        logger.warning("[stock_sentiment] mid term failed for %s: %s", stock_name, exc)
        bundle.mid_term = SentimentResult(
            window="mid", window_label="2周",
            stock_code=ts_code, stock_name=stock_name,
            posts_count=0, sentiment_label="中性", confidence="低",
            error=str(exc),
        )

    # ── 东方财富热度补充信号 ─────────────────────────────────────
    try:
        bundle.em_hot_signal = _fetch_em_hot_signal(ts_code)
        if bundle.em_hot_signal:
            logger.info("[stock_sentiment] %s 东财热度: %s", stock_name, bundle.em_hot_signal)
    except Exception:
        pass

    return bundle


def format_sentiment_for_prompt(bundle: StockSentimentBundle) -> str:
    """将舆情结果格式化为注入 prompt 的文本段落（≤700字）。"""
    if not bundle:
        return ""

    parts = ["【雪球舆情参考】"]

    def _fmt_result(r: SentimentResult) -> list[str]:
        if r is None:
            return []
        if r.error == "posts_insufficient":
            return [f"  {r.window_label}：讨论不足，无法判断"]
        if r.error:
            return []

        # 量化数字行
        quant_parts = [f"{r.window_label}（{r.posts_count}条）：{r.sentiment_label}，置信度{r.confidence}"]
        if r.bull_ratio >= 0 and r.bear_ratio >= 0:
            quant_parts.append(f"看多{r.bull_ratio}%/看空{r.bear_ratio}%")
        if r.bigv_direction and r.bigv_direction not in ("无大V", ""):
            quant_parts.append(f"大V方向:{r.bigv_direction}")
        lines = ["  " + " | ".join(quant_parts)]

        if r.bull_points:
            lines.append(f"  看多：{'；'.join(r.bull_points[:2])}")
        if r.bear_points:
            lines.append(f"  看空：{'；'.join(r.bear_points[:2])}")
        if r.key_concerns:
            lines.append(f"  争议：{'；'.join(r.key_concerns[:1])}")
        if r.catalysts:
            lines.append(f"  催化：{'；'.join(r.catalysts[:2])}")
        if r.risks:
            lines.append(f"  风险：{'；'.join(r.risks[:1])}")
        if r.one_liner:
            lines.append(f"  小结：{r.one_liner}")
        return lines

    if bundle.short_term:
        parts.extend(_fmt_result(bundle.short_term))
    if bundle.mid_term:
        parts.extend(_fmt_result(bundle.mid_term))

    # 东方财富热度补充信号
    if bundle.em_hot_signal:
        parts.append(f"  📊 {bundle.em_hot_signal}")

    # 短中期背离检测
    divergence = _detect_sentiment_divergence(bundle.short_term, bundle.mid_term)
    if divergence:
        parts.append(f"  ⚡ 背离预警：{divergence}")

    if len(parts) <= 1:
        return ""

    parts.append("⚠️ 舆情仅反映社区讨论，不构成操作依据")
    return "\n".join(parts)


# 情绪方向的多空极性映射
_SENTIMENT_POLARITY = {
    "强烈看多": 2, "偏多": 1, "中性": 0, "偏空": -1, "强烈看空": -2,
}


def _detect_sentiment_divergence(
    short: SentimentResult | None,
    mid: SentimentResult | None,
) -> str:
    """检测短期和中线情绪是否背离，返回描述字符串，无背离返回空字符串。"""
    if not short or not mid:
        return ""
    if short.error or mid.error:
        return ""
    if short.confidence == "低" and mid.confidence == "低":
        return ""

    sp = _SENTIMENT_POLARITY.get(short.sentiment_label, 0)
    mp = _SENTIMENT_POLARITY.get(mid.sentiment_label, 0)

    # 方向背离
    if sp > 0 and mp < 0:
        return (f"短期{short.sentiment_label}但中线{mid.sentiment_label}，"
                f"警惕短期情绪过热、中线趋势未确立")
    if sp < 0 and mp > 0:
        return (f"短期{short.sentiment_label}但中线{mid.sentiment_label}，"
                f"可能是中线上升途中的短期回调情绪，关注是否为左侧机会")
    if abs(sp - mp) >= 2:
        return (f"短期{short.sentiment_label}与中线{mid.sentiment_label}分歧显著，"
                f"需结合资金面判断主导方向")

    # 强度背离（量化字段）
    if short.bull_ratio >= 0 and mid.bull_ratio >= 0:
        if short.bull_ratio > 70 and mid.bull_ratio < 40:
            return f"短期看多情绪过热({short.bull_ratio}%)但中线支撑不足({mid.bull_ratio}%)，警惕追高风险"
        if short.bear_ratio > 70 and mid.bear_ratio < 40:
            return f"短期恐慌情绪浓厚({short.bear_ratio}%)但中线分歧不大({mid.bear_ratio}%)，可能是短期超跌机会"

    # 大V与散户分歧
    if (short.bigv_direction and mid.bigv_direction
            and short.bigv_direction not in ("无大V", "分歧", "")
            and mid.bigv_direction not in ("无大V", "分歧", "")):
        if short.bigv_direction != mid.bigv_direction:
            return f"大V短期{short.bigv_direction}但中线{mid.bigv_direction}，主力意图存在分歧"

    return ""
