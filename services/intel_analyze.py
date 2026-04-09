# -*- coding: utf-8 -*-
"""情报分析 — 抓取 URL 文章内容，AI 深度分析提炼关键思想

用法：
    python cli.py intel-analyze <url> [model]

流程：
  1. 抓取 URL 页面内容（微信公众号等特殊处理）
  2. 提取可读正文（去 HTML 标签/脚本/广告）
  3. 送 AI 模型进行情报分析
  4. 返回结构化分析结果
"""

import logging
import re
import uuid
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# ── 浏览器伪装 ─────────────────────────────────────────────────────

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_PROXY = "http://127.0.0.1:7890"

# 国内域名不走代理
_CN_DOMAINS = (
    "weixin.qq.com", "mp.weixin.qq.com", "qq.com",
    "sohu.com", "sina.com.cn", "163.com", "36kr.com",
    "eastmoney.com", "toutiao.com", "zhihu.com",
    "baidu.com", "jiemian.com", "caixin.com",
    "thepaper.cn", "yicai.com", "cls.cn",
)

# ── Prompt 模板 ────────────────────────────────────────────────────

INTEL_SYSTEM = """你是一位资深情报分析师（Intelligence Analyst）。你的任务是对给定的文章进行深度情报分析，提炼核心信息和可操作的洞察。

【分析框架】
1. 核心摘要（3-5句话概括文章主旨）
2. 关键信息点（逐条列出文章中的重要事实和数据）
3. 深层含义（文章背后未明说的信号、趋势、立场）
4. 利益相关方分析（涉及哪些主体，各自立场和动机）
5. 对A股市场的影响（如适用：受益/受损行业和标的）
6. 可操作建议（基于以上分析，读者应该关注什么、做什么）
7. 信息可信度评估（信源质量、是否有偏见、需要交叉验证的点）

【输出要求】
- 使用 Markdown 格式
- 每个部分用单个 ## 标题分隔（注意：只用一个 ##，不要写成 ## ##）
- 关键结论用 **加粗** 标注
- 如果文章涉及A股，务必给出具体标的分析
- 严格区分"原文明确提到的信息"和"你自行推理/联网搜索补充的信息"，推理补充的内容必须标注【AI补充】前缀
- 语言简洁犀利，不要空泛的套话"""

INTEL_PROMPT_TEMPLATE = """请对以下文章进行情报分析。

文章来源：{url}

--- 文章内容 ---
{content}
--- 文章结束 ---

请按照情报分析框架逐项分析。"""

INTEL_PROMPT_DIRECT = """请访问以下网址，阅读文章内容，然后按照情报分析框架进行深度分析。

网址：{url}

如果无法直接访问，请搜索该网址相关内容进行分析。

请按照情报分析框架逐项分析。"""

MAX_CONTENT_CHARS = 15000


# ── HTML 文本提取 ──────────────────────────────────────────────────

class _ReadableTextExtractor(HTMLParser):
    """用 stdlib HTMLParser 提取可见文本，跳过脚本/样式等。"""

    _SKIP_TAGS = frozenset({
        "script", "style", "nav", "header", "footer",
        "aside", "noscript", "iframe", "svg",
    })

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._pieces: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._pieces.append(text)

    def get_text(self) -> str:
        return "\n".join(self._pieces)


def _extract_title(html: str) -> str:
    """从 HTML 中提取标题。"""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if m:
        return unescape(m.group(1).strip())
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL | re.IGNORECASE)
    if m:
        return unescape(re.sub(r"<[^>]+>", "", m.group(1)).strip())
    return ""


def _extract_readable_text(html: str, url: str = "") -> tuple[str, str]:
    """从 HTML 提取可读正文，返回 (text, title)。"""
    title = _extract_title(html)

    # 微信公众号：从 js_content div 提取
    if "mp.weixin.qq.com" in url or "js_content" in html:
        m = re.search(
            r'id="js_content"[^>]*>(.*?)</div>\s*(?:<script|<div[^>]*class="ct_mpda_wrp")',
            html, re.DOTALL,
        )
        if m:
            html = m.group(1)

    parser = _ReadableTextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    text = parser.get_text()

    # 压缩多余空白
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    # 截断
    if len(text) > MAX_CONTENT_CHARS:
        text = text[:MAX_CONTENT_CHARS] + "\n\n[...内容已截断...]"

    return text.strip(), title


