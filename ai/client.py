"""AI 客户端 — 多 Provider 统一调用层"""

import logging
import time as _time
import threading
from openai import OpenAI, APIConnectionError, AuthenticationError, RateLimitError
from config import MODEL_CONFIGS
from ai.doubao import doubao_call

_MAX_RETRIES = 3
_RETRY_DELAYS = [2, 4, 8]  # 指数退避秒数

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# 全局 Token 计数器（线程安全）
# ══════════════════════════════════════════════════════════════════════════════

_token_lock = threading.Lock()
_token_usage = {"prompt": 0, "completion": 0, "total": 0}


def add_tokens(prompt_tokens: int = 0, completion_tokens: int = 0,
               total_tokens: int = 0, username: str = ""):
    """累加 token 用量（全局 + 用户级持久化）"""
    effective_total = total_tokens or (prompt_tokens + completion_tokens)
    with _token_lock:
        _token_usage["prompt"] += prompt_tokens
        _token_usage["completion"] += completion_tokens
        _token_usage["total"] += effective_total
    if username:
        try:
            from utils.user_store import add_user_tokens
            add_user_tokens(username, prompt_tokens, completion_tokens, effective_total)
        except Exception as e:
            logger.debug("[add_tokens] 用户token持久化失败: %s", e)


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
# AI 客户端
# ══════════════════════════════════════════════════════════════════════════════

def get_ai_client(model_name: str) -> tuple[OpenAI | None, dict | None, str | None]:
    """返回 (client, config, error_msg)"""
    cfg = MODEL_CONFIGS.get(model_name)
    if not cfg:
        return None, None, "未知模型配置"
    if not cfg["api_key"]:
        return None, cfg, f"「{model_name}」的 API Key 尚未配置"
    try:
        extra_kwargs = {}
        if cfg.get("provider") == "openrouter":
            extra_kwargs["default_headers"] = {
                "HTTP-Referer": "https://a-stock-research-assistant.streamlit.app",
                "X-Title": "A-Stock Research Assistant",
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


class StreamResult:
    """流式输出结果容器 — 可迭代，完成后获取 full_text"""

    def __init__(self, gen):
        self._gen = gen
        self.full_text = ""
        self.error = None

    def __iter__(self):
        try:
            for chunk in self._gen:
                self.full_text += chunk
                yield chunk
        except Exception as e:
            self.error = str(e)
            yield f"\n\n⚠️ 流式输出异常：{str(e)[:120]}"


def call_ai_stream(client: OpenAI, cfg: dict, prompt: str,
                   system: str = "", max_tokens: int = 12000,
                   username: str = ""):
    """
    流式调用 AI 模型，yield 文本片段。
    返回 StreamResult 对象（可迭代 + full_text 属性）。
    """
    messages = _build_messages(prompt, system)

    def _generate():
        nonlocal messages

        # 豆包走流式 responses API
        if cfg.get("provider") == "doubao" and cfg.get("supports_search"):
            from ai.doubao import doubao_stream
            full = ""
            for chunk in doubao_stream(cfg, messages, max_tokens):
                full += chunk
                yield chunk
            # 粗估 token
            est = int((len(prompt) + len(full)) * 1.5)
            add_tokens(total_tokens=est, username=username)
            return

        # 其他 provider 走 chat.completions stream
        extra = _build_extra(cfg)
        try:
            stream = client.chat.completions.create(
                model=cfg["model"],
                messages=messages,
                max_tokens=max_tokens,
                stream=True,
                **extra,
            )
            full = ""
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    full += text
                    yield text

            # 流结束后粗估 token
            est_prompt = int(len(prompt) * 1.5)
            est_completion = int(len(full) * 1.5)
            add_tokens(
                prompt_tokens=est_prompt,
                completion_tokens=est_completion,
                total_tokens=est_prompt + est_completion,
                username=username,
            )

        except AuthenticationError as e:
            yield f"\n\n⚠️ API Key 认证失败：{str(e)[:200]}"
        except RateLimitError as e:
            yield f"\n\n⚠️ 调用频率超限，请稍后重试"
        except APIConnectionError as e:
            yield f"\n\n⚠️ 网络连接失败：{e}"
        except Exception as e:
            yield f"\n\n⚠️ AI 流式调用异常：{str(e)[:120]}"

    return StreamResult(_generate())


def call_ai(client: OpenAI, cfg: dict, prompt: str,
            system: str = "", max_tokens: int = 8000,
            username: str = "") -> tuple[str, str | None]:
    """
    调用 AI 模型，返回 (text, error_msg)。
    豆包走 responses API，其他走 chat.completions。
    username 用于 per-user token 持久化。
    """
    messages = _build_messages(prompt, system)

    # 豆包专属路径
    if cfg.get("provider") == "doubao" and cfg.get("supports_search"):
        text, err = doubao_call(cfg, messages, max_tokens)
        if not err:
            # 豆包按字符粗估 token（中文约 1.5 token/字）
            est = int((len(prompt) + len(text)) * 1.5)
            add_tokens(total_tokens=est, username=username)
        return text, err

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

            # 提取 token 用量
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


def call_ai_chat(client: OpenAI, cfg: dict, messages: list[dict],
                 max_tokens: int = 4000,
                 username: str = "") -> tuple[str, str | None]:
    """
    多轮对话调用 — 接受完整 messages list（含 system/user/assistant）。
    复用 call_ai 的重试逻辑和 token 追踪。
    """
    # 豆包走 responses API
    if cfg.get("provider") == "doubao" and cfg.get("supports_search"):
        text, err = doubao_call(cfg, messages, max_tokens)
        if not err:
            est = int(sum(len(m.get("content", "")) for m in messages) * 1.5)
            est += int(len(text) * 1.5)
            add_tokens(total_tokens=est, username=username)
        return text, err

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
                logger.info("[call_ai_chat] RateLimitError, 重试 %d/%d",
                            attempt + 1, _MAX_RETRIES)
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
