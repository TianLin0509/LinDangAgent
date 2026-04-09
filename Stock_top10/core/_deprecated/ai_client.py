"""AI 客户端 — 多 Provider 统一调用层（合并 client + doubao）"""

import json
import logging
import time as _time
import threading
import requests
from openai import OpenAI, APIConnectionError, AuthenticationError, RateLimitError
from config import MODEL_CONFIGS

_MAX_RETRIES = 3
_RETRY_DELAYS = [2, 4, 8]

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# 全局 Token 计数器（线程安全）
# ══════════════════════════════════════════════════════════════════════════════

_token_lock = threading.Lock()
_token_usage = {"prompt": 0, "completion": 0, "total": 0}


def add_tokens(prompt_tokens: int = 0, completion_tokens: int = 0,
               total_tokens: int = 0, username: str = ""):
    """累加 token 用量（全局计数）"""
    effective_total = total_tokens or (prompt_tokens + completion_tokens)
    with _token_lock:
        _token_usage["prompt"] += prompt_tokens
        _token_usage["completion"] += completion_tokens
        _token_usage["total"] += effective_total


def get_token_usage() -> dict:
    """获取当前累计 token 用量"""
    with _token_lock:
        return dict(_token_usage)


def reset_token_usage():
    """重置 token 计数"""
    with _token_lock:
        _token_usage["prompt"] = 0
        _token_usage["completion"] = 0
        _token_usage["total"] = 0


# ══════════════════════════════════════════════════════════════════════════════
# 豆包 Responses API
# ══════════════════════════════════════════════════════════════════════════════

def _doubao_build_request(cfg, messages, max_tokens, stream=False):
    url = cfg["base_url"].rstrip("/") + "/responses"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['api_key']}",
    }
    body = {
        "model": cfg["model"],
        "input": messages,
        "tools": [{"type": "web_search", "max_keyword": 3}],
        "stream": stream,
        "max_output_tokens": max_tokens,
    }
    return url, headers, body


def _doubao_extract_text(data: dict) -> str:
    parts = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    parts.append(c.get("text", ""))
        if "text" in item:
            parts.append(item["text"])
    if parts:
        return "\n".join(parts)
    if "output_text" in data:
        return data["output_text"]
    return ""


def doubao_call(cfg, messages, max_tokens) -> tuple[str, str | None]:
    """豆包非流式 responses API 调用（含联网搜索）"""
    url, headers, body = _doubao_build_request(cfg, messages, max_tokens, stream=False)
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=180)
        resp.encoding = "utf-8"
        if resp.status_code != 200:
            return "", f"豆包 API 错误 {resp.status_code}：{resp.text[:200]}"
        data = resp.json()
        # 检查 API 级别错误
        if "error" in data:
            err_msg = data["error"].get("message", str(data["error"]))[:200]
            logger.warning("[doubao] API 返回错误: %s", err_msg)
            return "", f"豆包 API 错误：{err_msg}"
        text = _doubao_extract_text(data)
        if not text:
            status = data.get("status", "")
            logger.warning("[doubao] 返回内容为空, status=%s", status)
            return "", f"豆包返回空(status={status})，可能联网搜索并发限流"
        return text, None
    except requests.exceptions.Timeout:
        return "", "豆包 API 请求超时，请稍后重试"
    except Exception as e:
        return "", f"豆包调用异常：{str(e)[:120]}"


# ══════════════════════════════════════════════════════════════════════════════
# AI 客户端
# ══════════════════════════════════════════════════════════════════════════════

def get_ai_client(model_name: str) -> tuple[OpenAI | None, dict | None, str | None]:
    """返回 (client, config, error_msg)。CLI 模式下 client=None。"""
    cfg = MODEL_CONFIGS.get(model_name)
    if not cfg:
        return None, None, "未知模型配置"

    # CLI 模式不需要 OpenAI client
    if cfg.get("provider") in ("gemini_cli", "codex_cli"):
        return None, cfg, None

    if not cfg["api_key"]:
        return None, cfg, f"「{model_name}」的 API Key 尚未配置"
    try:
        extra_kwargs = {}
        if cfg.get("provider") == "openrouter":
            extra_kwargs["default_headers"] = {
                "HTTP-Referer": "https://stock-top10.streamlit.app",
                "X-Title": "Stock Top10 Daily Picks",
            }
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], **extra_kwargs)
        return client, cfg, None
    except Exception as e:
        return None, cfg, str(e)