# ── URL 内容抓取 ───────────────────────────────────────────────────

def _is_cn_domain(url: str) -> bool:
    """判断是否为国内域名。"""
    host = urlparse(url).hostname or ""
    return any(host.endswith(d) for d in _CN_DOMAINS)


def fetch_article_content(url: str) -> tuple[str, str, str | None]:
    """抓取 URL 文章内容。返回 (text, title, error_msg)。"""
    proxies = None if _is_cn_domain(url) else {"http": _PROXY, "https": _PROXY}

    try:
        resp = requests.get(
            url,
            headers=_BROWSER_HEADERS,
            timeout=30,
            allow_redirects=True,
            proxies=proxies,
        )
        resp.raise_for_status()
        # 自动检测编码
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text
    except requests.RequestException as e:
        logger.warning("抓取 URL 失败: %s — %s", url, e)
        return "", "", str(e)

    text, title = _extract_readable_text(html, url)
    if len(text) < 200:
        return text, title, "内容过短，可能需要 AI 联网抓取"

    return text, title, None


# ── AI 模型调用 ────────────────────────────────────────────────────

_MODEL_ALIASES = {
    "gemini": "🔮 Gemini CLI（免费）",
    "codex": "🤖 Codex CLI（Plus）",
    "opus": "🧠 Claude Opus（MAX）",
    "sonnet": "⚡ Claude Sonnet（MAX）",
}


def _resolve_model(model_override: str | None) -> str:
    """解析模型名，支持短别名。"""
    if model_override:
        return _MODEL_ALIASES.get(model_override.lower(), model_override)
    from config import get_active_model
    return get_active_model()


def _call_model(prompt: str, system: str, model_name: str) -> str:
    """调用单个模型（复用 event_recon 的调用模式）。"""
    from ai.client import call_ai, call_ai_stream, get_ai_client

    client, cfg, err = get_ai_client(model_name)
    if err and not cfg:
        return f"⚠️ 模型不可用：{err}"

    if cfg.get("provider") in ("gemini_cli", "codex_cli", "claude_cli"):
        stream = call_ai_stream(client, cfg, prompt, system=system, max_tokens=12000)
        for _ in stream:
            pass
        return stream.full_text

    text, call_err = call_ai(client, cfg, prompt, system=system, max_tokens=12000)
    if call_err:
        return f"⚠️ 调用失败：{call_err}"
    return text


# ── 主入口 ─────────────────────────────────────────────────────────

def run_intel_analyze(url: str, model_name: str = "") -> dict:
    """情报分析主流程。返回结果 dict。"""
    resolved_model = _resolve_model(model_name or None)
    report_id = str(uuid.uuid4())[:8]

    # 1. 抓取文章内容
    text, title, fetch_err = fetch_article_content(url)

    # 2. 构建 prompt
    if text and len(text) >= 200:
        prompt = INTEL_PROMPT_TEMPLATE.format(url=url, content=text)
    else:
        # 内容不足，让 AI 自行联网获取
        logger.info("内容抓取不足(%d字符)，回退到 AI 联网模式", len(text))
        prompt = INTEL_PROMPT_DIRECT.format(url=url)
        # 优先用有联网能力的 Gemini
        if not model_name:
            resolved_model = _MODEL_ALIASES["gemini"]

    # 3. 调用 AI 分析
    analysis = _call_model(prompt, INTEL_SYSTEM, resolved_model)

    # 后处理：清理模型输出中的重复 ## 标记（如 "## ## 1." → "## 1."）
    analysis = re.sub(r"^(#{1,3})\s*\1", r"\1", analysis, flags=re.MULTILINE)

    if not analysis or analysis.startswith("⚠️"):
        return {
            "status": "error",
            "message": analysis or "AI 分析无返回",
            "url": url,
        }

    result = {
        "status": "ok",
        "report_id": report_id,
        "url": url,
        "title": title or "情报分析",
        "model": resolved_model,
        "content_length": len(text),
        "fetch_error": fetch_err,
        "analysis": analysis,
    }

    # 持久化到情报知识库
    try:
        from knowledge.intel_memory import store_intel
        intel_id = store_intel(
            url=url,
            title=title or "情报分析",
            model=resolved_model,
            analysis=analysis,
        )
        result["intel_id"] = intel_id
        logger.info("情报已存入知识库: %s", intel_id)
    except Exception as e:
        logger.warning("情报持久化失败: %s", e)

    return result
