"""舆情雷达服务 — 编排爬虫 + LLM 分析 + 缓存"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parent.parent / "storage" / "sentiment_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Unicode escape to avoid Windows encoding issues
_DEFAULT_MODEL = "\U0001f7e3 \u8c46\u5305 \u00b7 Seed 2.0 Pro"  # 🟣 豆包 · Seed 2.0 Pro


def _cache_path() -> Path:
    return _CACHE_DIR / f"{date.today().isoformat()}_radar.json"


def _status_path() -> Path:
    return _CACHE_DIR / f"{date.today().isoformat()}_radar_status.json"


def get_radar_status() -> dict | None:
    fp = _status_path()
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_status(status: dict):
    try:
        _status_path().write_text(
            json.dumps(status, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


def get_latest_radar() -> dict | None:
    """读取最新缓存的舆情雷达结果。"""
    fp = _cache_path()
    if not fp.exists():
        # 尝试找最近的缓存文件
        candidates = sorted(_CACHE_DIR.glob("*_radar.json"), reverse=True)
        if not candidates:
            return None
        fp = candidates[0]
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _build_prompt(posts: list[dict], stock_mentions: dict, knowledge_ctx: str = "") -> str:
    """组装给 LLM 的舆情分析 prompt。

    优先展示带股票标签的帖子，最多取 50 条高质量帖子。
    """
    # 分两组：有股票标签的优先
    with_stocks = [p for p in posts if p.get("mentioned_stocks")]
    without_stocks = [p for p in posts if not p.get("mentioned_stocks")]

    # 有股票的全部展示，无股票的补到 50 条上限
    selected = with_stocks[:35] + without_stocks[: max(0, 50 - len(with_stocks[:35]))]

    lines = []
    for i, p in enumerate(selected, 1):
        fans = p["followers_count"]
        fans_label = f"{fans // 10000}万" if fans >= 10000 else str(fans)
        stocks = ", ".join(p.get("mentioned_stocks", [])[:5]) or "未提及"
        lines.append(
            f"[{i}] {p['user_name']}（{fans_label}粉丝, "
            f"{'认证' if p.get('verified') else '未认证'}）"
            f"| 点赞{p['like_count']} 评论{p['reply_count']}\n"
            f"提及股票: {stocks}\n"
            f"{p['text'][:500]}\n"
        )

    posts_text = "\n---\n".join(lines)

    # 股票提及统计
    top_stocks = stock_mentions.most_common(20)
    mention_text = "、".join(f"{name}({count}次)" for name, count in top_stocks) if top_stocks else "无"

    # 知识库上下文
    knowledge_section = ""
    if knowledge_ctx:
        knowledge_section = f"\n【系统历史参考】\n{knowledge_ctx}\n"

    return f"""以下是雪球社区最近24小时内的高质量投资帖子（共{len(selected)}条，按投资分析价值排序，优先展示提及具体股票的帖子）。

股票提及频率统计：{mention_text}
{knowledge_section}
---帖子内容---
{posts_text}
---帖子结束---

请站在"中线价值投机"操盘手的视角，基于以上帖子内容生成一份实战导向的市场舆情研判。

## 一、市场情绪温度计
给出整体情绪判断（强烈偏多/偏多/中性/偏空/强烈偏空），置信度（高/中/低），并用2-3句话说明核心依据。重点关注大V之间的分歧和共识。

## 二、热议板块与个股（按实战价值排序）
用 Markdown 表格列出当前热议的板块和个股：

| 板块/个股 | 情绪方向 | 讨论热度 | 代表性观点 | 操作启示 |
|---|---|---|---|---|

"操作启示"栏必须落到"是否值得关注、适合左侧还是右侧、当前风险收益比如何"。

## 三、大V核心观点提炼
摘要 3-5 位最有洞察力的大V观点（注明粉丝量级），重点提取：
- 他们看好/看空什么？逻辑是什么？
- 有没有与市场共识相反的"预期差"观点？
- 哪些观点值得中线价值投机者重点跟踪？

## 四、风险与机会扫描
- **潜在机会**：从帖子中提取被低估或刚开始被关注的板块/个股（尤其是基本面+催化共振的）
- **风险预警**：板块退潮信号、资金撤退迹象、一致预期过满的板块
- **事件催化**：近期可能影响市场的关键事件（财报季、政策、外围等）

## 五、一句话总结
用一句冷峻的操盘手口吻概括今日市场舆情。