def _build_messages(prompt: str, system: str = "") -> list[dict]:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def _build_extra(cfg: dict) -> dict:
    """根据 provider 构建联网搜索等额外参数"""
    extra: dict = {}
    if not cfg.get("supports_search"):
        return extra
    provider = cfg.get("provider")
    if provider == "qwen":
        extra["extra_body"] = {"enable_search": True}
    elif provider == "zhipu":
        extra["tools"] = [{"type": "web_search", "web_search": {"enable": True}}]
    elif provider == "openrouter":
        extra["extra_body"] = {"plugins": [{"id": "web", "max_results": 5}]}
    return extra


def call_ai(client: OpenAI | None, cfg: dict, prompt: str,
            system: str = "", max_tokens: int = 8000,
            username: str = "") -> tuple[str, str | None]:
    """
    调用 AI 模型，返回 (text, error_msg)。
    CLI 模式走 cli_providers，豆包走 responses API，其他走 chat.completions。
    """
    # CLI 模式：Gemini CLI / Codex CLI
    if cfg.get("provider") in ("gemini_cli", "codex_cli"):
        try:
            from ai.cli_providers import call_gemini_cli, call_codex_cli
        except ImportError:
            # Stock_top10 独立运行时可能找不到主项目模块
            import sys
            from pathlib import Path
            parent = Path(__file__).resolve().parent.parent.parent
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            from ai.cli_providers import call_gemini_cli, call_codex_cli

        fn = call_gemini_cli if cfg["provider"] == "gemini_cli" else call_codex_cli
        text, err = fn(prompt, system, timeout=300)
        if not err:
            est = int((len(prompt) + len(text)) * 0.7)
            add_tokens(total_tokens=est, username=username)
        return text, err

    messages = _build_messages(prompt, system)

    # 豆包专属路径（带重试，间隔更长以应对联网搜索限流，不回退普通模式）
    if cfg.get("provider") == "doubao" and cfg.get("supports_search"):
        _doubao_delays = [8, 15, 25]
        for attempt in range(_MAX_RETRIES):
            text, err = doubao_call(cfg, messages, max_tokens)
            if not err:
                est = int((len(prompt) + len(text)) * 1.5)
                add_tokens(total_tokens=est, username=username)
                return text, None
            logger.warning("[doubao] 第%d次尝试失败: %s", attempt + 1, err)
            if attempt < _MAX_RETRIES - 1:
                _time.sleep(_doubao_delays[attempt])
        return "", err

    extra = _build_extra(cfg)
    last_err = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=cfg["model"],
                messages=messages,
                max_tokens=max_tokens,
                **extra,
            )
            text = resp.choices[0].message.content or ""

            if hasattr(resp, "usage") and resp.usage:
                add_tokens(
                    prompt_tokens=resp.usage.prompt_tokens or 0,
                    completion_tokens=resp.usage.completion_tokens or 0,
                    total_tokens=resp.usage.total_tokens or 0,
                    username=username,
                )

            return text, None

        except AuthenticationError as e:
            return "", f"API Key 认证失败：{str(e)[:200]}"
        except RateLimitError as e:
            last_err = e
            if attempt < _MAX_RETRIES - 1:
                logger.info("[call_ai] RateLimitError, 重试 %d/%d (等待 %ds)",
                            attempt + 1, _MAX_RETRIES, _RETRY_DELAYS[attempt])
                _time.sleep(_RETRY_DELAYS[attempt])
                continue
            return "", "调用频率或额度超限（已重试3次），请稍后重试或切换其他模型"
        except APIConnectionError as e:
            return "", f"网络连接失败：{e}"
        except Exception as e:
            err = str(e)
            if "invalid_api_key" in err.lower() or "401" in err:
                return "", f"API Key 无效或模型不可用：{err[:200]}"
            if "quota" in err.lower() or "insufficient" in err.lower():
                return "", "账户余额不足，请充值或切换模型"
            if "model_not_found" in err.lower() or "does not exist" in err.lower():
                return "", f"模型不存在（{cfg['model']}），请联系开发者更新模型名称"
            return "", f"AI 调用异常：{err[:120]}"
    return "", f"AI 调用失败（重试耗尽）：{last_err}"