【输出纪律】
1. 基于帖子内容客观分析，不编造帖子中没有的信息
2. 所有金额用"亿元"或"万元"表示
3. 表格必须用标准 Markdown 语法
4. 结论要服务于交易决策，不要退化成新闻播报"""


SYSTEM_PROMPT = (
    "你是一位深谙A股生态的资深操盘手兼舆情分析师。你的交易流派是冷血高效的'中线价值投机'："
    "以基本面兜底锁定下行风险，以资金面热点与微观筹码博弈博取上行弹性，以技术面确立买卖纪律。"
    "你擅长从社交媒体讨论中提取真正有价值的交易信号，过滤噪音，直击要害。"
    "输出使用 Markdown 格式，语气冷峻客观。"
)


def run_sentiment_radar(model_name: str = "") -> dict:
    """执行完整的舆情雷达流程：爬取 → 分析 → 缓存。"""
    from ai.client import call_ai, get_ai_client, get_token_usage
    from data.xueqiu_radar import fetch_bigv_radar

    model_name = model_name or _DEFAULT_MODEL

    status = {
        "status": "running",
        "started": datetime.now().isoformat(),
        "model": model_name,
    }
    _write_status(status)

    tokens_before = get_token_usage()["total"]

    try:
        # Phase 1: 爬取
        logger.info("[sentiment_radar] Phase 1: 爬取大V帖子...")
        posts = fetch_bigv_radar()

        if len(posts) < 3:
            result = {
                "status": "insufficient",
                "date": date.today().isoformat(),
                "posts_count": len(posts),
                "summary": f"帖子数量不足（仅{len(posts)}条），无法生成有效舆情分析。",
                "report": "",
                "model": model_name,
                "generated_at": datetime.now().isoformat(),
            }
            _cache_path().write_text(
                json.dumps(result, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            status.update({"status": "done", "finished": datetime.now().isoformat()})
            _write_status(status)
            return result

        # Phase 2: 整理
        logger.info("[sentiment_radar] Phase 2: 整理 %d 条帖子...", len(posts))
        stock_mentions: Counter = Counter()
        for p in posts:
            for s in p.get("mentioned_stocks", []):
                stock_mentions[s] += 1

        # Phase 2.5: 获取知识库上下文（市场环境+历史经验）
        knowledge_ctx = ""
        try:
            from knowledge.injector import build_knowledge_context
            knowledge_ctx = build_knowledge_context()
        except Exception:
            pass

        # Phase 3: LLM 分析
        logger.info("[sentiment_radar] Phase 3: LLM 分析...")
        client, cfg, err = get_ai_client(model_name)
        if err:
            raise RuntimeError(f"AI 客户端初始化失败: {err}")

        prompt = _build_prompt(posts, stock_mentions, knowledge_ctx)
        report_text, ai_err = call_ai(
            client, cfg, prompt,
            system=SYSTEM_PROMPT,
            max_tokens=4000,
            username="sentiment_radar",
        )
        if ai_err:
            report_text = f"AI 分析失败：{ai_err}"

        tokens_after = get_token_usage()["total"]
        tokens_used = tokens_after - tokens_before

        # 提取一句话总结（最后一个 ## 之后的内容）
        summary = ""
        if report_text:
            import re
            match = re.search(r"##\s*一句话总结\s*\n(.+?)(?:\n#|\Z)", report_text, re.DOTALL)
            if match:
                summary = match.group(1).strip()
            if not summary:
                summary = report_text[:200]

        # Phase 4: 保存
        logger.info("[sentiment_radar] Phase 4: 保存结果...")
        # 序列化帖子（去掉 created_dt 对象）
        serializable_posts = []
        for p in posts[:30]:
            sp = {k: v for k, v in p.items() if k != "created_dt"}
            serializable_posts.append(sp)

        result = {
            "status": "done",
            "date": date.today().isoformat(),
            "posts_count": len(posts),
            "posts_used": len(serializable_posts),
            "stock_mentions": dict(stock_mentions.most_common(20)),
            "summary": summary,
            "report": report_text,
            "posts": serializable_posts,
            "model": model_name,
            "tokens_used": tokens_used,
            "generated_at": datetime.now().isoformat(),
        }
        _cache_path().write_text(
            json.dumps(result, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        status.update({
            "status": "done",
            "finished": datetime.now().isoformat(),
            "posts_count": len(posts),
            "tokens_used": tokens_used,
        })
        _write_status(status)

        logger.info(
            "[sentiment_radar] 完成！%d 条帖子，%d tokens",
            len(posts), tokens_used,
        )
        return result

    except Exception as exc:
        logger.error("[sentiment_radar] 失败: %s", exc, exc_info=True)
        status.update({
            "status": "error",
            "error": str(exc),
            "finished": datetime.now().isoformat(),
        })
        _write_status(status)
        raise


def build_radar_summary_text(radar: dict | None) -> str:
    """将舆情雷达结果格式化为微信消息文本。"""
    if not radar:
        return "暂时没有可用的舆情雷达结果，可以发送「生成舆情」来获取。"

    date_str = radar.get("date", "")
    summary = radar.get("summary", "")
    posts_count = radar.get("posts_count", 0)
    mentions = radar.get("stock_mentions", {})

    top_mentions = list(mentions.items())[:5]
    mention_text = "、".join(f"{name}({c})" for name, c in top_mentions) if top_mentions else "无"

    text = (
        f"【市场舆情雷达 {date_str}】\n"
        f"分析了 {posts_count} 条大V帖子\n"
        f"热门股票：{mention_text}\n\n"
        f"{summary[:400]}"
    )
    return text


def render_radar_html(radar: dict) -> str:
    """将舆情雷达结果渲染为 HTML 页面。"""
    report = radar.get("report", "")
    date_str = radar.get("date", "")
    posts_count = radar.get("posts_count", 0)
    model = radar.get("model", "")
    generated_at = radar.get("generated_at", "")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>市场舆情雷达 {date_str}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
  .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 12px; margin-bottom: 20px; }}
  .header h1 {{ margin: 0 0 8px 0; font-size: 24px; }}
  .header .meta {{ opacity: 0.85; font-size: 14px; }}
  .report {{ background: white; padding: 24px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); line-height: 1.8; }}
  .report h2 {{ color: #4a5568; border-bottom: 2px solid #667eea; padding-bottom: 6px; margin-top: 24px; }}
  .report ul {{ padding-left: 20px; }}
  .report li {{ margin-bottom: 6px; }}
  .footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 20px; }}
</style>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
</head>
<body>
<div class="header">
  <h1>\U0001f4e1 市场舆情雷达</h1>
  <div class="meta">{date_str} | 分析 {posts_count} 条大V帖子 | 模型: {model} | {generated_at}</div>
</div>
<div class="report" id="report"></div>
<div class="footer">Powered by LinDangAgent \u00b7 \u7acb\u82b1\u9053\u96ea</div>
<script>
  document.getElementById('report').innerHTML = marked.parse({json.dumps(report)});
</script>
</body>
</html>"""
